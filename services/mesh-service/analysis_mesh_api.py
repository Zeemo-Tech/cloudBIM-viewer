"""HTTP boundary for component-aware analysis mesh artifacts."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from analysis_mesh import registry
from analysis_mesh.artifact import build_artifact
from analysis_mesh.contracts import ContractError
from analysis_mesh.loader import load_component_stream


class BuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modelPath: str = Field(min_length=1)
    metadataPath: str = Field(min_length=1)
    outputPath: str = Field(min_length=1)
    algorithmId: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    faceCap: int = Field(default=250000, ge=1, le=250000)


def _storage_root() -> Path:
    return Path(os.environ.get("ANALYSIS_MESH_STORAGE_ROOT", "/storage")).resolve(strict=False)


def _safe(path: str, *, must_exist: bool, directory: bool = False) -> Path:
    root = _storage_root()
    candidate = Path(path)
    if not candidate.is_absolute(): candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try: resolved.relative_to(root)
    except ValueError as exc: raise HTTPException(400, "path escapes shared storage root") from exc
    if must_exist and (not resolved.exists() or resolved.is_symlink() or (directory and not resolved.is_dir()) or (not directory and not resolved.is_file())):
        raise HTTPException(400, "input must be a regular file within shared storage")
    return resolved


def create_analysis_mesh_router(heavy_task: Callable[[str], Callable] | None = None) -> APIRouter:
    router = APIRouter(prefix="/analysis-mesh", tags=["analysis-mesh"])
    @router.get("/algorithms")
    def algorithms(): return registry.descriptors()

    def build(request: BuildRequest):
        try:
            model, metadata, destination = _safe(request.modelPath, must_exist=True), _safe(request.metadataPath, must_exist=True), _safe(request.outputPath, must_exist=False)
            builder = registry.get(request.algorithmId)
            effective = builder.descriptor.effective_parameters(request.parameters)
            stream = load_component_stream(model, metadata)
            with tempfile.TemporaryDirectory(prefix="analysis-mesh-") as workspace:
                result = builder.build(stream, effective, workspace)
                return build_artifact(result, destination, {"id": builder.descriptor.id, "implementationVersion": builder.descriptor.implementationVersion, "contractVersion": builder.descriptor.contractVersion, "effectiveParameters": effective}, face_cap=request.faceCap)
        except (ContractError, FileExistsError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
    guarded_build = heavy_task("analysis-mesh-build")(build) if heavy_task else build

    # Keep the FastAPI boundary in this module so postponed BuildRequest
    # annotations resolve here instead of in the injected decorator's module.
    def build_endpoint(request: BuildRequest):
        return guarded_build(request)

    router.add_api_route("/build", build_endpoint, methods=["POST"])
    return router


router = create_analysis_mesh_router()


def include_analysis_mesh_router(app: Any, heavy_task: Callable[[str], Callable]) -> None:
    app.include_router(create_analysis_mesh_router(heavy_task))
