"""Per-run callback bundle for agent loop telemetry and persistence."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from opengis_backend.agent.loop.types import AgentStep
from opengis_backend.agent.telemetry.artifacts import ArtifactIndex, artifacts_from_tool_result
from opengis_backend.agent.telemetry.events import AgentEvent, AgentEventType, _enqueue
from opengis_backend.agent.telemetry.script_archive import ScriptArchive
from opengis_backend.agent.telemetry.step_recorder import StepRecorder
from opengis_backend.agent.workflow.workflow_model import WorkflowDocument
from opengis_backend.runs import RunArchive
from opengis_backend.tools.builtin._asset_refresh import notify_asset_refresh
from opengis_backend.tools.context import ToolContext, run_async_from_sync


logger = logging.getLogger(__name__)

_DISCLOSURE_KEEP_ARG_KEYS = {
    "path",
    "file_path",
    "src",
    "dst",
    "layer_id",
    "name",
    "query",
    "url",
    "workflow_name",
    "element_id",
}
_DISCLOSURE_MAX_ARG_CHARS = 500


def event_tool_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Return UI-safe tool args while preserving execute_code source display."""
    if name == "execute_code":
        return args
    slim: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if key not in _DISCLOSURE_KEEP_ARG_KEYS:
            continue
        if isinstance(value, str) and len(value) > _DISCLOSURE_MAX_ARG_CHARS:
            slim[key] = value[:_DISCLOSURE_MAX_ARG_CHARS] + "...(truncated)"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            slim[key] = value
        else:
            slim[key] = str(value)[:_DISCLOSURE_MAX_ARG_CHARS]
    if not slim and args:
        slim["_summary"] = f"{len(args)} argument(s) hidden"
    return slim


class AgentRunCallbacks:
    """Callbacks passed into AgentLoop/WorkflowLoop for one run."""

    def __init__(
        self,
        *,
        ctx: ToolContext,
        archive: ScriptArchive,
        run_archive: RunArchive,
        loop: asyncio.AbstractEventLoop,
        event_queue: asyncio.Queue[AgentEvent | None],
        user_message: str,
        session_id: str,
        workspace: str | None,
        workflow: WorkflowDocument | None,
    ):
        self.ctx = ctx
        self.archive = archive
        self.run_archive = run_archive
        self.loop = loop
        self.event_queue = event_queue
        self.user_message = user_message
        self.session_id = session_id
        self.workspace = workspace
        self.workflow = workflow
        self.recorder = StepRecorder(
            archive=archive,
            loop=loop,
            queue=event_queue,
            user_message=user_message,
        )
        self.pending_asset_refresh_paths: set[str] = set()
        self.current_output_tool: dict[str, str | None] = {
            "call_id": None,
            "name": None,
        }

    def _emit(self, event_type: AgentEventType, data: dict[str, Any] | str | None = None) -> None:
        _enqueue(self.loop, self.event_queue, AgentEvent(type=event_type, data=data))

    def flush_asset_refreshes(self, reason: str) -> None:
        if not self.pending_asset_refresh_paths:
            return
        paths = list(self.pending_asset_refresh_paths)
        self.pending_asset_refresh_paths.clear()
        notify_asset_refresh(self.ctx, paths[0], reason=reason)

    def step_callback(self, step: AgentStep) -> None:
        """Record a step and mirror executable code into the run archive."""
        try:
            self.recorder.on_step(step)
            if step.is_text_reply or not step.code:
                return
            self.run_archive.record_step(
                step=step.step_num,
                code=step.code,
                output=step.output,
                error=step.error,
                script_path=None,
            )
        except Exception:
            logger.exception("run_archive.record_step mirror failed")
        finally:
            if not step.is_text_reply and step.code:
                self.flush_asset_refreshes("execute_code")

    def risky_listener(self, entry: dict) -> None:
        self.run_archive.record_risky_op(entry)
        path_raw = entry.get("path")
        if not path_raw:
            return
        try:
            changed = Path(str(path_raw)).expanduser()
            if not changed.is_absolute() and self.workspace:
                changed = Path(self.workspace).expanduser().resolve() / changed
            self.pending_asset_refresh_paths.add(str(changed))
        except Exception:
            logger.debug("execute_code asset refresh failed", exc_info=True)

    def progress_callback(self, stage: str, detail: str = "") -> None:
        self.recorder.on_progress(stage, detail)

    def execution_output_callback(self, text: str) -> None:
        if not text:
            return
        call_id = self.current_output_tool.get("call_id")
        name = self.current_output_tool.get("name")
        if not call_id or name != "execute_code":
            return
        self._emit(
            AgentEventType.TOOL_OUTPUT_DELTA,
            {
                "call_id": call_id,
                "name": name,
                "delta": text,
                "run_id": self.archive.run_id,
            },
        )

    def on_thought_delta(self, text: str) -> None:
        if not text:
            return
        self._emit(
            AgentEventType.STREAM_DELTA,
            {
                "content": text,
                "run_id": self.archive.run_id,
            },
        )

    def on_code_start(self, step_num: int) -> None:
        self._emit(
            AgentEventType.CODE_BLOCK_START,
            {
                "step": step_num,
                "run_id": self.archive.run_id,
            },
        )

    def on_code_delta(self, step_num: int, text: str) -> None:
        if not text:
            return
        self._emit(
            AgentEventType.CODE_DELTA,
            {
                "step": step_num,
                "delta": text,
                "run_id": self.archive.run_id,
            },
        )

    def on_code_end(self, step_num: int) -> None:
        self._emit(
            AgentEventType.CODE_BLOCK_END,
            {
                "step": step_num,
                "run_id": self.archive.run_id,
            },
        )

    def on_tool_start(self, name: str, args: dict, call_id: str) -> None:
        if name == "execute_code":
            self.current_output_tool["call_id"] = call_id
            self.current_output_tool["name"] = name
        if self.ctx.meta is not None:
            pending = self.ctx.meta.setdefault("_tool_call_args", {})
            pending[call_id] = args
        try:
            self.run_archive.record_tool_call(
                call_id=call_id,
                name=name,
                arguments=args,
                status="running",
                metadata={"session_id": self.session_id},
            )
        except Exception:
            logger.debug("tool call start archive failed (non-fatal)", exc_info=True)
        self._emit(
            AgentEventType.TOOL_START,
            {
                "name": name,
                "args": event_tool_args(name, args),
                "args_hidden": name != "execute_code" and bool(args),
                "call_id": call_id,
                "run_id": self.archive.run_id,
            },
        )

    def on_tool_result(
        self,
        name: str,
        content: str,
        error: str | None,
        duration_ms: float,
        call_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = {}
        if self.ctx.meta is not None:
            pending = self.ctx.meta.get("_tool_call_args")
            if isinstance(pending, dict):
                args = pending.pop(call_id, {}) or {}

        result_metadata = dict(metadata or {})
        if self.current_output_tool.get("call_id") == call_id:
            self.current_output_tool["call_id"] = None
            self.current_output_tool["name"] = None
        if name == "execute_code":
            self._persist_execute_code_result(
                args=args,
                content=content,
                error=error,
                call_id=call_id,
                result_metadata=result_metadata,
            )

        self._emit(
            AgentEventType.TOOL_RESULT,
            {
                "name": name,
                "output": content,
                "error": error,
                "duration_ms": duration_ms,
                "call_id": call_id,
                "run_id": self.archive.run_id,
                "metadata": result_metadata,
            },
        )
        self._record_tool_result(
            name=name,
            args=args,
            content=content,
            error=error,
            duration_ms=duration_ms,
            call_id=call_id,
            metadata=metadata,
            result_metadata=result_metadata,
        )
        return result_metadata

    def plan_callback(self, plan_data: dict) -> None:
        plan_data["workflow"] = True
        plan_data.setdefault("run_id", self.archive.run_id)
        try:
            run_async_from_sync(self.ctx.notify("rpc.ui.chat.plan_update", plan_data))
        except Exception:
            logger.debug("plan_callback notify failed", exc_info=True)

    def _persist_execute_code_result(
        self,
        *,
        args: dict[str, Any],
        content: str,
        error: str | None,
        call_id: str,
        result_metadata: dict[str, Any],
    ) -> None:
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return
        persist_requested = bool(args.get("persist"))
        should_persist = self.workflow is not None or persist_requested
        try:
            step_no = int(result_metadata.get("code_step") or 0)
        except Exception:
            step_no = 0
        if step_no <= 0:
            step_no = self.archive.next_step_num()
        result_metadata["code_step"] = step_no
        result_metadata["persisted"] = should_persist
        if not should_persist:
            return
        semantic_name = str(args.get("script_name") or "")
        description = str(args.get("description") or "")
        try:
            abs_path = self.archive.write_step(
                step=step_no,
                code=code,
                user_message=self.user_message,
                observations=content,
                error=error,
                semantic_name=semantic_name,
                metadata={
                    "description": description,
                    "persist_requested": persist_requested,
                    "workflow": self.workflow.to_dict() if self.workflow is not None else None,
                    "loop_kind": "workflow" if self.workflow is not None else "chat",
                    "tool_call_id": call_id,
                },
            )
            rel_path = self.archive.to_relative(abs_path)
            result_metadata.update({
                "script_path": rel_path,
                "script_abs_path": str(abs_path),
            })
            notify_asset_refresh(self.ctx, abs_path, reason="persist_script")
            self.run_archive.record_step(
                step=step_no,
                code=code,
                output=content,
                error=error,
                script_path=rel_path,
            )
        except Exception:
            logger.debug("execute_code script persistence failed (non-fatal)", exc_info=True)

    def _record_tool_result(
        self,
        *,
        name: str,
        args: dict[str, Any],
        content: str,
        error: str | None,
        duration_ms: float,
        call_id: str,
        metadata: dict[str, Any] | None,
        result_metadata: dict[str, Any],
    ) -> None:
        try:
            archive_metadata = {
                "session_id": self.session_id,
                **result_metadata,
            }
            self.run_archive.record_tool_call(
                call_id=call_id,
                name=name,
                arguments=args,
                output=content,
                error=error,
                duration_ms=duration_ms,
                metadata=archive_metadata,
                status="error" if error else "completed",
            )
            artifact_meta = {
                "run_id": self.archive.run_id,
                "session_id": self.session_id,
                "tool_name": name,
                "call_id": call_id,
                **(metadata or {}),
                **result_metadata,
            }
            artifacts = artifacts_from_tool_result(name, content, artifact_meta)
            if artifacts:
                index = ArtifactIndex(self.workspace)
                for artifact in artifacts:
                    payload = artifact.to_dict()
                    self.run_archive.record_artifact(payload)
                    index.append(artifact)
        except Exception:
            logger.debug("tool call archive recording failed (non-fatal)", exc_info=True)
