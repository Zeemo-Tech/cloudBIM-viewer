"""Shared production/workbench segmentation; no artifact publishing or UI work.

The exact same ordered stages power both entry points. Arrays are persisted in
caller-owned scratch storage so full-source XYZ is never concatenated in RAM.
"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import json
import time
import numpy as np
from threadpoolctl import threadpool_limits
from .pointcloud_normals import PointCloudContext, estimate_normals
from .normal_geometry_classifier import classify_geometry
from .projection_geometry_classifier import classify_projection
from .pointcloud_fusion import fuse_classifications
from .shared_scene import prepare_scene, regions_from_partition, partition_region_report, ATTRIBUTES as SCENE_ATTRIBUTES
from .region_refinement import reuse_fusion_partition
from .internal_rebar import segment_internal_rebar, ATTRIBUTES as INTERNAL_ATTRIBUTES
from .rebar_dimension_priors import load_dimension_priors

VERSION = "shared-segmentation-v16-fixture-density-denoising"
CLASS_ATTRIBUTES = {"geometry_class": "u1", "geometry_support": "<f4", "geometry_recovered": "u1"}
PROJECTION_ATTRIBUTES = {"projection_class": "u1", "projection_layer": "u1"}
FUSION_ATTRIBUTES = {"fused_class": "u1", "fused_region": "u1", "fused_recovered": "u1", "fused_reason": "u1", "fused_steel_score": "<f4", "fused_steel_evidence": "u1"}
REFINEMENT_ATTRIBUTES = {"refined_class": "u1", "refined_region": "u1", "refined_zone": "u1", "refined_changed": "u1", "refined_reason": "u1"}


def segment_points(positions, directory, *, k=32, workers=1, through_step=6, source=None, progress=None):
    if type(through_step) is not int or through_step not in range(1,7):
        raise ValueError("through_step must be 1–6 (ending at UI Step 05)")
    progress = progress or (lambda *args: None)
    timing = {}
    t0 = time.perf_counter()
    progress("建立可复用 KD 树", 0, 1)
    context = PointCloudContext.build(positions)
    timing["treeS"] = time.perf_counter() - t0
    count = len(positions)
    t0 = time.perf_counter()
    shapes = {"normals": ((count, 3), "<f4"), "normal_valid": ((count,), "u1"),
              "curvature": ((count,), "<f4"), "neighbor_radius": ((count,), "<f4")}
    arrays = {key: np.lib.format.open_memmap(directory / f"{key}.npy", mode="w+", dtype=dtype, shape=shape)
              for key, (shape, dtype) in shapes.items()}
    computation = estimate_normals(context, k=k, workers=workers, output=arrays,
        progress=lambda done, total: progress("第 1 步：计算法向量", done, total))
    timing["normalsS"] = time.perf_counter() - t0
    preprocessing = None
    classification = None
    projection = None
    fusion = None
    regions = None
    refinement = None
    internal_rebar = None
    complete_rebar = None
    execution = None
    if through_step >= 2:
        for name, dtype in SCENE_ATTRIBUTES.items():
            shapes[name] = ((count,), dtype)
            arrays[name] = np.lib.format.open_memmap(directory / f"{name}.npy", mode="w+", dtype=dtype, shape=(count,))
        t0 = time.perf_counter()
        with threadpool_limits(limits=1):
            preprocessing = prepare_scene(context, output={name: arrays[name] for name in SCENE_ATTRIBUTES}, progress=progress)
        timing['preprocessingS'] = time.perf_counter()-t0
        t0 = time.perf_counter()
        attributes = {**CLASS_ATTRIBUTES, **(PROJECTION_ATTRIBUTES if through_step >= 3 else {})}
        for name, dtype in attributes.items():
            shapes[name] = ((count,), dtype)
            arrays[name] = np.lib.format.open_memmap(directory / f"{name}.npy", mode="w+", dtype=dtype, shape=(count,))
        projection_workers = max(1, workers//2) if through_step >= 3 else 0
        normal_workers = max(1, workers-projection_workers)
        def normal_branch():
            began = time.perf_counter()
            result = classify_geometry(context, workers=normal_workers,
                output={name: arrays[name] for name in CLASS_ATTRIBUTES}, progress=progress)
            return result, time.perf_counter()-began
        def projection_branch():
            began = time.perf_counter()
            result = classify_projection(context.positions, context.normals, context.normal_valid,
                workers=projection_workers, output={name: arrays[name] for name in PROJECTION_ATTRIBUTES}, progress=progress,
                prepared=context.scene_cache["projection"], region_owned=context.scene_cache["region_owned"])
            return result, time.perf_counter()-began
        if through_step >= 3:
            # One enclosing BLAS limit keeps its process-global state
            # stable until both tasks finish; nested normal limits restore 1.
            with threadpool_limits(limits=1):
                if workers >= 2:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        a = pool.submit(normal_branch); b = pool.submit(projection_branch)
                        classification, timing["classificationS"] = a.result()
                        (projection, projection_cache, _), timing["projectionS"] = b.result()
                else:
                    classification, timing["classificationS"] = normal_branch()
                    (projection, projection_cache, _), timing["projectionS"] = projection_branch()
            context.projection_class = arrays["projection_class"]
            context.projection_layer = arrays["projection_layer"]
            context.projection_cache = projection_cache
            execution = {"mode": "parallel" if workers >= 2 else "serial-worker-budget-1",
                         "normalWorkers": normal_workers, "projectionWorkers": projection_workers,
                         "sharedInput": "same source XYZ/normals, immutable table mask, XY raster and partition; independent classifier evidence",
                         "tableFitCalls": 1, "xyRasterBuilds": 1, "frameDetectionCalls": 1,
                         "rawTreeBuilds": 1, "projectionUsesKdTree": True,
                         "projectionTreeInput": "independent non-table 3 mm occupied voxel centroids"}
        else:
            classification, timing["classificationS"] = normal_branch()
        timing["classifiersWallS"] = time.perf_counter()-t0
        classification = json.loads(json.dumps(classification, default=lambda value: value.tolist()))
    if through_step >= 4:
        for name, dtype in FUSION_ATTRIBUTES.items():
            shapes[name] = ((count,), dtype)
            arrays[name] = np.lib.format.open_memmap(directory / f"{name}.npy", mode="w+", dtype=dtype, shape=(count,))
        t0 = time.perf_counter()
        with threadpool_limits(limits=1):
            fusion = fuse_classifications(context, workers=workers,
                output={name: arrays[name] for name in FUSION_ATTRIBUTES if name != "fused_region"}, progress=progress)
        timing["fusionS"] = time.perf_counter()-t0
        t0 = time.perf_counter()
        progress("第 3 步：复用钢筋分区", 0, count)
        regions_from_partition(context.fused_class, context.partition_zone, output=arrays['fused_region'])
        context.fused_region = arrays["fused_region"]
        regions = partition_region_report(context, context.fused_region)
        timing["regionsS"] = time.perf_counter()-t0
    if through_step >= 5:
        for name, dtype in REFINEMENT_ATTRIBUTES.items():
            shapes[name] = ((count,), dtype)
            arrays[name] = np.lib.format.open_memmap(directory / f"{name}.npy", mode="w+", dtype=dtype, shape=(count,))
        t0 = time.perf_counter()
        with threadpool_limits(limits=1):
            refinement = reuse_fusion_partition(context, regions,
                output={name: arrays[name] for name in REFINEMENT_ATTRIBUTES})
        timing['refinementS'] = time.perf_counter()-t0
    if through_step >= 6:
        for name, dtype in INTERNAL_ATTRIBUTES.items():
            shapes[name] = ((count,), dtype)
            arrays[name] = np.lib.format.open_memmap(directory / f'{name}.npy', mode='w+', dtype=dtype, shape=(count,))
        t0 = time.perf_counter()
        context.dimension_priors = load_dimension_priors(source_path=source)
        with threadpool_limits(limits=1):
            internal_rebar = segment_internal_rebar(context, workers=workers,
                output={name: arrays[name] for name in INTERNAL_ATTRIBUTES}, progress=progress)
        timing['internalRebarS'] = time.perf_counter()-t0
    return SimpleNamespace(context=context, arrays=arrays, shapes=shapes, computation=computation, timing=timing, classification=classification, projection=projection, fusion=fusion, regions=regions, refinement=refinement, internal_rebar=internal_rebar, complete_rebar=complete_rebar, execution=execution, preprocessing=preprocessing)
