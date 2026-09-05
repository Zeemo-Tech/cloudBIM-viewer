"""Load a GLB as a hierarchy of IFC-addressable component parts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from .contracts import Component, ComponentMeshStream, ContractError, MeshPart, mesh_position_hash, normalize_component_tree


def _global_ids(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = {str(v) for k, v in value.items() if k.lower().replace("_", "") == "globalid" and isinstance(v, (str, int))}
        for v in value.values(): found |= _global_ids(v)
        return found
    if isinstance(value, list):
        return set().union(*(_global_ids(v) for v in value)) if value else set()
    return set()


def _node_mapping(metadata: Any) -> dict[str, str]:
    """Map GLB node names to IFC GlobalIds across supported metadata layouts.

    CloudBIM's canonical metadata stores elements in an object keyed by GlobalId,
    while IfcConvert names the corresponding GLB node with that same key.  Older
    imports may instead carry an explicit ``nodeName`` next to ``GlobalId``.
    """
    mapping: dict[str, str] = {}
    known_ids = _global_ids(metadata)
    if isinstance(metadata, dict):
        elements = metadata.get("elements")
        if isinstance(elements, dict):
            for key, value in elements.items():
                global_id = str(value.get("GlobalId") or value.get("globalId") or value.get("id") or key) if isinstance(value, dict) else str(key)
                known_ids.add(global_id)
                mapping[str(key)] = global_id
                if isinstance(value, dict):
                    explicit = value.get("nodeName") or value.get("node_name")
                    if explicit:
                        mapping[str(explicit)] = global_id

    def visit(value: Any, hint: str | None = None) -> None:
        if isinstance(value, dict):
            node = value.get("nodeName") or value.get("node_name")
            gids = _global_ids(value)
            if node and len(gids) == 1: mapping[str(node)] = next(iter(gids))
            for k, v in value.items(): visit(v, str(k))
        elif isinstance(value, list):
            for v in value: visit(v)
    visit(metadata)
    for global_id in known_ids:
        mapping.setdefault(global_id, global_id)
    return mapping


def load_component_stream(model_path: str | Path, metadata_path: str | Path) -> ComponentMeshStream:
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    mapping = _node_mapping(metadata)
    loaded = trimesh.load(str(model_path), force="scene", process=False)
    if not isinstance(loaded, trimesh.Scene):
        raise ContractError("model must be a GLB scene")
    components: dict[str, Component] = {}
    source_tree = metadata.get("tree", {}) if isinstance(metadata, dict) else {}
    source_low: np.ndarray | None = None
    source_high: np.ndarray | None = None
    for node_name in loaded.graph.nodes_geometry:
        transform, geometry_name = loaded.graph.get(node_name)
        global_id = mapping.get(str(node_name))
        if not global_id:
            raise ContractError(f"geometry node {node_name!r} has no metadata GlobalId mapping")
        geometry = loaded.geometry[geometry_name].copy()
        geometry.apply_transform(transform)
        low, high = geometry.bounds
        source_low = low.copy() if source_low is None else np.minimum(source_low, low)
        source_high = high.copy() if source_high is None else np.maximum(source_high, high)
        component = components.setdefault(global_id, Component(global_id))
        component.parts.append(MeshPart(f"{global_id}:{node_name}", str(node_name), geometry, transform, mesh_position_hash(geometry)))
    if not components:
        raise ContractError("model has no IFC-addressable geometry nodes")
    assert source_low is not None and source_high is not None
    tree = normalize_component_tree(source_tree, set(components))
    return ComponentMeshStream(
        list(components.values()),
        tree,
        (tuple(map(float, source_low)), tuple(map(float, source_high))),
    )
