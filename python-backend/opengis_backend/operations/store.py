"""OperationStore — project-level reusable operation capsules.

An Operation is intentionally stricter than a persisted script. It has a stable
directory, a machine-readable contract, a single entrypoint, dependency
metadata, and a standard JSON run protocol.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4


class OperationError(RuntimeError):
    """Raised for invalid operation definitions or failed operation runs."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-_")
    return text or f"operation-{uuid4().hex[:8]}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise OperationError(f"Invalid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise OperationError(f"Expected JSON object: {path}")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except Exception:
        return str(path.resolve())


@dataclass(frozen=True)
class OperationStore:
    workspace: Path

    @classmethod
    def from_workspace(cls, workspace_path: str | Path) -> "OperationStore":
        workspace = Path(workspace_path).expanduser().resolve()
        if not workspace.exists():
            raise OperationError(f"workspace_path does not exist: {workspace_path}")
        return cls(workspace=workspace)

    @property
    def root(self) -> Path:
        return self.workspace / ".opengis" / "operations"

    def list(self, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        needle = query.strip().lower()
        records: list[dict[str, Any]] = []
        for meta_path in sorted(self.root.glob("*/operation.json")):
            try:
                meta = self.load(meta_path.parent.name, include_readme=False, include_code=False)
            except Exception:
                continue
            haystack = " ".join(str(meta.get(k) or "") for k in ("id", "name", "description", "status")).lower()
            if needle and needle not in haystack:
                continue
            records.append(self._summary(meta))
            if len(records) >= max(1, min(200, int(limit or 50))):
                break
        return records

    def load(
        self,
        operation_id: str,
        *,
        include_readme: bool = True,
        include_code: bool = False,
        max_code_chars: int = 40000,
    ) -> dict[str, Any]:
        op_dir = self._operation_dir(operation_id)
        meta_path = op_dir / "operation.json"
        meta = _read_json(meta_path)
        self._validate_meta(meta, op_dir)
        enriched = dict(meta)
        enriched["path"] = _rel(self.workspace, op_dir)
        enriched["abs_path"] = str(op_dir)
        if include_readme:
            readme = op_dir / "README.md"
            enriched["readme"] = readme.read_text(encoding="utf-8", errors="replace") if readme.exists() else ""
        if include_code:
            entry = op_dir / str(meta.get("entry") or "main.py")
            content = entry.read_text(encoding="utf-8", errors="replace")
            limit = max(1000, int(max_code_chars or 40000))
            enriched["code"] = content[:limit]
            enriched["code_truncated"] = len(content) > limit
        return enriched

    def create(
        self,
        *,
        operation_id: str,
        name: str,
        description: str,
        code: str,
        input_schema: Optional[dict[str, Any]] = None,
        output_schema: Optional[dict[str, Any]] = None,
        dependencies: Optional[list[str]] = None,
        status: str = "draft",
        provenance: Optional[dict[str, Any]] = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        op_id = _slug(operation_id or name)
        op_dir = self.root / op_id
        if op_dir.exists() and not overwrite:
            raise OperationError(f"operation already exists: {op_id}")
        op_dir.mkdir(parents=True, exist_ok=True)
        (op_dir / "main.py").write_text(code, encoding="utf-8")
        deps = [str(item).strip() for item in dependencies or [] if str(item).strip()]
        if deps:
            (op_dir / "requirements.txt").write_text("\n".join(deps) + "\n", encoding="utf-8")
        readme = f"# {name or op_id}\n\n{description or 'Reusable OpenGIS operation.'}\n"
        (op_dir / "README.md").write_text(readme, encoding="utf-8")
        meta = self._default_meta(
            operation_id=op_id,
            name=name or op_id,
            description=description or "",
            input_schema=input_schema or self._default_input_schema(),
            output_schema=output_schema or self._default_output_schema(),
            dependencies=deps,
            status=status,
            provenance=provenance or {},
        )
        _write_json(op_dir / "operation.json", meta)
        (op_dir / "examples").mkdir(exist_ok=True)
        (op_dir / "runs").mkdir(exist_ok=True)
        return self.load(op_id, include_readme=True, include_code=False)

    def update(
        self,
        operation_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        code: Optional[str] = None,
        input_schema: Optional[dict[str, Any]] = None,
        output_schema: Optional[dict[str, Any]] = None,
        dependencies: Optional[list[str]] = None,
        readme: Optional[str] = None,
        status: Optional[str] = None,
    ) -> dict[str, Any]:
        op_dir = self._operation_dir(operation_id)
        meta_path = op_dir / "operation.json"
        meta = _read_json(meta_path)
        self._validate_meta(meta, op_dir)

        if code is not None:
            entry = op_dir / str(meta.get("entry") or "main.py")
            entry.write_text(str(code), encoding="utf-8")
        if name is not None:
            meta["name"] = str(name)
        if description is not None:
            meta["description"] = str(description)
        if input_schema is not None:
            meta["input_schema"] = input_schema
        if output_schema is not None:
            meta["output_schema"] = output_schema
        if dependencies is not None:
            deps = [str(item).strip() for item in dependencies if str(item).strip()]
            runtime = dict(meta.get("runtime") or {})
            runtime["dependencies"] = deps
            meta["runtime"] = runtime
            requirements = op_dir / "requirements.txt"
            if deps:
                requirements.write_text("\n".join(deps) + "\n", encoding="utf-8")
            elif requirements.exists():
                requirements.unlink()
        if readme is not None:
            (op_dir / "README.md").write_text(str(readme), encoding="utf-8")
        if status is not None:
            meta["status"] = str(status)

        meta["revision"] = int(meta.get("revision") or 0) + 1
        meta["updated_at"] = _now()
        _write_json(meta_path, meta)
        return self.load(operation_id, include_readme=True, include_code=False)

    def promote_script(
        self,
        *,
        script_path: str,
        operation_id: str = "",
        name: str = "",
        description: str = "",
        input_schema: Optional[dict[str, Any]] = None,
        output_schema: Optional[dict[str, Any]] = None,
        dependencies: Optional[list[str]] = None,
        run_id: Optional[str] = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        src = self._resolve_workspace_file(script_path)
        if src.suffix.lower() != ".py":
            raise OperationError(f"script_path must point to a .py file: {script_path}")
        code = src.read_text(encoding="utf-8", errors="replace")
        op_id = operation_id or src.stem
        meta = src.with_suffix(".metadata.json")
        script_meta: dict[str, Any] = {}
        if meta.exists():
            try:
                script_meta = _read_json(meta)
            except Exception:
                script_meta = {}
        provenance = {
            "created_by": "agent",
            "created_from_script": _rel(self.workspace, src),
            "created_from_run": run_id or script_meta.get("run_id"),
            "created_at": _now(),
        }
        return self.create(
            operation_id=op_id,
            name=name or script_meta.get("semantic_name") or src.stem,
            description=description or script_meta.get("description") or "",
            code=code,
            input_schema=input_schema,
            output_schema=output_schema,
            dependencies=dependencies,
            provenance=provenance,
            overwrite=overwrite,
        )

    def run(
        self,
        operation_id: str,
        params: dict[str, Any],
        *,
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        meta = self.load(operation_id, include_readme=False, include_code=False)
        op_dir = Path(str(meta["abs_path"]))
        entry = op_dir / str(meta.get("entry") or "main.py")
        self._validate_params(meta.get("input_schema") or {}, params)
        run_id = f"oprun_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        run_dir = op_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        input_path = run_dir / "input.json"
        output_path = run_dir / "output.json"
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        _write_json(input_path, {"workspace": str(self.workspace), "operation_id": meta["id"], "params": params})

        started = _now()
        proc = subprocess.run(
            [sys.executable, str(entry), "--input", str(input_path), "--output", str(output_path)],
            cwd=str(op_dir),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_seconds or 600)),
        )
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8")
        finished = _now()

        output: dict[str, Any] = {}
        if output_path.exists():
            output = _read_json(output_path)
        success = proc.returncode == 0 and bool(output.get("success", True))
        run_record = {
            "run_id": run_id,
            "operation_id": meta["id"],
            "status": "success" if success else "failed",
            "returncode": proc.returncode,
            "started_at": started,
            "finished_at": finished,
            "input_path": _rel(self.workspace, input_path),
            "output_path": _rel(self.workspace, output_path) if output_path.exists() else None,
            "stdout_path": _rel(self.workspace, stdout_path),
            "stderr_path": _rel(self.workspace, stderr_path),
            "output": output,
        }
        _write_json(run_dir / "run.json", run_record)
        self._record_run(meta["id"], run_record)
        if not success:
            raise OperationError(
                f"operation '{meta['id']}' failed with return code {proc.returncode}: "
                f"{(proc.stderr or output.get('error') or '').strip()[:1000]}"
            )
        return run_record

    def _record_run(self, operation_id: str, run_record: dict[str, Any]) -> None:
        op_dir = self._operation_dir(operation_id)
        meta_path = op_dir / "operation.json"
        meta = _read_json(meta_path)
        provenance = dict(meta.get("provenance") or {})
        provenance["last_run"] = run_record["run_id"]
        if run_record["status"] == "success":
            provenance["last_success_run"] = run_record["run_id"]
            if meta.get("status") == "draft":
                meta["status"] = "validated"
        meta["provenance"] = provenance
        meta["updated_at"] = _now()
        _write_json(meta_path, meta)

    def _operation_dir(self, operation_id: str) -> Path:
        op_id = _slug(operation_id)
        op_dir = (self.root / op_id).resolve()
        root = self.root.resolve()
        if root != op_dir and root not in op_dir.parents:
            raise OperationError(f"operation_id resolves outside operation root: {operation_id}")
        return op_dir

    def _resolve_workspace_file(self, raw_path: str) -> Path:
        raw = Path(raw_path).expanduser()
        path = raw.resolve() if raw.is_absolute() else (self.workspace / raw).resolve()
        if self.workspace != path and self.workspace not in path.parents:
            raise OperationError(f"path must be inside workspace: {raw_path}")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"file not found: {raw_path}")
        return path

    def _summary(self, meta: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": meta.get("id"),
            "name": meta.get("name"),
            "version": meta.get("version"),
            "status": meta.get("status"),
            "description": meta.get("description"),
            "entry": meta.get("entry"),
            "dependencies": (meta.get("runtime") or {}).get("dependencies", []),
            "last_success_run": (meta.get("provenance") or {}).get("last_success_run"),
            "updated_at": meta.get("updated_at"),
        }

    def _validate_meta(self, meta: dict[str, Any], op_dir: Path) -> None:
        for key in ("id", "name", "entry", "input_schema", "output_schema"):
            if key not in meta:
                raise OperationError(f"operation.json missing required key '{key}' in {op_dir}")
        entry = op_dir / str(meta.get("entry"))
        if not entry.exists() or not entry.is_file():
            raise OperationError(f"operation entry not found: {entry}")

    def _validate_params(self, schema: dict[str, Any], params: dict[str, Any]) -> None:
        if not isinstance(params, dict):
            raise OperationError("operation params must be a JSON object")
        required = schema.get("required") or []
        if isinstance(required, list):
            missing = [str(key) for key in required if str(key) not in params]
            if missing:
                raise OperationError(f"operation params missing required keys: {', '.join(missing)}")

    def _default_meta(
        self,
        *,
        operation_id: str,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any],
        dependencies: list[str],
        status: str,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        created_at = provenance.get("created_at") or _now()
        return {
            "schema_version": "1.0",
            "id": operation_id,
            "name": name,
            "version": "0.1.0",
            "revision": 1,
            "status": status,
            "description": description,
            "entry": "main.py",
            "runtime": {
                "language": "python",
                "python": f">={sys.version_info.major}.{sys.version_info.minor}",
                "dependencies": dependencies,
            },
            "input_schema": input_schema,
            "output_schema": output_schema,
            "tunable_parameters": {},
            "validation": {"input_checks": [], "output_checks": []},
            "provenance": {**provenance, "created_at": created_at},
            "created_at": created_at,
            "updated_at": created_at,
        }

    def _default_input_schema(self) -> dict[str, Any]:
        return {"type": "object", "required": [], "properties": {}}

    def _default_output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "artifacts": {"type": "array"},
                "layers": {"type": "array"},
                "metrics": {"type": "object"},
                "summary": {"type": "string"},
            },
        }
