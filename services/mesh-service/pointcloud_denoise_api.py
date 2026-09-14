"""Storage-confined HTTP boundary; shares the process-wide heavy-task gate."""
from typing import Annotated
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from analysis_mesh_api import _safe
from pointcloud_denoise import build_denoise
from rebar_bim import _matrix


class DenoiseRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sourcePath: str
    ifcPath: str
    modelPath: str
    outputPath: str
    transform: list[Annotated[float, Field(allow_inf_nan=False)]] = Field(min_length=16, max_length=16)
    normalK: int = Field(default=32, ge=3, le=128, strict=True)


def create_denoise_router(heavy_task=None):
    router = APIRouter(prefix='/pointcloud-denoise', tags=['pointcloud-denoise'])

    def compute(request: DenoiseRequest):
        try:
            source, ifc, model = [_safe(p, must_exist=True) for p in (request.sourcePath, request.ifcPath, request.modelPath)]
            output = _safe(request.outputPath, must_exist=False)
            _matrix(request.transform)
            return build_denoise(source, ifc, model, request.transform, output, request.normalK)
        except (ValueError, FileExistsError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    guarded = heavy_task('pointcloud-denoise')(compute) if heavy_task else compute

    @router.post('/compute')
    def endpoint(request: DenoiseRequest):
        return guarded(request)

    return router
