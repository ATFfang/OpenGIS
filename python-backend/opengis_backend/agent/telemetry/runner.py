"""
AgentRunner — drives any Loop (AgentLoop or WorkflowLoop) in a worker
thread, yields AgentEvents.

The loop blocks synchronously (LLM calls + subprocess execution).
This module wraps it into an ``async`` generator yielding
:class:`AgentEvent` so the rest of the codebase (RPC handler, CLI,
tests) only has to ``async for event in runner.drive(...)``.

The runner drives any object with a ``run(user_message: str) -> str``
method. This allows the same Runner to drive both AgentLoop
(free-form function-call chat) and WorkflowLoop (DAG-driven).
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Optional, Protocol, runtime_checkable

from opengis_backend.agent.telemetry.events import AgentEvent, AgentEventType, _enqueue

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
    """
    tid = threading.get_ident()
    if thread_id_holder is not None:
        thread_id_holder.append(tid)
    logger.debug("[RUNNER] run_agent_safely START, thread=%d", tid)
    try:
        result = loop.run(user_message)
        logger.debug("[RUNNER] run_agent_safely loop.run() returned normally, thread=%d, result_len=%d",
                    tid, len(result) if result else 0)
        return result
    except KeyboardInterrupt:
        logger.debug("[RUNNER] run_agent_safely caught KeyboardInterrupt in thread=%d", tid)
        return "(Task interrupted by user.)"
    except Exception as e:
        logger.error("[RUNNER-DEBUG] run_agent_safely caught %s: %s in thread=%d", type(e).__name__, e, tid)
        raise
    finally:
        logger.debug("[RUNNER] run_agent_safely FINALLY, posting sentinel, thread=%d", tid)
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
    on_final_answer:
        Optional callback invoked with the loop's returned final answer
        regardless of whether it is emitted to the UI.
    """

    max_steps: int
    run_id: str
    thinking_banner: str = ""
    emit_final_answer: bool = True
    on_final_answer: Optional[Callable[[str], None]] = None
    _worker_thread_id: int = field(default=0, init=False, repr=False)

    def interrupt_worker_thread(self) -> bool:
        """Inject a KeyboardInterrupt into the worker thread to unblock
        a stuck LLM HTTP call.  Returns True if the signal was sent.
        """
        tid = self._worker_thread_id
        logger.debug("[RUNNER] interrupt_worker_thread called, tid=%d", tid)
        if not tid:
            logger.debug("[RUNNER] no worker thread id, returning False")
            return False
        try:
            res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(tid), ctypes.py_object(KeyboardInterrupt)
            )
            if res == 1:
                logger.debug("[RUNNER] KeyboardInterrupt injected into thread %d SUCCESS", tid)
                return True
            elif res > 1:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), None)
                logger.warning("[RUNNER-DEBUG] multiple threads matched, reset")
            logger.debug("[RUNNER] injection returned res=%d", res)
            return False
        except Exception as e:
            logger.warning("[RUNNER-DEBUG] failed to inject into thread %d: %s", tid, e)
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
                    data={"content": self.thinking_banner, "run_id": self.run_id},
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

            if final_answer and self.on_final_answer is not None:
                try:
                    self.on_final_answer(str(final_answer))
                except Exception:
                    logger.exception("on_final_answer callback failed")

            # Emit the final answer only if the underlying loop didn't
            # already stream it. AgentLoop streams via thought-delta
            # callbacks now, so re-emitting here would duplicate the
            # reply. WorkflowLoop still wants this single emission.
            if self.emit_final_answer and final_answer:
                yield AgentEvent(
                    type=AgentEventType.STREAM_DELTA,
                    data={"content": str(final_answer), "run_id": self.run_id},
                )
            yield AgentEvent(type=AgentEventType.STREAM_END, data={"run_id": self.run_id})

        finally:
            if on_cleanup is not None:
                try:
                    on_cleanup()
                except Exception:
                    logger.exception("agent runner cleanup failed")


__all__ = ["AgentRunner", "RunnableLoop", "run_agent_safely"]
