"""Shared production/workbench segmentation; no artifact publishing or UI work.

The exact same ordered stages power both entry points. Computation arrays use
caller-owned scratch storage so full-source XYZ is never concatenated in RAM.
Legacy refined_* attributes are read-only views, materialized by the publisher.
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
from .shared_floating_noise import prepare_floating_scene, denoise_branch, ATTRIBUTES as FLOATING_ATTRIBUTES
from .shared_scene import prepare_scene, regions_from_partition, partition_region_report, ATTRIBUTES as SCENE_ATTRIBUTES
from .region_refinement import reuse_fusion_partition
from .internal_rebar import segment_internal_rebar, ATTRIBUTES as INTERNAL_ATTRIBUTES
from .rebar_dimension_priors import load_dimension_priors
from .preprocessed_las import load_preprocessed_las

VERSION = "shared-segmentation-v29-evidence-finalization"
SCENE_ATTRIBUTES = {**SCENE_ATTRIBUTES, **FLOATING_ATTRIBUTES}
CLASS_ATTRIBUTES = {"geometry_class": "u1", "geometry_support": "<f4", "geometry_recovered": "u1"}
PROJECTION_ATTRIBUTES = {"projection_class": "u1", "projection_layer": "u1"}
FUSION_ATTRIBUTES = {"fused_class": "u1", "fused_region": "u1", "fused_recovered": "u1", "fused_reason": "u1", "fused_steel_score": "<f4", "fused_steel_evidence": "u1"}
REFINEMENT_ATTRIBUTES = {"refined_class": "u1", "refined_region": "u1", "refined_zone": "u1", "refined_changed": "u1", "refined_reason": "u1"}


def segment_points(positions, directory, *, k=32, workers=1, through_step=6, source=None, progress=None, design_inventory=None, dimension_priors=None):
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
    if through_step >= 2:
        for name, dtype in SCENE_ATTRIBUTES.items():
            shapes[name] = ((count,), dtype)
            arrays[name] = np.lib.format.open_memmap(directory / f"{name}.npy", mode="w+", dtype=dtype, shape=(count,))
    persisted = (load_preprocessed_las(source, count=count, normal_k=k, normal_output=arrays,
                 table_output=arrays.get("shared_table_mask")) if source is not None else None)
    if persisted is None:
        computation = estimate_normals(context, k=k, workers=workers, output=arrays,
            progress=lambda done, total: progress("第 1 步：计算法向量", done, total))
    else:
        for name in ("normals", "normal_valid", "curvature", "neighbor_radius"):
            setattr(context, name, arrays[name])
        computation = {"elapsedS": time.perf_counter() - t0, "effectiveK": min(k, count), "workers": 0,
                       "treeReused": True, "persisted": True,
                       "preprocessAlgorithmVersion": persisted["algorithmVersion"]}
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
        t0 = time.perf_counter()
        with threadpool_limits(limits=1):
            preprocessing = prepare_scene(context, output={name: arrays[name] for name in SCENE_ATTRIBUTES if name not in FLOATING_ATTRIBUTES}, progress=progress,
                fixed_table=persisted.get("plane") if persisted else None,
                fixed_table_mask=arrays["shared_table_mask"] if persisted else None)
            layering, floating_zones = prepare_floating_scene(context, design_inventory,
                output={name: arrays[name] for name in FLOATING_ATTRIBUTES}, progress=progress)
            preprocessing.update(layering=layering, floatingZones=floating_zones)
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
            result['floatingDenoising'] = denoise_branch(context, arrays['geometry_class'],
                cache=context.classification_cache, stage='02A', workers=normal_workers, progress=progress)
            arrays['geometry_recovered'][arrays['geometry_class'] != 3] = 0
            arrays['geometry_support'][arrays['geometry_class'] == 4] = 0
            result['noiseClass'] = 4
            result['classNames'] = {**result.get('classNames', {}), '4': '悬浮噪音'}
            result['colors'] = {**result.get('colors', {}), '4': '#ef476f'}
            result['counts'] = dict(zip(('table', 'fixture', 'rebar', 'noise'),
                map(int, np.bincount(arrays['geometry_class'], minlength=5)[1:5])))
            return result, time.perf_counter()-began
        def projection_branch():
            began = time.perf_counter()
            result = classify_projection(context.positions, context.normals, context.normal_valid,
                workers=projection_workers, output={name: arrays[name] for name in PROJECTION_ATTRIBUTES}, progress=progress,
                prepared=context.scene_cache["projection"], region_owned=context.scene_cache["region_owned"])
            report, cache, branch_output = result
            report['floatingDenoising'] = denoise_branch(context, arrays['projection_class'],
                cache=cache, stage='02B', workers=projection_workers, progress=progress)
            report['noiseClass'] = 4
            report['classNames'] = {**report.get('classNames', {}), '4': '悬浮噪音'}
            report['colors'] = {**report.get('colors', {}), '4': '#ef476f'}
            for layer in report.get('layers', []):
                selected = arrays['projection_layer'] == layer['id']
                layer['classCounts'] = dict(zip(('table', 'fixture', 'rebar', 'noise'),
                    map(int, np.bincount(arrays['projection_class'][selected], minlength=5)[1:5])))
            # Keep the debug image aligned with this branch's own labels.
            if 'image_labels' in cache:
                top_labels = np.zeros_like(cache['image_labels']).ravel()
                prepared = context.scene_cache['projection']
                for start in range(0, count, 262144):
                    stop = min(count, start + 262144)
                    pixels = cache['source_to_pixel'][start:stop]
                    visible = ~prepared['table_mask'][start:stop] & (np.abs(context.positions[start:stop, 2] - prepared['zmax'][pixels]) < 1.e-7)
                    np.maximum.at(top_labels, pixels[visible], arrays['projection_class'][start:stop][visible])
                cache['image_labels'] = top_labels.reshape(cache['image_labels'].shape)
            report['counts'] = dict(zip(('table', 'fixture', 'rebar', 'noise'),
                map(int, np.bincount(arrays['projection_class'], minlength=5)[1:5])))
            return (report, cache, branch_output), time.perf_counter()-began
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
                         "tableFitCalls": 0 if persisted else 1, "xyRasterBuilds": 1, "frameDetectionCalls": 1,
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
        t0 = time.perf_counter()
        refinement = reuse_fusion_partition(context, regions)
        for name, dtype in REFINEMENT_ATTRIBUTES.items():
            shapes[name] = ((count,), dtype)
            arrays[name] = getattr(context, name)
        timing['refinementS'] = time.perf_counter()-t0
    if through_step >= 6:
        for name, dtype in INTERNAL_ATTRIBUTES.items():
            shapes[name] = ((count,), dtype)
            arrays[name] = np.lib.format.open_memmap(directory / f'{name}.npy', mode='w+', dtype=dtype, shape=(count,))
        t0 = time.perf_counter()
        # The workbench resolves all design data from one validated snapshot.
        # None preserves legacy callers; an explicit unavailable report must
        # not trigger a lookup of an older source-associated model.
        context.dimension_priors = (load_dimension_priors(source_path=source)
                                    if dimension_priors is None else dimension_priors)
        with threadpool_limits(limits=1):
            internal_rebar = segment_internal_rebar(context, workers=workers,
                output={name: arrays[name] for name in INTERNAL_ATTRIBUTES}, progress=progress)
        timing['internalRebarS'] = time.perf_counter()-t0
    return SimpleNamespace(context=context, arrays=arrays, shapes=shapes, computation=computation, timing=timing, classification=classification, projection=projection, fusion=fusion, regions=regions, refinement=refinement, internal_rebar=internal_rebar, complete_rebar=complete_rebar, execution=execution, preprocessing=preprocessing)
