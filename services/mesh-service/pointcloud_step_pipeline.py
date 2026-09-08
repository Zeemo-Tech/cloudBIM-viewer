"""Durable, immutable point-cloud step runs, independent of legacy classifiers."""
from dataclasses import dataclass
from copy import deepcopy
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import time
import uuid

import laspy
import numpy as np
from threadpoolctl import threadpool_limits

from algorithms.pointcloud_normals import PointCloudContext, VERSION, available_workers, estimate_normals
from algorithms.normal_geometry_classifier import classify_geometry
from algorithms.projection_geometry_classifier import classify_projection
from algorithms.projection_artifacts import write_projection_artifacts
from algorithms.pointcloud_fusion import fuse_classifications
from algorithms.fixture_regions import classify_regions
from algorithms.region_refinement import refine_regions
from algorithms.internal_rebar import segment_internal_rebar, ATTRIBUTES as INTERNAL_ATTRIBUTES


ATTRIBUTES = {"normal_x": "<f4", "normal_y": "<f4", "normal_z": "<f4",
              "normal_valid": "u1", "normal_curvature": "<f4", "normal_radius": "<f4"}
CLASS_ATTRIBUTES = {"geometry_class": "u1", "geometry_support": "<f4", "geometry_recovered": "u1"}
PROJECTION_ATTRIBUTES = {"projection_class": "u1", "projection_layer": "u1"}
FUSION_ATTRIBUTES = {"fused_class": "u1", "fused_region": "u1", "fused_recovered": "u1", "fused_reason": "u1"}
FUSION_LAS_ATTRIBUTES = {**FUSION_ATTRIBUTES, "source_record_index": "<u8"}
REFINEMENT_ATTRIBUTES = {"refined_class": "u1", "refined_region": "u1", "refined_zone": "u1", "refined_changed": "u1", "refined_reason": "u1"}


def atomic_json(path: Path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def source_stamp(path):
    st = path.stat()
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def source_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_positions(source: Path, directory: Path, progress):
    with laspy.open(source) as reader:
        count = reader.header.point_count
        if count < 3:
            raise ValueError("源点云不足 3 个点")
        points = np.lib.format.open_memmap(directory / "positions.npy", mode="w+", dtype="<f8", shape=(count, 3))
        colors = np.lib.format.open_memmap(directory / "colors.npy", mode="w+", dtype="u1", shape=(count, 3))
        rgb = all(name in reader.header.point_format.dimension_names for name in ("red", "green", "blue"))
        offset = 0
        for chunk in reader.chunk_iterator(262144):
            stop = offset + len(chunk)
            for col, name in enumerate(("x", "y", "z")):
                points[offset:stop, col] = getattr(chunk, name)
            if rgb:
                for col, name in enumerate(("red", "green", "blue")):
                    colors[offset:stop, col] = (np.asarray(chunk[name], dtype=np.uint32) * 255 // 65535).astype(np.uint8)
            else:
                # Fixed intensity transfer (no whole-cloud sort or percentile).
                gray = (70 + 185 * np.sqrt(np.asarray(chunk.intensity, dtype=np.float32) / 65535)).astype(np.uint8)
                colors[offset:stop] = gray[:, None]
            offset = stop
            progress("读取原始点云", offset, count)
        if offset != count:
            raise ValueError("LAS 记录数量与头部声明不符")
    return points, colors


def write_las(source, output, context, subsets=None):
    """Copy every original dimension/record, add float normals as LAS Extra Bytes."""
    with laspy.open(source) as reader:
        header = deepcopy(reader.header)
        original_names = set(header.point_format.dimension_names)
        additions = []
        attributes = {**ATTRIBUTES, **(CLASS_ATTRIBUTES if context.geometry_class is not None else {})}
        if context.projection_class is not None:
            attributes.update(PROJECTION_ATTRIBUTES)
        if context.fused_class is not None:
            attributes.update(FUSION_LAS_ATTRIBUTES)
        if context.refined_class is not None:
            attributes.update(REFINEMENT_ATTRIBUTES)
        if context.internal_type is not None:
            attributes.update(INTERNAL_ATTRIBUTES)
        for name, dtype in attributes.items():
            if name in original_names:
                if header.point_format.dimension_by_name(name).dtype != np.dtype(dtype):
                    raise ValueError(f"源 LAS 已有不兼容属性 {name}")
            else:
                additions.append(laspy.ExtraBytesParams(name=name, type=dtype))
        header.add_extra_dims(additions)
        offset = 0
        with ExitStack() as stack:
            writer = stack.enter_context(laspy.open(output, mode="w", header=header, do_compress=False))
            subset_writers = {kind: stack.enter_context(laspy.open(path, mode="w", header=deepcopy(header), do_compress=False))
                              for kind, path in (subsets or {}).items()}
            for chunk in reader.chunk_iterator(262144):
                result = laspy.ScaleAwarePointRecord.zeros(len(chunk), header=header)
                for name in original_names:
                    result[name] = chunk[name]
                stop = offset + len(chunk)
                for col, name in enumerate(("normal_x", "normal_y", "normal_z")):
                    result[name] = context.normals[offset:stop, col]
                result["normal_valid"] = context.normal_valid[offset:stop]
                result["normal_curvature"] = context.curvature[offset:stop]
                result["normal_radius"] = context.neighbor_radius[offset:stop]
                if context.geometry_class is not None:
                    result["geometry_class"] = context.geometry_class[offset:stop]
                    result["geometry_support"] = context.geometry_support[offset:stop]
                    result["geometry_recovered"] = context.geometry_recovered[offset:stop]
                if context.projection_class is not None:
                    result["projection_class"] = context.projection_class[offset:stop]
                    result["projection_layer"] = context.projection_layer[offset:stop]
                if context.fused_class is not None:
                    for name in FUSION_ATTRIBUTES:
                        result[name] = getattr(context, name)[offset:stop]
                    result["source_record_index"] = np.arange(offset, stop, dtype=np.uint64)
                if context.refined_class is not None:
                    for name in REFINEMENT_ATTRIBUTES:
                        result[name] = getattr(context, name)[offset:stop]
                if context.internal_type is not None:
                    for name in INTERNAL_ATTRIBUTES:
                        result[name] = getattr(context, name)[offset:stop]
                writer.write_points(result)
                for kind, subset_writer in subset_writers.items():
                    final_classes = context.refined_class if context.refined_class is not None else context.fused_class
                    mask = context.internal_type[offset:stop] > 0 if kind == 'internal' else final_classes[offset:stop] == kind
                    if mask.any():
                        subset_writer.write_points(result[mask])
                offset = stop
            if reader.evlrs:
                writer.write_evlrs(reader.evlrs)
                for subset_writer in subset_writers.values():
                    subset_writer.write_evlrs(reader.evlrs)
        if offset != len(context.positions):
            raise ValueError("导出期间源 LAS 点数发生变化")


def write_preview(directory, context, colors, run_id, limit):
    count = len(context.positions)
    size = min(count, limit)
    # Deterministic, without allocating an N-sized index array for sampling.
    ids = np.linspace(0, count - 1, size, dtype=np.int64)
    lo, hi = context.positions.min(axis=0), context.positions.max(axis=0)
    origin = (lo + hi) / 2
    preview_dir = directory / "preview"
    preview_dir.mkdir()
    arrays = {"positions": (context.positions[ids] - origin).astype("<f4"),
              "normals": np.asarray(context.normals[ids], dtype="<f4"),
              "colors": colors[ids], "valid": context.normal_valid[ids],
              "source_indices": ids.astype("<u8")}
    if context.geometry_class is not None:
        arrays["classes"] = context.geometry_class[ids]
        arrays["recovered"] = context.geometry_recovered[ids]
    if context.projection_class is not None:
        arrays["projection_classes"] = context.projection_class[ids]
        arrays["projection_layers"] = context.projection_layer[ids]
    if context.fused_class is not None:
        arrays.update(fused_classes=context.fused_class[ids], fused_regions=context.fused_region[ids],
                      fused_recovered=context.fused_recovered[ids], fused_reasons=context.fused_reason[ids])
    if context.refined_class is not None:
        arrays.update(refined_classes=context.refined_class[ids], refined_regions=context.refined_region[ids],
                      refined_zones=context.refined_zone[ids], refined_changed=context.refined_changed[ids],
                      refined_reasons=context.refined_reason[ids])
    if context.internal_type is not None:
        arrays.update(internal_types=context.internal_type[ids], internal_instances=context.internal_instance[ids],
                      internal_segments=context.internal_segment[ids], internal_confidence=context.internal_confidence[ids])
    for name, array in arrays.items():
        array.tofile(preview_dir / f"{name}.bin")
    base = f"/runs/{run_id}/preview"
    return {"pointCount": size, "totalPointCount": count, "origin": origin.tolist(),
            "bounds": {"min": (lo - origin).tolist(), "max": (hi - origin).tolist()},
            "sampling": "deterministic evenly spaced source record indices; visualization only",
            **{name + "Url": f"{base}/{name}.bin" for name in ("positions", "normals", "colors", "valid")},
            "sourceIndicesUrl": f"{base}/source_indices.bin",
            **({"classesUrl": f"{base}/classes.bin", "recoveredUrl": f"{base}/recovered.bin"}
               if context.geometry_class is not None else {}),
            **({"projectionClassesUrl": f"{base}/projection_classes.bin", "projectionLayersUrl": f"{base}/projection_layers.bin"}
               if context.projection_class is not None else {}),
            **({"fusedClassesUrl": f"{base}/fused_classes.bin", "fusedRegionsUrl": f"{base}/fused_regions.bin",
                "fusedRecoveredUrl": f"{base}/fused_recovered.bin", "fusedReasonsUrl": f"{base}/fused_reasons.bin"}
               if context.fused_class is not None else {}),
            **({"refinedClassesUrl": f"{base}/refined_classes.bin", "refinedRegionsUrl": f"{base}/refined_regions.bin",
                "refinedZonesUrl": f"{base}/refined_zones.bin", "refinedChangedUrl": f"{base}/refined_changed.bin",
                "refinedReasonsUrl": f"{base}/refined_reasons.bin"} if context.refined_class is not None else {}),
            **({"internalTypesUrl": f"{base}/internal_types.bin", "internalInstancesUrl": f"{base}/internal_instances.bin",
                "internalSegmentsUrl": f"{base}/internal_segments.bin", "internalConfidenceUrl": f"{base}/internal_confidence.bin"}
               if context.internal_type is not None else {})}


@dataclass
class StepRun:
    manifest: dict
    context: PointCloudContext
    directory: Path


def run_from_source(source: Path, output_root: Path, *, k=32, workers=None, preview_limit=1000000, progress=None, through_step=1):
    """Always starts at raw source, never resumes a previous algorithm result.

    A successful run publishes a self-contained LAS and NPY attribute columns.
    Context.tree is retained for the next step of THIS run. Reloading a run from
    disk does not deserialize code/pickle; callers rebuild a tree when needed.
    """
    source, output_root = Path(source).resolve(), Path(output_root).resolve()
    workers = available_workers() if workers is None else workers
    if isinstance(k, bool) or not isinstance(k, int) or not 3 <= k <= 128:
        raise ValueError("k 必须是 3–128 的整数")
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= available_workers():
        raise ValueError(f"workers 必须是 1–{available_workers()} 的整数")
    if not isinstance(preview_limit, int) or preview_limit < 1:
        raise ValueError("preview_limit must be positive")
    if type(through_step) is not int or through_step not in (1, 2, 3, 4, 5, 6):
        raise ValueError("through_step 必须是 1–6，6 为内部钢筋分层与实例")
    progress = progress or (lambda *args: None)
    started, cpu_started = time.perf_counter(), time.process_time()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    output_root.mkdir(parents=True, exist_ok=True)
    directory = output_root / (".pending-" + run_id)
    directory.mkdir()
    timing = {}
    try:
        stamp = source_stamp(source)
        t0 = time.perf_counter()
        progress("校验源点云", 0, 1)
        digest = source_hash(source)
        positions, colors = load_positions(source, directory, progress)
        timing["readS"] = time.perf_counter() - t0
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
        classification = None
        projection = None
        fusion = None
        regions = None
        refinement = None
        internal_rebar = None
        execution = None
        if through_step >= 2:
            t0 = time.perf_counter()
            attributes = {**CLASS_ATTRIBUTES, **(PROJECTION_ATTRIBUTES if through_step >= 3 else {})}
            for name, dtype in attributes.items():
                shapes[name] = ((count,), dtype)
                arrays[name] = np.lib.format.open_memmap(directory / f"{name}.npy", mode="w+", dtype=dtype, shape=(count,))
            projection_workers = min(2, max(1, workers-1)) if through_step >= 3 else 0
            normal_workers = max(1, workers-projection_workers)
            def normal_branch():
                began = time.perf_counter()
                result = classify_geometry(context, workers=normal_workers,
                    output={name: arrays[name] for name in CLASS_ATTRIBUTES}, progress=progress)
                return result, time.perf_counter()-began
            def projection_branch():
                began = time.perf_counter()
                result = classify_projection(context.positions, context.normals, context.normal_valid,
                    workers=projection_workers, output={name: arrays[name] for name in PROJECTION_ATTRIBUTES}, progress=progress)
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
                             "sharedInput": "same source XYZ and Step 1 normals; no cross-branch label input",
                             "rawTreeBuilds": 1, "projectionUsesKdTree": False}
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
                    output={name: arrays[name] for name in ("fused_class", "fused_recovered", "fused_reason")}, progress=progress)
            timing["fusionS"] = time.perf_counter()-t0
            t0 = time.perf_counter()
            progress("第 3 步：夹具围内 / 外露钢筋区域", 0, count)
            with threadpool_limits(limits=1):
                regions, context.region_cache, region_labels = classify_regions(positions, context.fused_class,
                    projection, context.projection_cache, workers=workers)
            arrays["fused_region"][:] = region_labels
            context.fused_region = arrays["fused_region"]
            timing["regionsS"] = time.perf_counter()-t0
        if through_step >= 5:
            for name, dtype in REFINEMENT_ATTRIBUTES.items():
                shapes[name] = ((count,), dtype)
                arrays[name] = np.lib.format.open_memmap(directory / f"{name}.npy", mode="w+", dtype=dtype, shape=(count,))
            t0 = time.perf_counter()
            with threadpool_limits(limits=1):
                refinement = refine_regions(context, regions, workers=workers,
                    output={name: arrays[name] for name in REFINEMENT_ATTRIBUTES}, progress=progress)
            timing['refinementS'] = time.perf_counter()-t0
        if through_step >= 6:
            for name, dtype in INTERNAL_ATTRIBUTES.items():
                shapes[name] = ((count,), dtype)
                arrays[name] = np.lib.format.open_memmap(directory / f'{name}.npy', mode='w+', dtype=dtype, shape=(count,))
            t0 = time.perf_counter()
            with threadpool_limits(limits=1):
                internal_rebar = segment_internal_rebar(context, workers=workers,
                    output={name: arrays[name] for name in INTERNAL_ATTRIBUTES}, progress=progress)
            timing['internalRebarS'] = time.perf_counter()-t0
        t0 = time.perf_counter()
        progress("持久化点云及法向量属性", 0, 1)
        las_name = "pointcloud-with-classes.las" if classification else "pointcloud-with-normals.las"
        subsets = {3: directory / 'steel-only.las', 2: directory / 'fixture-only.las'} if fusion else {}
        if internal_rebar is not None:
            subsets['internal'] = directory / 'internal-steel.las'
        write_las(source, directory / las_name, context, subsets=subsets)
        if internal_rebar is not None:
            atomic_json(directory / 'internal-instances.json', internal_rebar)
        if classification:
            cache = context.classification_cache
            np.savez(directory / "classification-features.npz", grid_positions=cache["grid"].points,
                     grid_projectors=cache["grid"].projectors, source_to_cell=cache["grid"].source_to_cell,
                     residual_ids=cache["residual_ids"], patch_ids=cache["patch_ids"], cell_labels=cache["cell_labels"],
                     recovered_cells=cache["recovered_cells"], strong_bars=cache["strong_bars"],
                     **cache["features"])
        if projection is not None:
            np.savez(directory / "projection-features.npz", **context.projection_cache)
        if fusion is not None:
            np.savez(directory / "fusion-features.npz", **{name: value for name, value in context.fusion_cache.items() if name != "axis_tree"})
            np.savez(directory / "region-features.npz", **context.region_cache)
        if refinement is not None:
            np.savez(directory / "refinement-features.npz", **context.refinement_cache)
        for array in [positions, colors, *arrays.values()]:
            array.flush()
        timing["persistS"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        progress("生成效果预览", 0, 1)
        preview = write_preview(directory, context, colors, run_id, preview_limit)
        if projection is not None:
            write_projection_artifacts(directory, run_id, projection, context.projection_cache)
        timing["previewS"] = time.perf_counter() - t0
        if source_stamp(source) != stamp:
            raise ValueError("计算期间源文件发生变化；本轮结果未发布，请从头重试")
        # Flush artifacts before making their manifest visible.
        t0 = time.perf_counter()
        for path in directory.rglob("*"):
            if path.is_file():
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
        timing["persistS"] += time.perf_counter() - t0
        valid = int(np.count_nonzero(context.normal_valid))
        timing["totalS"] = time.perf_counter() - started
        manifest = {
            "schema": "pointcloud-steps-v1", "algorithmVersion": VERSION, "runId": run_id,
            "createdAt": datetime.now(timezone.utc).isoformat(), "completed": True,
            "runMode": "fresh-source-all-steps", "orientation": "unoriented",
            "source": {"name": source.name, "path": str(source), "sha256": digest, "pointCount": count,
                       "sizeBytes": stamp[2], "unchangedDuringRun": True},
            "parameters": {"k": k, "effectiveK": computation["effectiveK"], "workers": workers, "kIncludesSelf": True, "throughStep": through_step},
            "validNormalCount": valid, "invalidNormalCount": count - valid,
            "steps": [{"id": "00-source", "pointCount": count}, {"id": "01-normals", "pointCount": count}]
                     + ([{"id": "02-classification", "pointCount": count}] if classification else [])
                     + ([{"id": "02-projection", "pointCount": count, "dependsOn": "01-normals"}] if projection else [])
                     + ([{"id": "03-fusion", "pointCount": count, "dependsOn": ["02-classification", "02-projection"]}] if fusion else [])
                     + ([{"id": "04-refinement", "pointCount": count, "dependsOn": ["03-fusion"]}] if refinement else [])
                     + ([{"id": "05-internal-rebar", "pointCount": internal_rebar['pointCount'], "dependsOn": ["04-refinement"]}] if internal_rebar is not None else []),
            **({"classification": classification} if classification else {}),
            **({"projection": projection, "branchExecution": execution} if projection else {}),
            **({"fusion": fusion, "regions": regions} if fusion else {}),
            **({"refinement": refinement} if refinement else {}),
            **({"internalRebar": internal_rebar} if internal_rebar is not None else {}),
            "timings": timing,
            "performance": {"pointsPerSecond": count / timing["normalsS"],
                            "cpuS": time.process_time() - cpu_started,
                            "peakRssMB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                            "peakRssScope": "process lifetime high-water mark", **computation},
            "timingScope": "server pipeline wall time; excludes HTTP queue/download/browser render; OS page cache not flushed",
            "cache": {"scope": "live StepRun.context for subsequent steps of this run", "treeBuildCount": 1,
                      "sourceSha256": digest, "positionMutationAllowed": False,
                      "restart": "NPY attributes persist; KD tree is rebuilt, never unpickled"},
            "attributes": {"identity": "array row i = original source LAS record i (zero-based)",
                           "coordinateFrame": "native LAS XYZ, scaled float64; normals in the same frame",
                           "normal": "unit eigenvector of smallest PCA eigenvalue; sign not oriented",
                           "invalid": "normal_valid=0 and normal=(0,0,0) for degenerate neighborhoods",
                           **({"geometry_recovered": "1 = fixture-to-rebar recovery during this run; 0 = unchanged"} if classification else {}),
                           **({"projection_class": "Independent projection route: 1 table, 2 fixture, 3 rebar",
                               "projection_layer": "0 outside detected Z bands; other ids refer to projection.layers"} if projection else {}),
                           **({"fused_class": "Evidence fusion: 1 table, 2 fixture, 3 rebar",
                               "fused_region": "0 table, 1 interior steel, 2 exterior steel, 3 fixture, 4 unlocated steel",
                               "fused_recovered": "1 = recovered projection v2 junction or B fixture-to-fused steel",
                               "fused_reason": "Rule id, see fusion.reasonNames; not a confidence probability",
                               "source_record_index": "Zero-based original source record index, also retained in steel/fixture subset LAS"} if fusion else {}),
                           **({"refined_class": "Region-constrained final class: 1 table, 2 fixture, 3 steel",
                               "refined_region": "Same region ids as fused_region, recomputed after refinement",
                               "refined_zone": "0 unlocated, 1 inner, 2 frame band, 3 outside; independent of semantic class",
                               "refined_changed": "1 iff refined_class differs from fused_class",
                               "refined_reason": "Rule id, see refinement.reasonNames"} if refinement else {}),
                           **({"internal_type": "0 outside strict inner steel scope; 1 lower, 2 upper, 3 straight web, 4 unassigned",
                               "internal_instance": "Run-local stable geometry-sorted cylinder ID; 0 unassigned/outside",
                               "internal_segment": "Cylinder fit part; a mildly curved horizontal bar may have multiple parts in one instance; each web straight segment is one instance",
                               "internal_confidence": "Geometric fit score, not calibrated probability; 0 unassigned/outside"} if internal_rebar is not None else {}),
                           "lasExtraBytes": {**ATTRIBUTES, **(CLASS_ATTRIBUTES if classification else {}),
                                             **(PROJECTION_ATTRIBUTES if projection else {}), **(FUSION_LAS_ATTRIBUTES if fusion else {}),
                                             **(REFINEMENT_ATTRIBUTES if refinement else {}), **(INTERNAL_ATTRIBUTES if internal_rebar is not None else {})},
                           "columns": {name: name + ".npy" for name in ("positions", "colors", *shapes)}},
            "preview": preview,
            "files": {"lasUrl": f"/runs/{run_id}/{las_name}", "manifestUrl": f"/runs/{run_id}/manifest.json",
                      "subsetClassAttribute": "refined_class" if refinement else "fused_class" if fusion else None,
                      **({"steelLasUrl": f"/runs/{run_id}/steel-only.las", "fixtureLasUrl": f"/runs/{run_id}/fixture-only.las"} if fusion else {}),
                      **({"internalSteelLasUrl": f"/runs/{run_id}/internal-steel.las", "internalInstancesUrl": f"/runs/{run_id}/internal-instances.json"} if internal_rebar is not None else {})},
        }
        atomic_json(directory / "manifest.json", manifest)
        final = output_root / run_id
        directory.rename(final)
        atomic_json(output_root / "latest.json", {"runId": run_id})
        progress("完成", count, count)
        return StepRun(manifest, context, final)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
