from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from analysis_c2m.core import ALGORITHM, C2MContractError, build_c2m_artifact, effective_parameters


class BuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scanPath: str = Field(min_length=1)
    analysisMeshPath: str = Field(min_length=1)
    outputPath: str = Field(min_length=1)
    scanContentHash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    transform: list[float] = Field(min_length=16, max_length=16)
    parameters: dict[str, Any] = Field(default_factory=dict)
    algorithmId: str = ALGORITHM["id"]


def _root() -> Path:
    return Path(os.environ.get("ANALYSIS_C2M_STORAGE_ROOT", "/storage")).resolve(strict=False)


def _safe(value: str, exists: bool) -> Path:
    root = _root()
    raw = Path(value)
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(400, "path escapes shared storage root") from exc
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise HTTPException(400, "symlink paths are forbidden")
    if exists and not resolved.exists():
        raise HTTPException(400, "input path must exist")
    return resolved


def create_analysis_c2m_router(heavy_task: Callable[[str], Callable] | None = None) -> APIRouter:
    router = APIRouter(prefix="/analysis-c2m", tags=["analysis-c2m"])

    @router.get("/algorithms")
    def algorithms():
        return [{**ALGORITHM, "defaults": effective_parameters({})}]

    def build(request: BuildRequest):
        try:
            return build_c2m_artifact(
                _safe(request.scanPath, True),
                _safe(request.analysisMeshPath, True),
                _safe(request.outputPath, False),
                request.transform,
                request.parameters,
                request.algorithmId,
                scan_content_hash=request.scanContentHash,
            )
        except (C2MContractError, FileExistsError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    guarded_build = heavy_task("analysis-c2m-build")(build) if heavy_task else build

    def build_endpoint(request: BuildRequest):
        return guarded_build(request)

    router.add_api_route("/build", build_endpoint, methods=["POST"])
    return router


router = create_analysis_c2m_router()


def include_analysis_c2m_router(app: Any, heavy_task: Callable[[str], Callable]) -> None:
    app.include_router(create_analysis_c2m_router(heavy_task))
