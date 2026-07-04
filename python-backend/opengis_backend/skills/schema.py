"""Skill schema definition — defines the structure of a GIS Skill."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ParamType(str, Enum):
    """Parameter types for skill inputs."""
    FILE_PATH = "file_path"
    NUMBER = "number"
    STRING = "string"
    ENUM = "enum"
    BOOLEAN = "boolean"
    GEOMETRY = "geometry"
    CRS = "crs"
    LAYER_REF = "layer_ref"
    ARRAY = "array"
    OBJECT = "object"
    ANY = "any"


@dataclass
class SkillParam:
    """Definition of a single skill parameter."""
    name: str
    type: ParamType
    description: str
    required: bool = True
    default: Any = None
    options: list[str] | None = None  # For ENUM type
    min_value: float | None = None
    max_value: float | None = None

    def to_dict(self) -> dict:
        type_val = self.type.value if isinstance(self.type, ParamType) else str(self.type)
        result = {
            "name": self.name,
            "type": type_val,
            "description": self.description,
            "required": self.required,
        }
        if self.default is not None:
            result["default"] = self.default
        if self.options:
            result["options"] = self.options
        if self.min_value is not None:
            result["min_value"] = self.min_value
        if self.max_value is not None:
            result["max_value"] = self.max_value
        return result

    def to_json_schema(self) -> dict:
        """Convert to JSON Schema for OpenAI Function Calling."""
        type_map = {
            ParamType.FILE_PATH: "string",
            ParamType.NUMBER: "number",
            ParamType.STRING: "string",
            ParamType.ENUM: "string",
            ParamType.BOOLEAN: "boolean",
            ParamType.GEOMETRY: "string",
            ParamType.CRS: "string",
            ParamType.LAYER_REF: "string",
            ParamType.ARRAY: "array",
            ParamType.OBJECT: "object",
            ParamType.ANY: None,
        }
        # Also support string keys for type_map lookup
        str_type_map = {
            "file_path": "string",
            "number": "number",
            "string": "string",
            "enum": "string",
            "boolean": "boolean",
            "geometry": "string",
            "crs": "string",
            "layer_ref": "string",
            "array": "array",
            "object": "object",
            "any": None,
        }
        if isinstance(self.type, ParamType):
            json_type = type_map.get(self.type, "string")
        else:
            json_type = str_type_map.get(str(self.type), "string")
        schema: dict[str, Any] = {"description": self.description}
        if json_type is not None:
            schema["type"] = json_type
        if json_type == "array":
            schema["items"] = self._array_items_schema()
            if self.name == "bbox":
                schema["minItems"] = 4
                schema["maxItems"] = 4
        if json_type == "object":
            schema["additionalProperties"] = True
        if self.options:
            schema["enum"] = self.options
        if self.min_value is not None:
            schema["minimum"] = self.min_value
        if self.max_value is not None:
            schema["maximum"] = self.max_value
        if self.default is not None:
            schema["default"] = self.default
        return schema

    def _array_items_schema(self) -> dict:
        """Infer a useful item schema for common array parameters."""
        if self.name in {"tasks", "skill_groups"}:
            return {"type": "string"}
        if self.name in {"steps", "nodes", "edges"}:
            return {"type": "object", "additionalProperties": True}
        if self.name == "bbox":
            return {"type": "number"}
        return {}


@dataclass
class SkillSchema:
    """Complete definition of a GIS Skill."""
    name: str
    display_name: str
    description: str
    category: str  # vector | raster | statistics | conversion | visualization
    params: list[SkillParam]
    returns: str
    examples: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    group: str = "core"  # "core" = always-on, "qgis" = requires attach, etc.

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "params": [p.to_dict() for p in self.params],
            "returns": self.returns,
            "examples": self.examples,
            "tags": self.tags,
            "version": self.version,
            "group": self.group,
        }

    def to_openai_schema(self) -> dict:
        """Convert to OpenAI Function Calling tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        p.name: p.to_json_schema() for p in self.params
                    },
                    "required": [p.name for p in self.params if p.required],
                },
            },
        }
