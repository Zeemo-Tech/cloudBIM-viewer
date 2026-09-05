"""FastAPI contract for the training-free geometric rebar PoC.

``main.py`` can register this module without creating a second concurrency
mechanism::

    from rebar_api import include_rebar_router

    include_rebar_router(app, heavy_task=_single_heavy_task)

The injected decorator makes rebar segmentation participate in the existing
process-wide heavy-task gate and therefore preserves its HTTP 429 response.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any, Callable

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from algorithms.rebar_segmentation import (
    RebarSegmentationError,
    RebarSegmentationParams,
)
from rebar_poc import (
    DEFAULT_MAX_INPUT_POINTS,
    PointCloudInputError,
    StoragePathViolationError,
    UnsupportedPointCloudFormatError,
    run_segmentation_file,
)


logger = logging.getLogger(__name__)

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
HeavyTaskFactory = Callable[[str], Callable[[Callable[..., Any]], Callable[..., Any]]]
CORE_MAX_POINT_COUNT = RebarSegmentationParams().max_point_count


class RebarParams(BaseModel):
    """HTTP representation of every geometric core parameter."""

    model_config = ConfigDict(extra="forbid")

    random_seed: int = Field(default=20260905, ge=0)
    min_point_count: int = Field(default=200, ge=3, le=CORE_MAX_POINT_COUNT)
    max_point_count: int = Field(
        default=CORE_MAX_POINT_COUNT,
        ge=3,
        le=CORE_MAX_POINT_COUNT,
    )
    min_scene_extent: PositiveFiniteFloat = 0.20
    max_scene_extent: PositiveFiniteFloat = 20.0
    max_median_nn_spacing: PositiveFiniteFloat = 0.030
    plane_ransac_iterations: int = Field(default=500, ge=1, le=2_000)
    plane_distance_threshold: PositiveFiniteFloat = 0.003
    min_plane_inliers: int = Field(default=80, ge=3, le=CORE_MAX_POINT_COUNT)
    min_plane_inlier_ratio: FiniteFloat = Field(default=0.25, gt=0, le=1)
    ransac_confidence: FiniteFloat = Field(default=0.99, gt=0, lt=1)
    max_plane_tilt_deg: FiniteFloat = Field(default=30.0, gt=0, lt=90)
    up_hint_x: FiniteFloat = 0.0
    up_hint_y: FiniteFloat = 0.0
    up_hint_z: FiniteFloat = 1.0
    min_rebar_height: PositiveFiniteFloat = 0.008
    max_rebar_height: PositiveFiniteFloat = 0.080
    height_cluster_gap: PositiveFiniteFloat = 0.015
    min_height_band_points: int = Field(default=60, ge=1, le=CORE_MAX_POINT_COUNT)
    pca_radius: PositiveFiniteFloat = 0.032
    pca_min_neighbors: int = Field(default=7, ge=3, le=10_000)
    pca_max_neighbors: int = Field(default=64, ge=3, le=512)
    min_linearity: FiniteFloat = Field(default=0.55, ge=0, lt=1)
    direction_count: int = Field(default=2, ge=1, le=16)
    direction_bin_count: int = Field(default=180, ge=1, le=720)
    direction_tolerance_deg: FiniteFloat = Field(default=12.0, gt=0, lt=45)
    min_direction_separation_deg: FiniteFloat = Field(default=45.0, gt=0, le=90)
    min_direction_votes: int = Field(default=15, ge=1, le=CORE_MAX_POINT_COUNT)
    offset_cluster_gap: PositiveFiniteFloat = 0.014
    min_axis_spacing: PositiveFiniteFloat = 0.030
    axis_distance_threshold: PositiveFiniteFloat = 0.008
    min_axis_directional_support: int = Field(
        default=10, ge=1, le=CORE_MAX_POINT_COUNT
    )
    axial_sample_gap: PositiveFiniteFloat = 0.035
    bridge_gap: PositiveFiniteFloat = 0.130
    min_segment_points: int = Field(default=3, ge=1, le=CORE_MAX_POINT_COUNT)
    min_line_support: int = Field(default=16, ge=1, le=CORE_MAX_POINT_COUNT)
    min_line_length: PositiveFiniteFloat = 0.25
    max_axis_candidates_per_direction: int = Field(default=256, ge=1, le=100_000)

    @model_validator(mode="after")
    def validate_core_relationships(self) -> "RebarParams":
        try:
            RebarSegmentationParams.from_value(self.model_dump())
        except RebarSegmentationError as exc:
            raise ValueError(str(exc)) from exc
        return self


class RebarSegmentRequest(BaseModel):
    """Shared-storage request for ``POST /rebar/segment``."""

    model_config = ConfigDict(extra="forbid")

    point_cloud_path: str = Field(min_length=1, max_length=4096)
    max_input_points: int = Field(
        default=DEFAULT_MAX_INPUT_POINTS,
        ge=3,
        le=DEFAULT_MAX_INPUT_POINTS,
        description="Hard upper bound on points passed to the geometric algorithm",
    )
    voxel_size: PositiveFiniteFloat | None = Field(
        default=None,
        ge=1e-6,
        le=5.0,
        description=(
            "Optional voxel size in metres; when omitted, deterministic "
            "format-aware sampling is used only if the point cap is exceeded"
        ),
    )
    params: RebarParams = Field(default_factory=RebarParams)

    @model_validator(mode="after")
    def validate_work_budget(self) -> "RebarSegmentRequest":
        point_cap = min(self.max_input_points, self.params.max_point_count)
        if point_cap * self.params.plane_ransac_iterations > 100_000_000:
            raise ValueError(
                "max_input_points × plane_ransac_iterations exceeds the service work budget"
            )
        if (
            point_cap
            * self.params.direction_count
            * self.params.direction_bin_count
            > 100_000_000
        ):
            raise ValueError(
                "max_input_points × direction_count × direction_bin_count exceeds "
                "the service work budget"
            )
        if point_cap * self.params.pca_max_neighbors > 25_000_000:
            raise ValueError(
                "max_input_points × pca_max_neighbors exceeds the service work budget"
            )
        return self


def _storage_root_from_environment() -> str:
    return (
        os.environ.get("REBAR_STORAGE_ROOT")
        or os.environ.get("MESH_SERVICE_STORAGE_DIR")
        or "/storage"
    )


def create_rebar_router(
    *,
    heavy_task: HeavyTaskFactory | None = None,
    storage_root: str | os.PathLike[str] | None = None,
) -> APIRouter:
    """Build a router, optionally wrapped by ``main._single_heavy_task``.

    Status semantics:

    * 400: missing, unreadable, empty, or unsupported point-cloud file;
    * 403: path outside the configured shared-storage root;
    * 422: Pydantic contract failure or geometrically unusable input;
    * 429: emitted by the injected process-wide heavy-task gate;
    * 500: unexpected implementation failure with a sanitized response.
    """

    router = APIRouter()
    effective_storage_root = str(
        storage_root if storage_root is not None else _storage_root_from_environment()
    )

    def segment_rebar(request: RebarSegmentRequest):
        try:
            return run_segmentation_file(
                request.point_cloud_path,
                params=request.params.model_dump(),
                max_input_points=request.max_input_points,
                voxel_size=request.voxel_size,
                storage_root=effective_storage_root,
            )
        except StoragePathViolationError as exc:
            return JSONResponse(
                status_code=403,
                content={"code": 403, "msg": str(exc)},
            )
        except UnsupportedPointCloudFormatError as exc:
            return JSONResponse(
                status_code=400,
                content={"code": 400, "msg": str(exc)},
            )
        except PointCloudInputError as exc:
            return JSONResponse(
                status_code=400,
                content={"code": 400, "msg": str(exc)},
            )
        except RebarSegmentationError as exc:
            return JSONResponse(
                status_code=422,
                content={"code": 422, "msg": str(exc)},
            )
        except Exception:
            logger.exception("rebar segmentation failed")
            return JSONResponse(
                status_code=500,
                content={"code": 500, "msg": "rebar segmentation failed"},
            )

    # Resolve the postponed annotation before a decorator from another module
    # copies/follows this function's signature.
    segment_rebar.__annotations__["request"] = RebarSegmentRequest
    endpoint: Callable[..., Any] = segment_rebar
    if heavy_task is not None:
        endpoint = heavy_task("rebar-segment")(endpoint)
        # ``from __future__ import annotations`` stores the request annotation
        # as a string.  Decorators defined in main.py have a different globals
        # namespace, so FastAPI cannot otherwise resolve the wrapped parameter
        # and incorrectly treats it as a query field.  Publish the concrete
        # type after wrapping while keeping the gate's functools.wraps metadata.
        endpoint.__annotations__ = {
            **getattr(endpoint, "__annotations__", {}),
            "request": RebarSegmentRequest,
        }

    router.add_api_route(
        "/rebar/segment",
        endpoint,
        methods=["POST"],
        summary="Segment planar rebar instances from a shared point-cloud file",
        responses={
            400: {"description": "Invalid point-cloud file"},
            403: {"description": "Path escapes shared storage"},
            422: {"description": "Invalid request or insufficient geometric evidence"},
            429: {"description": "Another heavy mesh-service task is active"},
            500: {"description": "Unexpected segmentation failure"},
        },
    )
    return router


def include_rebar_router(
    app: FastAPI,
    *,
    heavy_task: HeavyTaskFactory,
    storage_root: str | os.PathLike[str] | None = None,
) -> None:
    """Small ``main.py`` integration seam using its existing heavy-task gate."""

    app.include_router(
        create_rebar_router(heavy_task=heavy_task, storage_root=storage_root)
    )
