"""Storage-confined upload-time point-cloud preprocessing HTTP boundary."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from analysis_mesh_api import _safe
from pointcloud_preprocess import build_preprocess


class PreprocessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sourcePath: str = Field(min_length=1)
    outputPath: str = Field(min_length=1)


def create_preprocess_router(heavy_task=None):
    router = APIRouter(prefix="/pointcloud-preprocess", tags=["pointcloud-preprocess"])

    def compute(request: PreprocessRequest):
        try:
            source = _safe(request.sourcePath, must_exist=True)
            output = _safe(request.outputPath, must_exist=False)
            return build_preprocess(source, output)
        except (FileExistsError, OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    guarded = heavy_task("pointcloud-preprocess")(compute) if heavy_task else compute

    @router.post("/compute")
    def endpoint(request: PreprocessRequest):
        return guarded(request)

    return router
