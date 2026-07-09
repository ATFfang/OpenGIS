"""Streaming parser for assistant text and Python code blocks."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

_FENCE_OPEN_MARKER = "```"
_FENCE_CLOSE = "```"


@dataclass
class StreamingParser:
    """Classify streamed LLM text as reasoning text or Python code.

    This parser is used only for display while the provider streams content.
    Execution still requires a structured ``execute_code`` function call.
    """

    on_thought_delta: Callable[[str], None] | None = None
    on_code_start: Callable[[], None] | None = None
    on_code_delta: Callable[[str], None] | None = None
    on_code_end: Callable[[], None] | None = None

    _state: str = field(default="thought", init=False)
    _pending: str = field(default="", init=False)
    _emitted_open: bool = field(default=False, init=False)
    _swallow_next_newline: bool = field(default=False, init=False)

    _MAX_HOLD = 16

    def feed(self, chunk: str) -> None:
        """Feed one streamed chunk into the parser."""
        if not chunk:
            return
        self._pending += chunk
        while True:
            advanced = self._step()
            if not advanced:
                break

    def finish(self) -> None:
        """Flush held-back text once the provider stream ends."""
        if not self._pending:
            return
        if self._state == "thought":
            self._emit_thought(self._pending)
        elif self._state == "code":
            self._emit_code_delta(self._pending)
            self._emit_code_end()
        elif self._state == "in_fence_open":
            self._emit_thought(self._pending)
        elif self._state == "in_fence_close":
            self._emit_code_delta(self._pending)
            self._emit_code_end()
        self._pending = ""

    def _step(self) -> bool:
        if not self._pending:
            return False
        if self._state == "thought":
            return self._step_thought()
        if self._state == "in_fence_open":
            return self._step_fence_open()
        if self._state == "code":
            return self._step_code()
        if self._state == "in_fence_close":
            return self._step_fence_close()
        return False

    def _step_thought(self) -> bool:
        if self._swallow_next_newline and self._pending:
            self._swallow_next_newline = False
            if self._pending[0] == "\r":
                if len(self._pending) >= 2 and self._pending[1] == "\n":
                    self._pending = self._pending[2:]
                else:
                    self._pending = self._pending[1:]
                if not self._pending:
                    return False
            elif self._pending[0] == "\n":
                self._pending = self._pending[1:]
                if not self._pending:
                    return False

        idx = self._pending.find("`")
        if idx == -1:
            self._emit_thought(self._pending)
            self._pending = ""
            return False

        if idx > 0:
            self._emit_thought(self._pending[:idx])
            self._pending = self._pending[idx:]

        self._state = "in_fence_open"
        return True

    def _step_fence_open(self) -> bool:
        buf = self._pending
        if buf.startswith(_FENCE_OPEN_MARKER):
            for suffix in ("\n", "\r\n"):
                idx = buf.find(suffix, len(_FENCE_OPEN_MARKER))
                if idx >= 0:
                    fence_header = buf[len(_FENCE_OPEN_MARKER):idx].strip().lower()
                    if fence_header in {"python", "py"}:
                        self._pending = buf[idx + len(suffix):]
                        self._enter_code()
                    else:
                        self._emit_thought(buf[:idx + len(suffix)])
                        self._pending = buf[idx + len(suffix):]
                        self._state = "thought"
                    return True
            if len(buf) > self._MAX_HOLD:
                self._emit_thought(buf[0])
                self._pending = buf[1:]
                self._state = "thought"
                return True
            return False
        self._emit_thought(buf[0])
        self._pending = buf[1:]
        self._state = "thought"
        return True

    def _step_code(self) -> bool:
        idx = self._pending.find("`")
        if idx == -1:
            self._emit_code_delta(self._pending)
            self._pending = ""
            return False

        if idx > 0:
            self._emit_code_delta(self._pending[:idx])
            self._pending = self._pending[idx:]

        self._state = "in_fence_close"
        return True

    def _step_fence_close(self) -> bool:
        buf = self._pending
        if buf.startswith(_FENCE_CLOSE):
            self._pending = buf[len(_FENCE_CLOSE):]
            if self._pending.startswith("\r\n"):
                self._pending = self._pending[2:]
            elif self._pending.startswith("\n"):
                self._pending = self._pending[1:]
            else:
                self._swallow_next_newline = True
            self._exit_code()
            return True
        if _FENCE_CLOSE.startswith(buf):
            if len(buf) > self._MAX_HOLD:
                self._emit_code_delta(buf[0])
                self._pending = buf[1:]
                self._state = "code"
                return True
            return False
        self._emit_code_delta(buf[0])
        self._pending = buf[1:]
        self._state = "code"
        return True

    def _enter_code(self) -> None:
        self._state = "code"
        if not self._emitted_open:
            self._emitted_open = True
            if self.on_code_start:
                try:
                    self.on_code_start()
                except Exception:
                    logger.exception("on_code_start callback raised")

    def _exit_code(self) -> None:
        self._state = "thought"
        if self.on_code_end:
            try:
                self.on_code_end()
            except Exception:
                logger.exception("on_code_end callback raised")

    def _emit_thought(self, text: str) -> None:
        if not text or not self.on_thought_delta:
            return
        try:
            self.on_thought_delta(text)
        except Exception:
            logger.exception("on_thought_delta callback raised")

    def _emit_code_delta(self, text: str) -> None:
        if not text or not self.on_code_delta:
            return
        try:
            self.on_code_delta(text)
        except Exception:
            logger.exception("on_code_delta callback raised")

    def _emit_code_end(self) -> None:
        if self.on_code_end and self._emitted_open:
            try:
                self.on_code_end()
            except Exception:
                logger.exception("on_code_end callback raised")


__all__ = ["StreamingParser"]
