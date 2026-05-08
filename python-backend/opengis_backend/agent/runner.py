"""
AgentRunner — drives any Loop (AgentLoop or WorkflowLoop) in a worker
thread, yields AgentEvents.

The loop blocks synchronously (LLM calls + subprocess execution).
This module wraps it into an ``async`` generator yielding
:class:`AgentEvent` so the rest of the codebase (RPC handler, CLI,
tests) only has to ``async for event in runner.drive(...)``.

v3.1 (2026-04): Rewritten for the custom AgentLoop. No longer depends
on smolagents' RunResult or step callbacks — the AgentLoop itself
invokes step callbacks synchronously, and we translate AgentStep objects
into AgentEvents.

v3.2 (2026-04): Made generic — now drives any object with a
``run(user_message: str) -> str`` method. This allows the same Runner
to drive both AgentLoop (free-form CodeAct) and WorkflowLoop (DAG-driven).
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Optional, Protocol, runtime_checkable

from opengis_backend.agent.events import AgentEvent, AgentEventType, _enqueue

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Protocol for any driveable loop
# ──────────────────────────────────────────────────────────────────────

@runtime_checkable
class RunnableLoop(Protocol):
    """Protocol for any loop that the AgentRunner can drive.

    Both AgentLoop and WorkflowLoop satisfy this protocol.
    """

    def run(self, user_message: str) -> str:
        ...


def run_agent_safely(
    loop: RunnableLoop,
    user_message: str,
    event_loop: asyncio.AbstractEventLoop,
    queue: "asyncio.Queue[Optional[AgentEvent]]",
    thread_id_holder: list[int] | None = None,
) -> str:
    """
    Worker-thread entry: drives ``loop.run(user_message)`` synchronously
    and *always* posts a terminating sentinel so the drain loop in
    :meth:`AgentRunner.drive` can exit cleanly.

    If ``thread_id_holder`` is provided, stores the current thread's
    ident so the caller can interrupt a blocked LLM call from outside.
    """
    if thread_id_holder is not None:
        thread_id_holder.append(threading.get_ident())
    try:
        return loop.run(user_message)
    finally:
        # Post the sentinel via the owning loop.
        try:
            if not event_loop.is_closed():
                event_loop.call_soon_threadsafe(queue.put_nowait, None)
        except Exception:
            pass


@dataclass
class AgentRunner:
    """Drive a blocking loop in a worker thread, yield ``AgentEvent``s.

    Parameters
    ----------
    max_steps:
        Echoed into the ``MAX_STEPS_REACHED`` event payload so the UI
        knows what ceiling was in effect.
    run_id:
        Correlation id surfaced with terminal events.
    thinking_banner:
        Text for the very first ``STREAM_DELTA`` event.
    emit_final_answer:
        When True (default) the final answer is emitted as a trailing
        ``STREAM_DELTA``. Set to False when the underlying loop already
        streams its tokens — otherwise the final reply gets duplicated.
    """

    max_steps: int
    run_id: str
    thinking_banner: str = ""
    emit_final_answer: bool = True
    _worker_thread_id: int = field(default=0, init=False, repr=False)

    def interrupt_worker_thread(self) -> bool:
        """Inject a KeyboardInterrupt into the worker thread to unblock
        a stuck LLM HTTP call.  Returns True if the signal was sent.

        This is a last-resort mechanism — the normal interrupt path
        (loop.interrupt + executor.interrupt + task.cancel) handles
        most cases.  This only fires when the thread is blocked on a
        long-running HTTP request and won't return on its own.
        """
        tid = self._worker_thread_id
        if not tid:
            return False
        try:
            res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(tid), ctypes.py_object(KeyboardInterrupt)
            )
            if res == 1:
                logger.info("[interrupt] KeyboardInterrupt injected into thread %d", tid)
                return True
            elif res > 1:
                # Reset if multiple threads matched (shouldn't happen).
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), None)
                logger.warning("[interrupt] multiple threads matched, reset")
            return False
        except Exception as e:
            logger.warning("[interrupt] failed to inject into thread %d: %s", tid, e)
            return False

    async def drive(
        self,
        loop: RunnableLoop,
        user_message: str,
        *,
        queue: "asyncio.Queue[Optional[AgentEvent]]",
        on_cleanup: Optional[Callable[[], None]] = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Drive the loop and yield events.

        The caller is responsible for setting up the queue, the loop
        instance, and the step callback that feeds the queue.

        ``on_cleanup`` runs in the ``finally`` block regardless of how
        the run terminates.
        """
        event_loop = asyncio.get_running_loop()

        try:
            if self.thinking_banner:
                yield AgentEvent(
                    type=AgentEventType.STREAM_DELTA,
                    data=self.thinking_banner,
                )

            # Kick off the (blocking) loop in a worker thread.
            # Pass a mutable list so the worker can report its thread ident.
            thread_id_holder: list[int] = []
            agent_task = asyncio.create_task(
                asyncio.to_thread(
                    run_agent_safely, loop, user_message, event_loop, queue,
                    thread_id_holder,
                ),
            )

            # Wait briefly for the thread to register its ident.
            for _ in range(50):  # up to ~0.5s
                if thread_id_holder:
                    self._worker_thread_id = thread_id_holder[0]
                    break
                await asyncio.sleep(0.01)

            # Drain step events until the sentinel arrives.
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                yield ev

            # Await the task to get the final answer.
            try:
                final_answer = await agent_task
            except KeyboardInterrupt:
                yield AgentEvent(
                    type=AgentEventType.ERROR,
                    data="Agent interrupted by user",
                )
                yield AgentEvent(type=AgentEventType.STREAM_END)
                return
            except Exception as e:
                yield AgentEvent(type=AgentEventType.ERROR, data=f"Agent error: {e}")
                yield AgentEvent(type=AgentEventType.STREAM_END)
                return

            # Emit the final answer only if the underlying loop didn't
            # already stream it. AgentLoop streams via thought-delta
            # callbacks now, so re-emitting here would duplicate the
            # reply. WorkflowLoop still wants this single emission.
            if self.emit_final_answer and final_answer:
                yield AgentEvent(
                    type=AgentEventType.STREAM_DELTA,
                    data=str(final_answer),
                )
            yield AgentEvent(type=AgentEventType.STREAM_END)

        finally:
            if on_cleanup is not None:
                try:
                    on_cleanup()
                except Exception:
                    logger.exception("agent runner cleanup failed")


__all__ = ["AgentRunner", "RunnableLoop", "run_agent_safely"]
