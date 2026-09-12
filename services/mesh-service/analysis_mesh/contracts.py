"""Stable contracts for component-aware analysis meshes."""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import hashlib
from typing import Any, Protocol

import numpy as np


class ContractError(ValueError):
    pass


def normalize_component_tree(value: Any, component_ids: set[str]) -> dict[str, Any]:
    """Return a strict, complete IFC tree for the component contract."""
    if value in ({}, None):
        return {"id": "root", "name": "IFC model", "type": "IfcProject", "children": [
            {"id": global_id, "children": []} for global_id in sorted(component_ids)
        ]}
    if not isinstance(value, dict):
        raise ContractError("metadata tree must be an object")
    tree = deepcopy(value)
    seen: set[str] = set()

    def visit(node: Any) -> None:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"].strip():
            raise ContractError("metadata tree node must have a non-empty id")
        if node["id"] in seen:
            raise ContractError(f"metadata tree has duplicate id {node['id']!r}")
        seen.add(node["id"])
        if "children" not in node:
            node["children"] = []
        children = node.get("children")
        if not isinstance(children, list):
            raise ContractError("metadata tree node children must be an array")
        for child in children:
            visit(child)

    visit(tree)
    tree["children"].extend(
        {"id": global_id, "children": []}
        for global_id in sorted(component_ids - seen)
    )
    return tree


def mesh_position_hash(mesh: Any) -> str:
    """Hash the exact float32 position stream consumed by the web renderer."""
    positions = np.ascontiguousarray(np.asarray(mesh.vertices, dtype="<f4"))
    return hashlib.sha256(positions.tobytes()).hexdigest()


@dataclass(frozen=True)
class AlgorithmDescriptor:
    id: str
    label: str
    implementationVersion: str
    contractVersion: str
    capabilities: tuple[str, ...]
    parameterSchema: dict[str, dict[str, Any]]
    defaults: dict[str, Any]

    def effective_parameters(self, supplied: dict[str, Any] | None) -> dict[str, Any]:
        supplied = supplied or {}
        unknown = set(supplied) - set(self.parameterSchema)
        if unknown:
            raise ContractError("unknown algorithm parameters: " + ", ".join(sorted(unknown)))
        result = dict(self.defaults)
        for name, value in supplied.items():
            schema = self.parameterSchema[name]
            kind = schema.get("type")
            if kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ContractError(f"{name} must be a number")
            if kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
                raise ContractError(f"{name} must be an integer")
            if kind == "boolean" and not isinstance(value, bool):
                raise ContractError(f"{name} must be a boolean")
            if "minimum" in schema and value < schema["minimum"]:
                raise ContractError(f"{name} is below minimum")
            if "maximum" in schema and value > schema["maximum"]:
                raise ContractError(f"{name} is above maximum")
            result[name] = value
        return result

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "implementationVersion": self.implementationVersion,
                "contractVersion": self.contractVersion, "capabilities": list(self.capabilities),
                "parameterSchema": self.parameterSchema, "defaults": self.defaults}


class ComponentBuilder(Protocol):
    descriptor: AlgorithmDescriptor
    def build(self, stream: "ComponentMeshStream", effectiveParameters: dict[str, Any], workspace: str) -> "ComponentMeshStream": ...


@dataclass
class MeshPart:
    part_id: str
    node_name: str
    mesh: Any
    transform: Any
    position_hash: str
    source_quality: dict[str, Any] | None = None
    remesh_diagnostics: dict[str, Any] | None = None


@dataclass
class Component:
    global_id: str
    parts: list[MeshPart] = field(default_factory=list)


@dataclass
class ComponentMeshStream:
    components: list[Component]
    tree: dict[str, Any]
    source_bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None


class AlgorithmRegistry:
    def __init__(self) -> None:
        self._builders: dict[str, ComponentBuilder] = {}

    def register(self, builder: ComponentBuilder) -> None:
        algorithm_id = builder.descriptor.id
        if algorithm_id in self._builders:
            raise ContractError(f"duplicate algorithm id: {algorithm_id}")
        self._builders[algorithm_id] = builder

    def get(self, algorithm_id: str) -> ComponentBuilder:
        try:
            return self._builders[algorithm_id]
        except KeyError as exc:
            raise ContractError(f"unknown algorithm id: {algorithm_id}") from exc

    def descriptors(self) -> list[dict[str, Any]]:
        return [b.descriptor.public() for b in self._builders.values()]
