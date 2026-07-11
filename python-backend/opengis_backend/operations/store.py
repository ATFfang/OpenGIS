"""OperationStore — reusable operation capsules.

An Operation is intentionally stricter than a persisted script. It has a stable
directory, a machine-readable contract, a single entrypoint, dependency
metadata, and a standard JSON run protocol.

Operations are resolved from two scopes:

* ``workspace``: project-local operations under ``.opengis/operations``. These
  are mutable and are where newly created/promoted operations are stored.
* ``builtin``: OpenGIS-shipped operations bundled with the backend package.
  These are read-only software capabilities shared by every workspace.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import ast
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


class _ParamsUsageVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.param_aliases: set[str] = {"params"}
        self.required: set[str] = set()
        self.optional: set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> Any:
        key = _constant_subscript_key(node.value)
        if key == "params":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.param_aliases.add(target.id)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        if isinstance(node.value, ast.Name) and node.value.id in self.param_aliases:
            key = _slice_constant_string(node.slice)
            if key:
                self.required.add(key)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Name)
            and func.value.id in self.param_aliases
            and node.args
        ):
            key = _literal_string(node.args[0])
            if key:
                self.optional.add(key)
        self.generic_visit(node)


def _literal_string(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _slice_constant_string(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _constant_subscript_key(node: ast.AST) -> str:
    if isinstance(node, ast.Subscript):
        return _slice_constant_string(node.slice)
    return ""


def _inspect_operation_code(code: str) -> dict[str, Any]:
    flags = {flag for flag in ("--input", "--output") if flag in code}
    visitor = _ParamsUsageVisitor()
    syntax_error = ""
    try:
        tree = ast.parse(code)
        visitor.visit(tree)
    except SyntaxError as exc:
        syntax_error = f"{exc.msg} at line {exc.lineno}"
    return {
        "protocol_flags": flags,
        "required_params_from_code": visitor.required,
        "optional_params_from_code": visitor.optional - visitor.required,
        "syntax_error": syntax_error,
    }


@dataclass(frozen=True)
class OperationLocation:
    operation_id: str
    scope: str
    root: Path
    directory: Path
    read_only: bool


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

    @property
    def builtin_root(self) -> Path:
        return Path(__file__).resolve().parent / "builtin"

    @property
    def run_root(self) -> Path:
        return self.workspace / ".opengis" / "operation-runs"

    @property
    def roots(self) -> dict[str, str]:
        return {
            "workspace": str(self.root),
            "builtin": str(self.builtin_root),
            "runs": str(self.run_root),
        }

    def list(self, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
        needle = query.strip().lower()
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        max_records = max(1, min(200, int(limit or 50)))
        for scope, root in (("workspace", self.root), ("builtin", self.builtin_root)):
            if not root.exists():
                continue
            for meta_path in sorted(root.glob("*/operation.json")):
                op_id = _slug(meta_path.parent.name)
                if op_id in seen:
                    continue
                try:
                    meta = self._load_from_location(
                        OperationLocation(
                            operation_id=op_id,
                            scope=scope,
                            root=root,
                            directory=meta_path.parent.resolve(),
                            read_only=scope != "workspace",
                        ),
                        include_readme=False,
                        include_code=False,
                    )
                except Exception:
                    continue
                haystack = " ".join(str(meta.get(k) or "") for k in ("id", "name", "description", "status", "scope")).lower()
                if needle and needle not in haystack:
                    continue
                records.append(self._summary(meta))
                seen.add(op_id)
                if len(records) >= max_records:
                    return records
        return records

    def load(
        self,
        operation_id: str,
        *,
        include_readme: bool = True,
        include_code: bool = False,
        max_code_chars: int = 40000,
    ) -> dict[str, Any]:
        return self._load_from_location(
            self._operation_location(operation_id),
            include_readme=include_readme,
            include_code=include_code,
            max_code_chars=max_code_chars,
        )

    def _load_from_location(
        self,
        location: OperationLocation,
        *,
        include_readme: bool = True,
        include_code: bool = False,
        max_code_chars: int = 40000,
    ) -> dict[str, Any]:
        op_dir = location.directory
        meta_path = op_dir / "operation.json"
        meta = _read_json(meta_path)
        self._validate_meta(meta, op_dir)
        enriched = dict(meta)
        enriched["scope"] = location.scope
        enriched["read_only"] = location.read_only
        enriched["source_root"] = str(location.root)
        enriched["path"] = _rel(self.workspace, op_dir) if location.scope == "workspace" else f"builtin://{meta.get('id') or location.operation_id}"
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
        location = self._operation_location(operation_id)
        if location.read_only:
            raise OperationError(
                f"operation '{operation_id}' is builtin/read-only; copy it to the workspace before editing"
            )
        op_dir = location.directory
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

    def copy_to_workspace(
        self,
        operation_id: str,
        *,
        overwrite: bool = False,
        status: str = "draft",
    ) -> dict[str, Any]:
        location = self._operation_location(operation_id)
        target = self.root / location.operation_id
        if location.scope == "workspace":
            return self.load(operation_id, include_readme=True, include_code=False)
        if target.exists():
            if not overwrite:
                raise OperationError(f"workspace operation already exists: {location.operation_id}")
            shutil.rmtree(target)
        shutil.copytree(
            location.directory,
            target,
            ignore=shutil.ignore_patterns("runs", "__pycache__", "*.pyc"),
        )
        meta_path = target / "operation.json"
        meta = _read_json(meta_path)
        provenance = dict(meta.get("provenance") or {})
        provenance.update({
            "copied_from_scope": location.scope,
            "copied_from_path": str(location.directory),
            "copied_at": _now(),
        })
        meta["provenance"] = provenance
        meta["status"] = str(status or "draft")
        meta["revision"] = int(meta.get("revision") or 0) + 1
        meta["updated_at"] = _now()
        _write_json(meta_path, meta)
        return self.load(location.operation_id, include_readme=True, include_code=False)

    def run(
        self,
        operation_id: str,
        params: dict[str, Any],
        *,
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        location = self._operation_location(operation_id)
        meta = self._load_from_location(location, include_readme=False, include_code=False)
        op_dir = location.directory
        entry = op_dir / str(meta.get("entry") or "main.py")
        validation = self.validate_contract(operation_id, params=params)
        if not validation["ok"]:
            errors = "; ".join(str(item.get("message") or item) for item in validation["errors"])
            raise OperationError(f"operation contract validation failed: {errors}")
        run_id = f"oprun_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        run_dir = self._run_dir_for(location, run_id)
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
            "scope": location.scope,
            "input_path": _rel(self.workspace, input_path),
            "output_path": _rel(self.workspace, output_path) if output_path.exists() else None,
            "stdout_path": _rel(self.workspace, stdout_path),
            "stderr_path": _rel(self.workspace, stderr_path),
            "output": output,
        }
        _write_json(run_dir / "run.json", run_record)
        self._record_run(location, run_record)
        if not success:
            raise OperationError(
                f"operation '{meta['id']}' failed with return code {proc.returncode}: "
                f"{(proc.stderr or output.get('error') or '').strip()[:1000]}"
            )
        return run_record

    def validate_contract(
        self,
        operation_id: str,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Validate operation metadata/code contract before execution.

        This catches common reusable-operation drift before launching a
        subprocess: missing standard CLI protocol, schema required keys missing
        from params, and code reading ``params["x"]`` that the schema/call does
        not provide.
        """
        location = self._operation_location(operation_id)
        meta = self._load_from_location(location, include_readme=False, include_code=False)
        op_dir = location.directory
        entry = op_dir / str(meta.get("entry") or "main.py")
        code = entry.read_text(encoding="utf-8", errors="replace")
        input_schema = meta.get("input_schema") if isinstance(meta.get("input_schema"), dict) else {}
        schema_required = [
            str(item)
            for item in (input_schema.get("required") or [])
            if str(item)
        ]
        properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
        schema_properties = {str(key) for key in properties.keys()}
        provided = set(params.keys()) if isinstance(params, dict) else set()
        code_contract = _inspect_operation_code(code)

        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        if params is not None and not isinstance(params, dict):
            errors.append({
                "code": "params_not_object",
                "message": "operation params must be a JSON object",
            })

        for flag in ("--input", "--output"):
            if flag not in code_contract["protocol_flags"]:
                errors.append({
                    "code": "missing_standard_cli_flag",
                    "message": f"main.py does not declare standard {flag} argument",
                    "flag": flag,
                })

        missing_schema_required = [key for key in schema_required if key not in provided]
        if params is not None and missing_schema_required:
            errors.append({
                "code": "missing_required_params",
                "message": "operation params missing required schema keys: " + ", ".join(missing_schema_required),
                "keys": missing_schema_required,
            })

        direct_params = set(code_contract["required_params_from_code"])
        undeclared_code_params = sorted(key for key in direct_params if key not in schema_required and key not in schema_properties)
        if undeclared_code_params:
            warnings.append({
                "code": "code_params_not_declared_in_schema",
                "message": "main.py directly reads params keys that input_schema does not declare: " + ", ".join(undeclared_code_params),
                "keys": undeclared_code_params,
            })

        if params is not None:
            missing_code_params = sorted(key for key in direct_params if key not in provided)
            if missing_code_params:
                errors.append({
                    "code": "missing_code_required_params",
                    "message": "main.py directly reads params keys missing from this run: " + ", ".join(missing_code_params),
                    "keys": missing_code_params,
                    "repair": "Add these keys to run_operation params or repair operation.input_schema/main.py with edit_operation.",
                })

        optional_undeclared = sorted(
            key
            for key in code_contract["optional_params_from_code"]
            if key not in schema_required and key not in schema_properties
        )
        if optional_undeclared:
            warnings.append({
                "code": "optional_code_params_not_declared_in_schema",
                "message": "main.py uses params.get keys not declared in input_schema: " + ", ".join(optional_undeclared),
                "keys": optional_undeclared,
            })

        if code_contract.get("syntax_error"):
            errors.append({
                "code": "main_py_syntax_error",
                "message": str(code_contract["syntax_error"]),
            })

        return {
            "success": True,
            "ok": not errors,
            "operation_id": meta["id"],
            "errors": errors,
            "warnings": warnings,
            "contract": {
                "entry": _rel(self.workspace, entry) if location.scope == "workspace" else str(entry),
                "schema_required": schema_required,
                "schema_properties": sorted(schema_properties),
                "provided_params": sorted(provided),
                "required_params_from_code": sorted(direct_params),
                "optional_params_from_code": sorted(code_contract["optional_params_from_code"]),
                "undeclared_code_params": undeclared_code_params,
                "protocol_flags": sorted(code_contract["protocol_flags"]),
            },
        }

    def _record_run(self, location: OperationLocation, run_record: dict[str, Any]) -> None:
        if location.read_only:
            self._record_builtin_run(location, run_record)
            return
        op_dir = location.directory
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

    def _record_builtin_run(self, location: OperationLocation, run_record: dict[str, Any]) -> None:
        index_path = self.run_root / location.operation_id / "latest.json"
        payload = {
            "operation_id": location.operation_id,
            "scope": location.scope,
            "last_run": run_record["run_id"],
            "last_success_run": run_record["run_id"] if run_record["status"] == "success" else None,
            "updated_at": _now(),
        }
        _write_json(index_path, payload)

    def _operation_location(self, operation_id: str) -> OperationLocation:
        op_id = _slug(operation_id)
        for scope, root in (("workspace", self.root), ("builtin", self.builtin_root)):
            resolved_root = root.resolve()
            op_dir = (resolved_root / op_id).resolve()
            if resolved_root != op_dir and resolved_root not in op_dir.parents:
                raise OperationError(f"operation_id resolves outside operation root: {operation_id}")
            if (op_dir / "operation.json").exists():
                return OperationLocation(
                    operation_id=op_id,
                    scope=scope,
                    root=resolved_root,
                    directory=op_dir,
                    read_only=scope != "workspace",
                )
        raise FileNotFoundError(f"operation not found: {operation_id}")

    def _operation_dir(self, operation_id: str) -> Path:
        return self._operation_location(operation_id).directory

    def _run_dir_for(self, location: OperationLocation, run_id: str) -> Path:
        if location.scope == "workspace":
            return location.directory / "runs" / run_id
        return self.run_root / location.operation_id / run_id

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
            "scope": meta.get("scope") or "workspace",
            "read_only": bool(meta.get("read_only")),
            "description": meta.get("description"),
            "entry": meta.get("entry"),
            "dependencies": (meta.get("runtime") or {}).get("dependencies", []),
            "last_success_run": (meta.get("provenance") or {}).get("last_success_run"),
            "updated_at": meta.get("updated_at"),
            "path": meta.get("path"),
            "abs_path": meta.get("abs_path"),
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
