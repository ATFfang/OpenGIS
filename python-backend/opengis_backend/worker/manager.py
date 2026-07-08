"""Resident Python worker process manager.

Workers are long-running Python applications started by the agent or by the
UI. Each worker owns a workspace-local folder under
``worker/<worker_id>/`` containing its script, metadata, and logs.
The manager keeps process handles in memory and restores workspace-local
metadata after backend restarts. Restored workers are paused/stopped until the
user or agent explicitly restarts them.
"""

from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


MAX_WORKERS = 2
MAX_LOG_LINES = 500
MAX_LOG_TEXT_CHARS = 1600
MAX_LOG_FILE_BYTES = 5 * 1024 * 1024
MAX_LOG_READ_BYTES = 2 * 1024 * 1024
ENTRYPOINT_FILENAME = "main.py"
LEGACY_ENTRYPOINT_FILENAME = "worker.py"
HELPER_FILENAME = "opengis_worker.py"
MANIFEST_FILENAME = "manifest.json"
README_FILENAME = "README.md"
CONFIG_FILENAME = "config.json"
SRC_DIRNAME = "src"
SERVICE_PACKAGE_VERSION = 1

WorkerEventCallback = Callable[[str, dict[str, Any]], None]


def _now() -> float:
    return time.time()


def _slug(text: str, fallback: str = "worker") -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text.strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:48] or fallback


def _feature_count(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    features = value.get("features")
    return len(features) if isinstance(features, list) else None


def _compact_log_text(text: str) -> str:
    """Keep UI-facing worker logs readable under high-frequency JSON output."""
    compact = text
    try:
        payload = json.loads(text)
    except Exception:
        payload = None

    if isinstance(payload, dict):
        method = payload.get("opengis_method")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else payload
        if method == "rpc.ui.map.dynamic_layer_update" or payload.get("opengis_event") == "dynamic_layer_update":
            layer_id = params.get("layer_id") or "-"
            mode = params.get("mode") or ("full" if "geojson" in params else "diff")
            sequence = params.get("sequence")
            geojson_count = _feature_count(params.get("geojson"))
            diff = params.get("diff") if isinstance(params.get("diff"), dict) else {}
            update = diff.get("update") if isinstance(diff, dict) else None
            remove = diff.get("remove") if isinstance(diff, dict) else None
            update_count = len(update) if isinstance(update, list) else None
            remove_count = len(remove) if isinstance(remove, list) else None
            parts = [
                "[dynamic_map]",
                f"layer={layer_id}",
                f"mode={mode}",
            ]
            if sequence is not None:
                parts.append(f"seq={sequence}")
            if geojson_count is not None:
                parts.append(f"features={geojson_count}")
            if update_count is not None:
                parts.append(f"update={update_count}")
            if remove_count is not None:
                parts.append(f"remove={remove_count}")
            compact = " ".join(parts)

    if len(compact) > MAX_LOG_TEXT_CHARS:
        return compact[:MAX_LOG_TEXT_CHARS] + f"... <truncated {len(compact) - MAX_LOG_TEXT_CHARS} chars>"
    return compact


def _entrypoint_path(folder: Path) -> Path:
    return folder / ENTRYPOINT_FILENAME


def _legacy_entrypoint_path(folder: Path) -> Path:
    return folder / LEGACY_ENTRYPOINT_FILENAME


def _default_manifest(*, worker_id: str, name: str, description: str, kind: str = "resident") -> dict[str, Any]:
    return {
        "schema_version": SERVICE_PACKAGE_VERSION,
        "id": worker_id,
        "name": name or worker_id,
        "description": description or "",
        "kind": kind,
        "entrypoint": ENTRYPOINT_FILENAME,
        "permissions": {
            "network": "approval_required",
            "filesystem": "workspace",
            "dynamic_map": True,
        },
        "runtime": {
            "language": "python",
            "max_running_workers": MAX_WORKERS,
            "unbuffered_stdout": True,
        },
        "layers": [],
        "contracts": {
            "stdout": "line-delimited JSON for rpc.ui.* events plus human-readable logs",
            "dynamic_map": "emit full frame first, then diff frames with stable feature ids",
        },
    }


def _default_readme(*, worker_id: str, name: str, description: str) -> str:
    title = name or worker_id
    summary = description.strip() or "Resident OpenGIS worker."
    return (
        f"# {title}\n\n"
        f"{summary}\n\n"
        "## Structure\n\n"
        "- `main.py`: the only process entrypoint; keep it thin.\n"
        "- `config.json`: runtime configuration for polling, layer ids, and data sources.\n"
        "- `src/datasource.py`: data acquisition from APIs, files, or sockets.\n"
        "- `src/service.py`: transformation, validation, aggregation, and state updates.\n"
        "- `src/publisher.py`: OpenGIS output adapter using `opengis_worker`.\n"
        "- `manifest.json`: worker service contract used by OpenGIS and the agent.\n\n"
        "## Agent Rules\n\n"
        "Modify the smallest layer that matches the bug. Do not replace the whole worker "
        "when logs identify a failing datasource/service/publisher function. Do not edit "
        "`opengis_worker.py`; it is generated by OpenGIS.\n"
    )


def _default_config(*, worker_id: str, name: str) -> dict[str, Any]:
    slug = _slug(name or worker_id)
    return {
        "worker_id": worker_id,
        "interval_seconds": 2,
        "layers": {
            "points": f"{slug}_points",
            "tracks": f"{slug}_tracks",
        },
    }


def _default_src_files() -> dict[str, str]:
    return {
        "src/__init__.py": '"""Worker package modules."""\n',
        "src/datasource.py": (
            '"""Data access layer for the worker.\n\n'
            "Keep external API calls, file reads, and socket polling here.\n"
            '"""\n\n'
            "from __future__ import annotations\n\n\n"
            "def fetch_snapshot(config: dict) -> dict:\n"
            "    \"\"\"Return one raw data snapshot.\n\n"
            "    Replace this with API/file/socket logic. Keep it side-effect-light so\n"
            "    the worker can retry and tests can exercise the service layer.\n"
            "    \"\"\"\n"
            "    return {\"items\": []}\n"
        ),
        "src/service.py": (
            '"""Business logic layer for the worker.\n\n'
            "Transform raw snapshots into stable map objects or other outputs here.\n"
            '"""\n\n'
            "from __future__ import annotations\n\n\n"
            "def build_state(snapshot: dict, previous_state: dict | None = None) -> dict:\n"
            "    previous_state = previous_state or {}\n"
            "    return {\"points\": [], \"tracks\": previous_state.get(\"tracks\", {})}\n"
        ),
        "src/publisher.py": (
            '"""OpenGIS output adapter for the worker.\n\n'
            "Only publishing code should import opengis_worker.\n"
            '"""\n\n'
            "from __future__ import annotations\n\n"
            "from opengis_worker import emit_moving_objects\n\n\n"
            "def publish_state(config: dict, state: dict, sequence: int) -> None:\n"
            "    layers = config.get(\"layers\", {}) if isinstance(config, dict) else {}\n"
            "    emit_moving_objects(\n"
            "        point_layer_id=str(layers.get(\"points\") or \"worker_points\"),\n"
            "        track_layer_id=str(layers.get(\"tracks\") or \"worker_tracks\"),\n"
            "        points=list(state.get(\"points\") or []),\n"
            "        tracks=dict(state.get(\"tracks\") or {}),\n"
            "        sequence=sequence,\n"
            "        point_name=\"Live Points\",\n"
            "        track_name=\"Live Tracks\",\n"
            "    )\n"
        ),
    }


def _read_recent_log_lines(path: Path, *, max_lines: int = MAX_LOG_LINES) -> list[str]:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            start = max(0, size - MAX_LOG_READ_BYTES)
            f.seek(start)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if start > 0 and lines:
            lines = lines[1:]
        return lines[-max_lines:]
    except Exception:
        return []


@dataclass
class ResidentWorker:
    id: str
    name: str
    workspace_path: str
    folder: str
    script_path: str
    status: str = "starting"
    description: str = ""
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    started_at: float | None = None
    stopped_at: float | None = None
    startup_checked_at: float | None = None
    startup_check_state: str | None = None
    startup_check_message: str | None = None
    pid: int | None = None
    returncode: int | None = None
    last_error: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    logs: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    resource_cache: dict[str, Any] | None = field(default=None, repr=False)
    resource_sampled_at: float = field(default=0.0, repr=False)
    process: subprocess.Popen | None = field(default=None, repr=False)
    stdout_thread: threading.Thread | None = field(default=None, repr=False)
    stderr_thread: threading.Thread | None = field(default=None, repr=False)

    def public_dict(self, *, include_logs: bool = True) -> dict[str, Any]:
        resources = self._sample_resources()
        health = self._health_summary()
        manifest = self._read_manifest()
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "workspace_path": self.workspace_path,
            "folder": self.folder,
            "script_path": self.script_path,
            "status": self.status,
            "pid": self.pid,
            "returncode": self.returncode,
            "last_error": self.last_error,
            "manifest": manifest,
            "package": {
                "schema_version": manifest.get("schema_version") if isinstance(manifest, dict) else None,
                "entrypoint": manifest.get("entrypoint") if isinstance(manifest, dict) else ENTRYPOINT_FILENAME,
                "has_readme": (Path(self.folder) / README_FILENAME).exists(),
                "has_config": (Path(self.folder) / CONFIG_FILENAME).exists(),
                "src_files": self._list_src_files(),
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "resources": resources,
            "health": health,
            "startup_check": {
                "state": self.startup_check_state,
                "message": self.startup_check_message,
                "checked_at": self.startup_checked_at,
            },
            "logs": list(self.logs) if include_logs else [],
        }

    def _read_manifest(self) -> dict[str, Any]:
        path = Path(self.folder) / MANIFEST_FILENAME
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return dict(self.manifest or {})

    def _list_src_files(self) -> list[str]:
        src = Path(self.folder) / SRC_DIRNAME
        if not src.exists() or not src.is_dir():
            return []
        files: list[str] = []
        for path in sorted(src.rglob("*")):
            if path.is_file():
                try:
                    files.append(str(path.relative_to(Path(self.folder))))
                except Exception:
                    continue
        return files[:80]

    def _health_summary(self) -> dict[str, Any]:
        now = _now()
        since = self.started_at if self.status in {"running", "starting"} else None
        recent_logs = [
            item
            for item in list(self.logs)[-48:]
            if item.get("stream") in {"stdout", "stderr"}
            and (since is None or float(item.get("ts") or 0) >= since)
        ]
        recent_stderr = [str(item.get("text", "")) for item in recent_logs if item.get("stream") == "stderr"]
        last_log_at = recent_logs[-1].get("ts") if recent_logs else None
        last_log_text = str(recent_logs[-1].get("text", "")) if recent_logs else ""

        if self.status in {"running", "starting"}:
            if not recent_logs:
                return {
                    "state": "uncertain",
                    "ok": None,
                    "message": "process is alive, but no worker output has been observed yet",
                    "checked_at": now,
                    "last_log_at": None,
                }
            if recent_stderr:
                return {
                    "state": "warning",
                    "ok": None,
                    "message": recent_stderr[-1][-500:],
                    "checked_at": now,
                    "last_log_at": last_log_at,
                }
            return {
                "state": "ok",
                "ok": True,
                "message": last_log_text[-500:] or "process is alive",
                "checked_at": now,
                "last_log_at": last_log_at,
            }

        if self.status == "failed":
            return {
                "state": "failed",
                "ok": False,
                "message": self.last_error or (recent_stderr[-1][-500:] if recent_stderr else "worker failed"),
                "checked_at": now,
                "last_log_at": last_log_at,
            }

        if self.status == "stopped":
            return {
                "state": "stopped",
                "ok": False,
                "message": f"process exited with code {self.returncode}",
                "checked_at": now,
                "last_log_at": last_log_at,
            }

        return {
            "state": self.status,
            "ok": None,
            "message": self.last_error or self.status,
            "checked_at": now,
            "last_log_at": last_log_at,
        }

    def _sample_resources(self) -> dict[str, Any]:
        """Best-effort process resource sample.

        Keep this dependency-free: on macOS/Linux we ask ``ps`` for CPU, RSS,
        and elapsed runtime. If sampling fails, callers still get a stable
        resources object instead of a worker-list failure.
        """
        now = _now()
        if self.resource_cache and now - self.resource_sampled_at < 1.5:
            return self.resource_cache

        unavailable = {
            "available": False,
            "cpu_percent": None,
            "rss_bytes": None,
            "rss_mb": None,
            "elapsed": None,
            "sampled_at": now,
        }
        if not self.pid or self.status not in {"starting", "running"}:
            self.resource_cache = unavailable
            self.resource_sampled_at = now
            return unavailable

        try:
            if self.process and self.process.poll() is not None:
                raise RuntimeError("process is not running")
            result = subprocess.run(
                ["ps", "-p", str(self.pid), "-o", "%cpu=", "-o", "rss=", "-o", "etime="],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.75,
            )
            line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
            if result.returncode != 0 or not line:
                raise RuntimeError(result.stderr.strip() or "ps returned no output")
            parts = line.split(None, 2)
            if len(parts) < 3:
                raise RuntimeError(f"unexpected ps output: {line!r}")
            cpu = float(parts[0])
            rss_bytes = int(float(parts[1])) * 1024
            resources = {
                "available": True,
                "cpu_percent": cpu,
                "rss_bytes": rss_bytes,
                "rss_mb": round(rss_bytes / 1024 / 1024, 1),
                "elapsed": parts[2].strip(),
                "sampled_at": now,
            }
        except Exception as exc:
            resources = {**unavailable, "error": str(exc)}

        self.resource_cache = resources
        self.resource_sampled_at = now
        return resources


class ResidentWorkerManager:
    """Process-local manager for resident Python workers."""

    def __init__(self) -> None:
        self._workers: dict[str, ResidentWorker] = {}
        self._event_callbacks: set[WorkerEventCallback] = set()
        self._lock = threading.RLock()

    def subscribe_events(self, callback: WorkerEventCallback) -> Callable[[], None]:
        """Subscribe to worker-emitted frontend events.

        Worker scripts emit structured events by printing one JSON line with
        either ``opengis_event`` or ``opengis_method``. The manager fans those
        events out to all connected RPC handlers.
        """
        with self._lock:
            self._event_callbacks.add(callback)

        def unsubscribe() -> None:
            with self._lock:
                self._event_callbacks.discard(callback)

        return unsubscribe

    def start_worker(
        self,
        *,
        workspace_path: str,
        name: str,
        code: str,
        description: str = "",
        worker_id: str | None = None,
        files: dict[str, str] | None = None,
        manifest: dict[str, Any] | None = None,
        initial_health_timeout: float = 1.5,
    ) -> dict[str, Any]:
        workspace = Path(workspace_path).expanduser().resolve()
        if not workspace.exists():
            raise ValueError(f"workspace_path does not exist: {workspace_path}")
        if not code.strip():
            raise ValueError("worker code is required")

        with self._lock:
            self._refresh_locked()
            active = [w for w in self._workers.values() if w.status in {"starting", "running"}]
            if len(active) >= MAX_WORKERS:
                raise RuntimeError(f"worker limit reached: max {MAX_WORKERS} running workers")

            wid = worker_id or f"worker_{uuid.uuid4().hex[:10]}"
            if wid in self._workers and self._workers[wid].status in {"starting", "running"}:
                raise RuntimeError(f"worker already running: {wid}")

            folder = workspace / "worker" / f"{_slug(name)}-{wid}"
            folder.mkdir(parents=True, exist_ok=True)
            script_path = _entrypoint_path(folder)
            helper_path = folder / HELPER_FILENAME
            meta_path = folder / "metadata.json"
            stdout_path = folder / "stdout.log"
            stderr_path = folder / "stderr.log"
            script_path.write_text(code, encoding="utf-8")
            helper_path.write_text(_WORKER_HELPER_CODE, encoding="utf-8")
            package_manifest = self._ensure_service_package_locked(
                folder,
                worker_id=wid,
                name=name or wid,
                description=description,
                manifest=manifest,
                files=files,
                overwrite=False,
            )

            env = os.environ.copy()
            env["OPENGIS_WORKER_ID"] = wid
            env["OPENGIS_WORKSPACE"] = str(workspace)
            env["PYTHONUNBUFFERED"] = "1"

            process = subprocess.Popen(
                [sys.executable, "-u", str(script_path)],
                cwd=str(workspace),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
            )

            worker = ResidentWorker(
                id=wid,
                name=name or wid,
                description=description,
                workspace_path=str(workspace),
                folder=str(folder),
                script_path=str(script_path),
                status="running",
                started_at=_now(),
                pid=process.pid,
                process=process,
                manifest=package_manifest,
            )
            self._workers[wid] = worker
            self._write_metadata(worker, meta_path)

            worker.stdout_thread = threading.Thread(
                target=self._pump_stream,
                args=(worker, process.stdout, "stdout", stdout_path),
                daemon=True,
                name=f"{wid}-stdout",
            )
            worker.stderr_thread = threading.Thread(
                target=self._pump_stream,
                args=(worker, process.stderr, "stderr", stderr_path),
                daemon=True,
                name=f"{wid}-stderr",
            )
            worker.stdout_thread.start()
            worker.stderr_thread.start()
            threading.Thread(target=self._watch_process, args=(worker, process), daemon=True, name=f"{wid}-watch").start()

        self._wait_initial_health(wid, timeout=initial_health_timeout)
        with self._lock:
            self._refresh_locked()
            return self._require(wid).public_dict()

    def pause_worker(
        self,
        worker_id: str,
        *,
        reason: str = "paused",
        workspace_path: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if workspace_path:
                self._restore_workspace_locked(workspace_path)
            worker = self._require(worker_id)
            self._terminate_locked(worker, status="paused", reason=reason)
            return worker.public_dict()

    def restart_worker(
        self,
        worker_id: str,
        *,
        code: str | None = None,
        files: dict[str, str] | None = None,
        manifest: dict[str, Any] | None = None,
        reason: str = "restart",
        initial_health_timeout: float = 1.5,
        workspace_path: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if workspace_path:
                self._restore_workspace_locked(workspace_path)
            self._refresh_locked()
            worker = self._require(worker_id)
            self._normalize_worker_entrypoint_locked(worker)
            if code is not None:
                if not code.strip():
                    raise ValueError("worker code is required")
                Path(worker.script_path).write_text(code, encoding="utf-8")
            Path(worker.folder, HELPER_FILENAME).write_text(_WORKER_HELPER_CODE, encoding="utf-8")
            worker.manifest = self._ensure_service_package_locked(
                Path(worker.folder),
                worker_id=worker.id,
                name=worker.name,
                description=worker.description,
                manifest=manifest,
                files=files,
                overwrite=True,
            )
            if worker.status in {"starting", "running"}:
                self._terminate_locked(worker, status="paused", reason=reason)
            self._launch_worker_locked(worker, reason=reason)

        self._wait_initial_health(worker_id, timeout=initial_health_timeout)
        with self._lock:
            self._refresh_locked()
            return self._require(worker_id).public_dict()

    def delete_worker(self, worker_id: str, *, workspace_path: str | None = None) -> dict[str, Any]:
        with self._lock:
            cleaned_deleted_ids: set[str] = set()
            if workspace_path:
                cleaned_deleted_ids = self._restore_workspace_locked(workspace_path)
            try:
                worker = self._require(worker_id)
            except KeyError:
                if worker_id in cleaned_deleted_ids:
                    return {
                        "id": worker_id,
                        "status": "deleted",
                        "folder_deleted": True,
                    }
                if workspace_path:
                    deleted_folder = self._delete_workspace_worker_folder_by_id(workspace_path, worker_id)
                    if deleted_folder is not None:
                        return {
                            "id": worker_id,
                            "status": "deleted",
                            "folder": str(deleted_folder),
                            "folder_deleted": True,
                        }
                raise
            self._terminate_locked(worker, status="deleted", reason="deleted")
            removed = self._workers.pop(worker_id, worker)
        self._join_worker_threads(removed)
        self._delete_folder(removed)
        result = removed.public_dict()
        result["folder_deleted"] = not Path(removed.folder).exists()
        return result

    def pause_all(self, *, reason: str = "shutdown") -> None:
        with self._lock:
            for worker in list(self._workers.values()):
                if worker.status in {"starting", "running"}:
                    self._terminate_locked(worker, status="paused", reason=reason)

    def list_workers(self, *, include_logs: bool = True, workspace_path: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if workspace_path:
                self._restore_workspace_locked(workspace_path)
            self._refresh_locked()
            workspace = str(Path(workspace_path).expanduser().resolve()) if workspace_path else None
            return [
                w.public_dict(include_logs=include_logs)
                for w in sorted(self._workers.values(), key=lambda item: item.created_at, reverse=True)
                if w.status != "deleted" and (workspace is None or w.workspace_path == workspace)
            ]

    def get_worker(
        self,
        worker_id: str,
        *,
        include_logs: bool = True,
        workspace_path: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if workspace_path:
                self._restore_workspace_locked(workspace_path)
            self._refresh_locked()
            return self._require(worker_id).public_dict(include_logs=include_logs)

    def _ensure_service_package_locked(
        self,
        folder: Path,
        *,
        worker_id: str,
        name: str,
        description: str,
        manifest: dict[str, Any] | None = None,
        files: dict[str, str] | None = None,
        overwrite: bool,
    ) -> dict[str, Any]:
        folder = folder.resolve()
        folder.mkdir(parents=True, exist_ok=True)

        package_manifest = _default_manifest(worker_id=worker_id, name=name, description=description)
        manifest_path = folder / MANIFEST_FILENAME
        if manifest_path.exists():
            try:
                existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(existing_manifest, dict):
                    package_manifest.update(existing_manifest)
            except Exception:
                pass
        if manifest:
            package_manifest.update({key: value for key, value in manifest.items() if key not in {"id", "entrypoint"}})
        package_manifest["schema_version"] = SERVICE_PACKAGE_VERSION
        package_manifest["id"] = worker_id
        package_manifest["name"] = name or worker_id
        package_manifest["description"] = description or str(package_manifest.get("description") or "")
        package_manifest["entrypoint"] = ENTRYPOINT_FILENAME

        self._write_managed_package_file(
            folder,
            MANIFEST_FILENAME,
            json.dumps(package_manifest, ensure_ascii=False, indent=2) + "\n",
            overwrite=True,
        )
        self._write_managed_package_file(
            folder,
            README_FILENAME,
            _default_readme(worker_id=worker_id, name=name, description=description),
            overwrite=overwrite,
        )
        self._write_managed_package_file(
            folder,
            CONFIG_FILENAME,
            json.dumps(_default_config(worker_id=worker_id, name=name), ensure_ascii=False, indent=2) + "\n",
            overwrite=overwrite,
        )
        for rel_path, content in _default_src_files().items():
            self._write_package_file(folder, rel_path, content, overwrite=False)

        for rel_path, content in (files or {}).items():
            if not isinstance(content, str):
                raise ValueError(f"worker package file content must be string: {rel_path}")
            self._write_package_file(folder, str(rel_path), content, overwrite=True)

        return package_manifest

    def _write_package_file(self, folder: Path, rel_path: str, content: str, *, overwrite: bool) -> None:
        self._write_package_file_checked(folder, rel_path, content, overwrite=overwrite, allow_managed=False)

    def _write_managed_package_file(self, folder: Path, rel_path: str, content: str, *, overwrite: bool) -> None:
        self._write_package_file_checked(folder, rel_path, content, overwrite=overwrite, allow_managed=True)

    def _write_package_file_checked(
        self,
        folder: Path,
        rel_path: str,
        content: str,
        *,
        overwrite: bool,
        allow_managed: bool,
    ) -> None:
        rel = Path(str(rel_path))
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"worker package file path must stay inside worker folder: {rel_path}")
        normalized = rel.as_posix().lstrip("/")
        if not normalized:
            raise ValueError("worker package file path is empty")
        if not allow_managed and normalized in {
            ENTRYPOINT_FILENAME,
            HELPER_FILENAME,
            MANIFEST_FILENAME,
            "metadata.json",
            "stdout.log",
            "stderr.log",
        }:
            raise ValueError(f"worker package file is managed by OpenGIS and cannot be overridden via files: {normalized}")
        if normalized.startswith(".") or "/." in normalized:
            raise ValueError(f"hidden worker package files are not allowed: {normalized}")

        path = (folder / normalized).resolve()
        if folder != path and folder not in path.parents:
            raise ValueError(f"worker package file path escapes folder: {rel_path}")
        if path.exists() and not overwrite:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _launch_worker_locked(self, worker: ResidentWorker, *, reason: str) -> None:
        self._normalize_worker_entrypoint_locked(worker)
        workspace = Path(worker.workspace_path).expanduser().resolve()
        script_path = Path(worker.script_path)
        stdout_path = Path(worker.folder) / "stdout.log"
        stderr_path = Path(worker.folder) / "stderr.log"

        env = os.environ.copy()
        env["OPENGIS_WORKER_ID"] = worker.id
        env["OPENGIS_WORKSPACE"] = str(workspace)
        env["PYTHONUNBUFFERED"] = "1"

        process = subprocess.Popen(
            [sys.executable, "-u", str(script_path)],
            cwd=str(workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
        )

        worker.status = "running"
        worker.started_at = _now()
        worker.stopped_at = None
        worker.pid = process.pid
        worker.returncode = None
        worker.last_error = None
        worker.startup_checked_at = None
        worker.startup_check_state = None
        worker.startup_check_message = None
        worker.resource_cache = None
        worker.resource_sampled_at = 0.0
        worker.process = process
        worker.updated_at = _now()
        worker.logs.append({"ts": _now(), "stream": "system", "text": f"worker started: {reason}"})

        worker.stdout_thread = threading.Thread(
            target=self._pump_stream,
            args=(worker, process.stdout, "stdout", stdout_path),
            daemon=True,
            name=f"{worker.id}-stdout",
        )
        worker.stderr_thread = threading.Thread(
            target=self._pump_stream,
            args=(worker, process.stderr, "stderr", stderr_path),
            daemon=True,
            name=f"{worker.id}-stderr",
        )
        worker.stdout_thread.start()
        worker.stderr_thread.start()
        threading.Thread(target=self._watch_process, args=(worker, process), daemon=True, name=f"{worker.id}-watch").start()
        self._persist_runtime_state(worker)

    def _normalize_worker_entrypoint_locked(self, worker: ResidentWorker) -> None:
        folder = Path(worker.folder)
        main_path = _entrypoint_path(folder)
        legacy_path = _legacy_entrypoint_path(folder)

        if not main_path.exists() and legacy_path.exists():
            main_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
            try:
                legacy_path.unlink()
            except Exception as exc:
                worker.last_error = f"legacy entrypoint cleanup failed: {exc}"

        if Path(worker.script_path) != main_path:
            worker.script_path = str(main_path)
            worker.updated_at = _now()

    def _wait_initial_health(self, worker_id: str, *, timeout: float) -> None:
        deadline = _now() + max(0.0, timeout)
        saw_output = False
        while _now() < deadline:
            with self._lock:
                worker = self._workers.get(worker_id)
                if worker is None:
                    return
                self._refresh_locked()
                if worker.status not in {"starting", "running"}:
                    self._mark_startup_check_locked(worker)
                    return
                started_at = worker.started_at or 0
                saw_output = saw_output or any(
                    item.get("stream") in {"stdout", "stderr"}
                    and float(item.get("ts") or 0) >= started_at
                    for item in worker.logs
                )
            time.sleep(0.05)

        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                return
            self._refresh_locked()
            if worker.status not in {"starting", "running"}:
                self._mark_startup_check_locked(worker)
                return
            started_at = worker.started_at or 0
            has_worker_output = saw_output or any(
                item.get("stream") in {"stdout", "stderr"}
                and float(item.get("ts") or 0) >= started_at
                for item in worker.logs
            )
            if has_worker_output:
                worker.startup_check_state = "ok"
                worker.startup_check_message = "worker process is alive and produced output during startup check"
            else:
                worker.startup_check_state = "uncertain"
                worker.startup_check_message = (
                    "worker process is alive, but produced no output during startup check; "
                    "inspect logs or call get_worker before claiming it is healthy"
                )
            worker.startup_checked_at = _now()
            worker.updated_at = _now()
            self._persist_runtime_state(worker)

    def _mark_startup_check_locked(self, worker: ResidentWorker) -> None:
        health = worker._health_summary()
        worker.startup_check_state = health["state"]
        worker.startup_check_message = str(health.get("message") or worker.status)
        worker.startup_checked_at = _now()
        worker.updated_at = _now()
        self._persist_runtime_state(worker)

    def _require(self, worker_id: str) -> ResidentWorker:
        worker = self._workers.get(worker_id)
        if worker is None:
            raise KeyError(f"worker not found: {worker_id}")
        return worker

    def _restore_workspace_locked(self, workspace_path: str) -> set[str]:
        cleaned_deleted_ids: set[str] = set()
        workspace = Path(workspace_path).expanduser().resolve()
        worker_root = workspace / "worker"
        if not worker_root.exists() or not worker_root.is_dir():
            return cleaned_deleted_ids

        for meta_path in worker_root.glob("*/metadata.json"):
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
                worker_id = payload.get("id")
                if payload.get("status") == "deleted" and isinstance(worker_id, str) and worker_id:
                    worker = self._deleted_worker_from_metadata(payload, workspace, meta_path.parent)
                    if worker is not None:
                        self._delete_folder(worker)
                        cleaned_deleted_ids.add(worker_id)
                    continue
                worker = self._worker_from_metadata_locked(payload, workspace, meta_path.parent)
                if worker is None:
                    continue
                existing = self._workers.get(worker.id)
                if existing:
                    continue
                self._workers[worker.id] = worker
                self._persist_runtime_state(worker)
            except Exception:
                continue
        return cleaned_deleted_ids

    def _deleted_worker_from_metadata(
        self,
        payload: dict[str, Any],
        workspace: Path,
        folder: Path,
    ) -> ResidentWorker | None:
        worker_id = payload.get("id")
        if not isinstance(worker_id, str) or not worker_id:
            return None
        return ResidentWorker(
            id=worker_id,
            name=str(payload.get("name") or worker_id),
            description=str(payload.get("description") or ""),
            workspace_path=str(workspace),
            folder=str(folder),
            script_path=str(_entrypoint_path(folder)),
            status="deleted",
            created_at=float(payload.get("created_at") or _now()),
            updated_at=float(payload.get("updated_at") or _now()),
            last_error=payload.get("last_error") if isinstance(payload.get("last_error"), str) else None,
        )

    def _delete_workspace_worker_folder_by_id(self, workspace_path: str, worker_id: str) -> Path | None:
        workspace = Path(workspace_path).expanduser().resolve()
        worker_root = workspace / "worker"
        if not worker_root.exists() or not worker_root.is_dir():
            return None
        for meta_path in worker_root.glob("*/metadata.json"):
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if payload.get("id") != worker_id:
                continue
            worker = self._deleted_worker_from_metadata(payload, workspace, meta_path.parent)
            if worker is None:
                continue
            self._delete_folder(worker)
            return meta_path.parent
        return None

    def _worker_from_metadata_locked(
        self,
        payload: dict[str, Any],
        workspace: Path,
        folder: Path,
    ) -> ResidentWorker | None:
        worker_id = payload.get("id")
        if not isinstance(worker_id, str) or not worker_id:
            return None
        status = str(payload.get("status") or "paused")
        if status == "deleted":
            return None
        script_path = _entrypoint_path(folder)
        legacy_path = _legacy_entrypoint_path(folder)
        if not script_path.exists() and legacy_path.exists():
            try:
                script_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
                legacy_path.unlink()
            except Exception:
                return None
        if not script_path.exists():
            return None
        manifest = self._ensure_service_package_locked(
            folder,
            worker_id=worker_id,
            name=str(payload.get("name") or worker_id),
            description=str(payload.get("description") or ""),
            manifest=payload.get("manifest") if isinstance(payload.get("manifest"), dict) else None,
            files=None,
            overwrite=False,
        )

        restored_running = status in {"starting", "running"}
        if restored_running:
            status = "paused"

        logs = self._read_worker_logs(folder)
        if restored_running:
            logs.append({
                "ts": _now(),
                "stream": "system",
                "text": "worker restored after backend restart; process is paused until restarted",
            })
        startup_check = payload.get("startup_check")
        if not isinstance(startup_check, dict):
            startup_check = {}
        started_at = payload.get("started_at")
        stopped_at = payload.get("stopped_at")
        returncode = payload.get("returncode")

        worker = ResidentWorker(
            id=worker_id,
            name=str(payload.get("name") or worker_id),
            description=str(payload.get("description") or ""),
            workspace_path=str(workspace),
            folder=str(folder),
            script_path=str(script_path),
            status=status,
            created_at=float(payload.get("created_at") or _now()),
            updated_at=float(payload.get("updated_at") or _now()),
            started_at=started_at if isinstance(started_at, (int, float)) else None,
            stopped_at=stopped_at if isinstance(stopped_at, (int, float)) else (_now() if restored_running else None),
            startup_checked_at=startup_check.get("checked_at") if isinstance(startup_check.get("checked_at"), (int, float)) else None,
            startup_check_state=startup_check.get("state") if isinstance(startup_check.get("state"), str) else None,
            startup_check_message=startup_check.get("message") if isinstance(startup_check.get("message"), str) else None,
            pid=None,
            returncode=returncode if isinstance(returncode, int) else None,
            last_error=payload.get("last_error") if isinstance(payload.get("last_error"), str) else None,
            logs=logs,
            manifest=manifest,
        )
        if restored_running and not worker.last_error:
            worker.last_error = None
        return worker

    def _read_worker_logs(self, folder: Path) -> deque[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for name in ("stdout.log", "stderr.log"):
            path = folder / name
            if not path.exists():
                continue
            for line in _read_recent_log_lines(path, max_lines=MAX_LOG_LINES):
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    entries.append({
                        "ts": float(item.get("ts") or 0),
                        "stream": str(item.get("stream") or ("stderr" if name.startswith("stderr") else "stdout")),
                        "text": _compact_log_text(str(item.get("text") or "")),
                    })
        entries.sort(key=lambda item: float(item.get("ts") or 0))
        return deque(entries[-MAX_LOG_LINES:], maxlen=MAX_LOG_LINES)

    def _terminate_locked(self, worker: ResidentWorker, *, status: str, reason: str) -> None:
        proc = worker.process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    if os.name != "nt":
                        try:
                            os.kill(proc.pid, signal.SIGKILL)
                        except Exception:
                            pass
            except Exception as exc:
                worker.last_error = str(exc)
        worker.returncode = proc.poll() if proc else worker.returncode
        worker.status = status
        worker.stopped_at = _now()
        worker.pid = None
        worker.process = None
        worker.resource_cache = None
        worker.resource_sampled_at = 0.0
        worker.updated_at = _now()
        worker.logs.append({"ts": _now(), "stream": "system", "text": f"worker {status}: {reason}"})
        self._persist_runtime_state(worker)

    def _refresh_locked(self) -> None:
        remove_ids: list[str] = []
        for worker in self._workers.values():
            proc = worker.process
            if worker.status == "deleted":
                remove_ids.append(worker.id)
                continue
            if worker.status in {"starting", "running"}:
                self._normalize_worker_entrypoint_locked(worker)
                folder_exists = Path(worker.folder).exists()
                entrypoint_exists = Path(worker.script_path).exists()
                if not folder_exists or not entrypoint_exists:
                    self._terminate_locked(worker, status="deleted", reason="worker folder or main.py missing")
                    remove_ids.append(worker.id)
                    continue
            if not proc or worker.status not in {"starting", "running"}:
                continue
            rc = proc.poll()
            if rc is not None:
                worker.returncode = rc
                worker.status = "failed" if rc != 0 else "stopped"
                worker.stopped_at = worker.stopped_at or _now()
                worker.updated_at = _now()
                if rc != 0 and not worker.last_error:
                    worker.last_error = f"process exited with code {rc}"
                self._persist_runtime_state(worker)
        for worker_id in remove_ids:
            self._workers.pop(worker_id, None)

    def _watch_process(self, worker: ResidentWorker, proc: subprocess.Popen) -> None:
        rc = proc.wait()
        with self._lock:
            if worker.process is not proc:
                return
            if worker.status in {"paused", "deleted"}:
                return
            worker.returncode = rc
            worker.status = "failed" if rc != 0 else "stopped"
            worker.stopped_at = _now()
            worker.updated_at = _now()
            if rc != 0:
                worker.last_error = f"process exited with code {rc}"
            self._persist_runtime_state(worker)

    def _pump_stream(self, worker: ResidentWorker, stream: Any, stream_name: str, log_path: Path) -> None:
        if stream is None:
            return
        f = None
        try:
            f = open(log_path, "a", encoding="utf-8")
            for line in stream:
                text = line.rstrip("\n")
                if stream_name == "stdout":
                    self._maybe_emit_worker_event(worker, text)
                display_text = _compact_log_text(text)
                entry = {"ts": _now(), "stream": stream_name, "text": display_text}
                with self._lock:
                    worker.logs.append(entry)
                    worker.updated_at = _now()
                if f.tell() > MAX_LOG_FILE_BYTES:
                    f.close()
                    f = open(log_path, "w", encoding="utf-8")
                    rotation_entry = {
                        "ts": _now(),
                        "stream": "system",
                        "text": f"{stream_name} log rotated after reaching {MAX_LOG_FILE_BYTES} bytes",
                    }
                    f.write(json.dumps(rotation_entry, ensure_ascii=False) + "\n")
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
        finally:
            if f is not None:
                try:
                    f.close()
                except Exception:
                    pass
            try:
                stream.close()
            except Exception:
                pass

    def _write_metadata(self, worker: ResidentWorker, path: Path) -> None:
        payload = worker.public_dict(include_logs=False)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _persist_runtime_state(self, worker: ResidentWorker) -> None:
        try:
            folder = Path(worker.folder)
            self._write_metadata(worker, folder / "metadata.json")
        except Exception:
            pass

    def _join_worker_threads(self, worker: ResidentWorker, *, timeout: float = 1.0) -> None:
        for thread in (worker.stdout_thread, worker.stderr_thread):
            if thread and thread.is_alive():
                try:
                    thread.join(timeout=timeout)
                except RuntimeError:
                    pass

    def _delete_folder(self, worker: ResidentWorker) -> None:
        workspace = Path(worker.workspace_path).expanduser().resolve()
        folder = Path(worker.folder).expanduser().resolve()
        worker_root = (workspace / "worker").resolve()
        if folder == worker_root or worker_root not in folder.parents:
            raise RuntimeError(f"refusing to delete non-worker folder: {folder}")
        if not folder.exists():
            return
        last_error: Exception | None = None
        for _ in range(3):
            try:
                shutil.rmtree(folder)
                if not folder.exists():
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(0.15)
        message = f"delete folder failed: {last_error or f'folder still exists after deletion: {folder}'}"
        worker.last_error = message
        raise RuntimeError(message)

    def _maybe_emit_worker_event(self, worker: ResidentWorker, text: str) -> None:
        try:
            payload = json.loads(text)
        except Exception:
            return
        if not isinstance(payload, dict):
            return

        method: str | None = None
        params: dict[str, Any] | None = None
        if isinstance(payload.get("opengis_method"), str):
            method = str(payload.get("opengis_method"))
            raw_params = payload.get("params")
            params = raw_params if isinstance(raw_params, dict) else {}
        elif payload.get("opengis_event") == "dynamic_layer_update":
            method = "rpc.ui.map.dynamic_layer_update"
            params = {
                key: value
                for key, value in payload.items()
                if key not in {"opengis_event", "opengis_method"}
            }

        if not method or not method.startswith("rpc.ui."):
            return
        params = dict(params or {})
        params.setdefault("worker_id", worker.id)
        params.setdefault("worker_name", worker.name)
        params.setdefault("workspace_path", worker.workspace_path)
        params.setdefault("worker_started_at", worker.started_at)

        with self._lock:
            callbacks = list(self._event_callbacks)
        for callback in callbacks:
            try:
                callback(method, params)
            except Exception:
                pass


_WORKER_HELPER_CODE = '''"""OpenGIS resident worker helper API.

This file is generated next to each worker ``main.py`` and is importable from that
worker without installing any package:

    from opengis_worker import (
        emit_dynamic_layer_update,
        emit_dynamic_layer_diff,
        emit_dynamic_points,
        emit_dynamic_tracks,
        emit_moving_objects,
    )

The helper emits one compact JSON line to stdout. The OpenGIS worker manager
forwards that line to the frontend, where the map is updated.

Dynamic map protocol:
    1. Use a stable ``layer_id`` for the same live layer.
    2. Start with ``emit_dynamic_layer_update`` to send a full GeoJSON frame.
    3. For high-frequency changes, use ``emit_dynamic_layer_diff`` after the
       first full frame. Diff mode requires every feature to have a stable
       ``feature.id``. OpenGIS also accepts full GeoJSON Feature objects in
       ``diff["update"]`` and treats missing ids as upserts when possible.
    4. Use increasing ``sequence`` numbers; stale frames are ignored.
    5. For performance, pass ``bbox``, ``schema_changed=False``, and
       ``size_bytes`` when you know them.

High-level helpers:
    ``emit_dynamic_points``, ``emit_dynamic_tracks`` and
    ``emit_moving_objects`` send a full frame automatically the first time a
    layer id is used, then diff frames afterwards. Pass ``full=True`` to force
    a reset, or ``full=False`` only when you intentionally know the layer
    already exists.

Full-frame example:
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "vehicle-1",
                "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                "properties": {"speed": 35},
            }
        ],
    }
    emit_dynamic_layer_update(
        layer_id="live_vehicles",
        name="Live Vehicles",
        geojson=fc,
        bbox=[116.4, 39.9, 116.4, 39.9],
        style={"type": "circle", "paint": {"circle-color": "#22c55e", "circle-radius": 7}},
        sequence=1,
        schema_changed=True,
    )

Diff-frame example:
    emit_dynamic_layer_diff(
        layer_id="live_vehicles",
        diff={
            "remove": ["vehicle-2"],
            "add": [
                {
                    "type": "Feature",
                    "id": "vehicle-3",
                    "geometry": {"type": "Point", "coordinates": [116.5, 40.0]},
                    "properties": {"speed": 28},
                }
            ],
            "update": [
                {
                    "id": "vehicle-1",
                    "newGeometry": {"type": "Point", "coordinates": [116.41, 39.91]},
                    "addOrUpdateProperties": [{"key": "speed", "value": 36}],
                }
            ],
        },
        bbox=[116.4, 39.9, 116.5, 40.0],
        sequence=2,
        schema_changed=False,
    )
"""

from __future__ import annotations

import json
import sys

_EMITTED_FULL_LAYERS: set[str] = set()


def _feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _point_feature(
    feature_id: str,
    lon: float,
    lat: float,
    properties: dict | None = None,
) -> dict:
    props = dict(properties or {})
    props.setdefault("id", feature_id)
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


def _line_feature(
    feature_id: str,
    coordinates: list,
    properties: dict | None = None,
) -> dict:
    props = dict(properties or {})
    props.setdefault("id", feature_id)
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "properties": props,
    }


def _bbox_from_coordinates(coords: list) -> list[float] | None:
    flat: list[list[float]] = []

    def visit(value):
        if (
            isinstance(value, (list, tuple))
            and len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            flat.append([float(value[0]), float(value[1])])
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(coords)
    if not flat:
        return None
    xs = [item[0] for item in flat]
    ys = [item[1] for item in flat]
    return [min(xs), min(ys), max(xs), max(ys)]


def _should_emit_full(layer_id: str, full: bool | None) -> bool:
    if full is None:
        return layer_id not in _EMITTED_FULL_LAYERS
    return bool(full)


def _mark_full_if_needed(layer_id: str, emitted_full: bool) -> None:
    if emitted_full:
        _EMITTED_FULL_LAYERS.add(layer_id)


def emit(method: str, params: dict) -> None:
    """Emit a raw frontend RPC notification.

    Prefer ``emit_dynamic_layer_update`` / ``emit_dynamic_layer_diff`` for map
    rendering. Use this only when you intentionally need another ``rpc.ui.*``
    method.
    """
    print(
        json.dumps(
            {"opengis_method": method, "params": params},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def emit_dynamic_layer_update(
    *,
    layer_id: str,
    name: str,
    geojson: dict,
    bbox: list[float] | tuple[float, float, float, float] | None = None,
    style: dict | None = None,
    visible: bool = True,
    sequence: int | None = None,
    schema_changed: bool | None = None,
    size_bytes: int | None = None,
) -> None:
    """Emit a full GeoJSON frame for a dynamic map layer.

    Use this for the first frame, for low-frequency updates, or whenever the
    feature schema/geometry type changed. ``geojson`` should be a
    FeatureCollection. For later high-frequency updates, use
    ``emit_dynamic_layer_diff`` with stable feature ids.
    """
    payload = {
        "mode": "full",
        "layer_id": layer_id,
        "name": name,
        "geojson": geojson,
        "visible": visible,
    }
    if bbox is not None:
        payload["bbox"] = list(bbox)
    if style is not None:
        payload["style"] = style
    if sequence is not None:
        payload["sequence"] = sequence
    if schema_changed is not None:
        payload["schema_changed"] = schema_changed
    if size_bytes is not None:
        payload["size_bytes"] = size_bytes
    emit("rpc.ui.map.dynamic_layer_update", payload)


def emit_dynamic_layer_diff(
    *,
    layer_id: str,
    diff: dict,
    name: str | None = None,
    bbox: list[float] | tuple[float, float, float, float] | None = None,
    style: dict | None = None,
    visible: bool | None = None,
    sequence: int | None = None,
    schema_changed: bool | None = None,
    size_bytes: int | None = None,
) -> None:
    """Emit a MapLibre-style diff frame for a dynamic map layer.

    ``diff`` supports these keys:
      - ``removeAll``: bool, clears all features.
      - ``remove``: list of feature ids to delete.
      - ``add``: list of full GeoJSON Feature objects. Each must have ``id``.
      - ``update``: either MapLibre-style patch objects with ``id`` and
        optional ``newGeometry`` / property patch fields, or full GeoJSON
        Feature objects with stable ids. Full Feature updates replace existing
        features and upsert missing ids.

    Diff mode is only fast when every feature in the layer has a stable id.
    If ids are unavailable, emit a full frame instead.
    """
    payload = {
        "mode": "diff",
        "layer_id": layer_id,
        "diff": diff,
    }
    if name is not None:
        payload["name"] = name
    if bbox is not None:
        payload["bbox"] = list(bbox)
    if style is not None:
        payload["style"] = style
    if visible is not None:
        payload["visible"] = visible
    if sequence is not None:
        payload["sequence"] = sequence
    if schema_changed is not None:
        payload["schema_changed"] = schema_changed
    if size_bytes is not None:
        payload["size_bytes"] = size_bytes
    emit("rpc.ui.map.dynamic_layer_update", payload)


def emit_dynamic_points(
    *,
    layer_id: str,
    name: str,
    points: list[dict],
    sequence: int,
    full: bool | None = None,
    style: dict | None = None,
) -> None:
    """Emit moving point objects.

    Each point dict should contain ``id``, ``lon`` and ``lat`` plus optional
    ``properties``. By default, the first call for a layer id emits a full
    frame and later calls emit full-Feature diff updates.
    """
    features = [
        _point_feature(
            str(item["id"]),
            float(item["lon"]),
            float(item["lat"]),
            item.get("properties") if isinstance(item.get("properties"), dict) else {
                key: value for key, value in item.items() if key not in {"id", "lon", "lat"}
            },
        )
        for item in points
        if "id" in item and "lon" in item and "lat" in item
    ]
    bbox = _bbox_from_coordinates([feature["geometry"]["coordinates"] for feature in features])
    emit_full = _should_emit_full(layer_id, full)
    if emit_full:
        emit_dynamic_layer_update(
            layer_id=layer_id,
            name=name,
            geojson=_feature_collection(features),
            bbox=bbox,
            style=style,
            sequence=sequence,
            schema_changed=True,
        )
        _mark_full_if_needed(layer_id, True)
    else:
        emit_dynamic_layer_diff(
            layer_id=layer_id,
            name=name,
            diff={"update": features},
            bbox=bbox,
            style=style,
            sequence=sequence,
            schema_changed=False,
        )


def emit_dynamic_tracks(
    *,
    layer_id: str,
    name: str,
    tracks: dict[str, list],
    sequence: int,
    full: bool | None = None,
    max_track_points: int = 200,
    style: dict | None = None,
) -> None:
    """Emit trajectory LineString features keyed by moving object id.

    By default, the first call for a layer id emits a full frame and later
    calls emit full-Feature diff updates.
    """
    features = []
    for track_id, coords in tracks.items():
        trimmed = list(coords)[-max(2, int(max_track_points)):]
        if len(trimmed) < 2:
            continue
        features.append(_line_feature(str(track_id), trimmed, {"track_id": str(track_id)}))
    bbox = _bbox_from_coordinates([feature["geometry"]["coordinates"] for feature in features])
    emit_full = _should_emit_full(layer_id, full)
    if emit_full:
        emit_dynamic_layer_update(
            layer_id=layer_id,
            name=name,
            geojson=_feature_collection(features),
            bbox=bbox,
            style=style,
            sequence=sequence,
            schema_changed=True,
        )
        _mark_full_if_needed(layer_id, True)
    else:
        emit_dynamic_layer_diff(
            layer_id=layer_id,
            name=name,
            diff={"update": features},
            bbox=bbox,
            style=style,
            sequence=sequence,
            schema_changed=False,
        )


def emit_moving_objects(
    *,
    point_layer_id: str,
    track_layer_id: str,
    points: list[dict],
    tracks: dict[str, list],
    sequence: int,
    point_name: str = "Live Points",
    track_name: str = "Live Tracks",
    full: bool | None = None,
    max_track_points: int = 200,
    point_style: dict | None = None,
    track_style: dict | None = None,
) -> None:
    """Emit synchronized moving points and their trajectories.

    By default, each layer emits a full frame the first time it is used and
    diff frames afterwards.
    """
    emit_dynamic_tracks(
        layer_id=track_layer_id,
        name=track_name,
        tracks=tracks,
        sequence=sequence,
        full=full,
        max_track_points=max_track_points,
        style=track_style,
    )
    emit_dynamic_points(
        layer_id=point_layer_id,
        name=point_name,
        points=points,
        sequence=sequence,
        full=full,
        style=point_style,
    )
'''


_MANAGER = ResidentWorkerManager()


def get_worker_manager() -> ResidentWorkerManager:
    return _MANAGER
