"""Durable, immutable point-cloud step runs, independent of legacy classifiers."""
from dataclasses import dataclass
from copy import deepcopy
from contextlib import ExitStack
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

from algorithms.pointcloud_normals import PointCloudContext, available_workers
from algorithms.projection_artifacts import write_projection_artifacts
from algorithms.internal_rebar import ATTRIBUTES as INTERNAL_ATTRIBUTES
from algorithms.rebar_extension import ATTRIBUTES as COMPLETE_ATTRIBUTES
from algorithms.design_prior_refinement import ATTRIBUTES as PRIOR_ATTRIBUTES, MODES as PRIOR_MODES, refine_design_prior
from rebar_design_prior import validate_snapshot
from algorithms.design_guided_instances import refine_instances


ATTRIBUTES = {"normal_x": "<f4", "normal_y": "<f4", "normal_z": "<f4",
              "normal_valid": "u1", "normal_curvature": "<f4", "normal_radius": "<f4"}
from algorithms.pointcloud_segmentation import (VERSION, segment_points, CLASS_ATTRIBUTES, PROJECTION_ATTRIBUTES, FUSION_ATTRIBUTES, REFINEMENT_ATTRIBUTES, SCENE_ATTRIBUTES)
FUSION_LAS_ATTRIBUTES = {**FUSION_ATTRIBUTES, "source_record_index": "<u8"}


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
        if context.shared_table_mask is not None:
            attributes.update(SCENE_ATTRIBUTES)
        if context.projection_class is not None:
            attributes.update(PROJECTION_ATTRIBUTES)
        if context.fused_class is not None:
            attributes.update(FUSION_LAS_ATTRIBUTES)
        if context.refined_class is not None:
            attributes.update(REFINEMENT_ATTRIBUTES)
        if context.internal_type is not None:
            attributes.update(INTERNAL_ATTRIBUTES)
        if context.complete_class is not None:
            attributes.update(COMPLETE_ATTRIBUTES)
        if getattr(context, 'prior_class', None) is not None:
            attributes.update(PRIOR_ATTRIBUTES)
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
                if context.shared_table_mask is not None:
                    for name in SCENE_ATTRIBUTES:
                        result[name] = getattr(context, name)[offset:stop]
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
                if context.complete_class is not None:
                    for name in COMPLETE_ATTRIBUTES:
                        result[name] = getattr(context, name)[offset:stop]
                if getattr(context, 'prior_class', None) is not None:
                    for name in PRIOR_ATTRIBUTES:
                        result[name] = getattr(context, name)[offset:stop]
                writer.write_points(result)
                for kind, subset_writer in subset_writers.items():
                    final_classes = (context.refined_class if context.refined_class is not None else context.fused_class)
                    if context.internal_type is not None:
                        final_classes = final_classes[offset:stop].copy()
                        final_classes[context.internal_type[offset:stop] == 5] = 4
                    else:
                        final_classes = final_classes[offset:stop]
                    mask = (((context.internal_type[offset:stop] > 0) & (context.internal_type[offset:stop] < 5)) if kind == 'internal' else
                            context.prior_class[offset:stop] == 3 if kind == 'prior-steel' else
                            context.prior_class[offset:stop] == 4 if kind == 'prior-noise' else
                            ((context.complete_class[offset:stop] == 3) & (context.complete_instance[offset:stop] > 0)) if kind == 'resolved' else
                            ((context.complete_class[offset:stop] == 3) & (context.complete_instance[offset:stop] == 0)) if kind == 'pending' else
                            context.complete_class[offset:stop] == 3 if kind == 'complete' else
                            (context.complete_class[offset:stop] == 4 if context.complete_class is not None else final_classes == 4) if kind == 'noise' else final_classes == kind)
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
    if context.shared_table_mask is not None:
        arrays.update(shared_table_mask=context.shared_table_mask[ids], partition_zones=context.partition_zone[ids])
    if context.geometry_class is not None:
        arrays["classes"] = context.geometry_class[ids]
        arrays["recovered"] = context.geometry_recovered[ids]
    if context.projection_class is not None:
        arrays["projection_classes"] = context.projection_class[ids]
        arrays["projection_layers"] = context.projection_layer[ids]
    if context.fused_class is not None:
        arrays.update(fused_classes=context.fused_class[ids], fused_regions=context.fused_region[ids],
                      fused_recovered=context.fused_recovered[ids], fused_reasons=context.fused_reason[ids],
                      fused_steel_score=context.fused_steel_score[ids], fused_steel_evidence=context.fused_steel_evidence[ids])
    if context.refined_class is not None:
        arrays.update(refined_classes=context.refined_class[ids], refined_regions=context.refined_region[ids],
                      refined_zones=context.refined_zone[ids], refined_changed=context.refined_changed[ids],
                      refined_reasons=context.refined_reason[ids])
    if context.internal_type is not None:
        arrays.update(internal_types=context.internal_type[ids], internal_instances=context.internal_instance[ids],
                      internal_segments=context.internal_segment[ids], internal_confidence=context.internal_confidence[ids])
    if context.complete_class is not None:
        arrays.update({name: getattr(context, name)[ids] for name in COMPLETE_ATTRIBUTES})
    if getattr(context, 'prior_class', None) is not None:
        arrays.update({name: getattr(context, name)[ids] for name in PRIOR_ATTRIBUTES})
    for name, array in arrays.items():
        array.tofile(preview_dir / f"{name}.bin")
    base = f"/runs/{run_id}/preview"
    return {"pointCount": size, "totalPointCount": count, "origin": origin.tolist(),
            "bounds": {"min": (lo - origin).tolist(), "max": (hi - origin).tolist()},
            "sampling": "deterministic evenly spaced source record indices; visualization only",
            **{name + "Url": f"{base}/{name}.bin" for name in ("positions", "normals", "colors", "valid")},
            "sourceIndicesUrl": f"{base}/source_indices.bin",
            **({"sharedTableMaskUrl": f"{base}/shared_table_mask.bin", "partitionZonesUrl": f"{base}/partition_zones.bin"}
               if context.shared_table_mask is not None else {}),
            **({"classesUrl": f"{base}/classes.bin", "recoveredUrl": f"{base}/recovered.bin"}
               if context.geometry_class is not None else {}),
            **({"projectionClassesUrl": f"{base}/projection_classes.bin", "projectionLayersUrl": f"{base}/projection_layers.bin"}
               if context.projection_class is not None else {}),
            **({"fusedClassesUrl": f"{base}/fused_classes.bin", "fusedRegionsUrl": f"{base}/fused_regions.bin",
                "fusedRecoveredUrl": f"{base}/fused_recovered.bin", "fusedReasonsUrl": f"{base}/fused_reasons.bin",
                "fusedSteelScoreUrl": f"{base}/fused_steel_score.bin", "fusedSteelEvidenceUrl": f"{base}/fused_steel_evidence.bin"}
               if context.fused_class is not None else {}),
            **({"refinedClassesUrl": f"{base}/refined_classes.bin", "refinedRegionsUrl": f"{base}/refined_regions.bin",
                "refinedZonesUrl": f"{base}/refined_zones.bin", "refinedChangedUrl": f"{base}/refined_changed.bin",
                "refinedReasonsUrl": f"{base}/refined_reasons.bin"} if context.refined_class is not None else {}),
            **({"internalTypesUrl": f"{base}/internal_types.bin", "internalInstancesUrl": f"{base}/internal_instances.bin",
                "internalSegmentsUrl": f"{base}/internal_segments.bin", "internalConfidenceUrl": f"{base}/internal_confidence.bin"}
               if context.internal_type is not None else {}),
            **({name + 'Url': f'{base}/{name}.bin' for name in COMPLETE_ATTRIBUTES}
               if context.complete_class is not None else {}),
            **({name + 'Url': f'{base}/{name}.bin' for name in PRIOR_ATTRIBUTES}
               if getattr(context, 'prior_class', None) is not None else {})}


@dataclass
class StepRun:
    manifest: dict
    context: PointCloudContext
    directory: Path


def run_from_source(source: Path, output_root: Path, *, k=32, workers=None, preview_limit=1000000, progress=None, through_step=1,
                    prior_mode='off', design_prior=None):
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
    if type(through_step) is not int or through_step not in (1, 2, 3, 4, 5, 6, 7):
        raise ValueError("through_step 必须是 1–7，7 对应页面第 06 步")
    if prior_mode not in PRIOR_MODES:
        raise ValueError('priorMode 必须为 off / geometry / topology')
    if through_step == 7 and (prior_mode == 'off' or not isinstance(design_prior, dict)):
        raise ValueError('第 06 步需要服务端设计模型、粗配准快照及设计辅助模式')
    if through_step < 7 and prior_mode != 'off':
        raise ValueError('设计辅助仅用于第 06 步（throughStep=7）')
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
        inventory = validate_snapshot(design_prior, source, digest) if prior_mode != 'off' else None
        positions, colors = load_positions(source, directory, progress)
        timing["readS"] = time.perf_counter() - t0
        stages = segment_points(positions, directory, k=k, workers=workers, through_step=min(through_step, 6),
                                source=source, progress=progress)
        context, arrays, shapes, computation = stages.context, stages.arrays, stages.shapes, stages.computation
        timing.update(stages.timing)
        preprocessing = stages.preprocessing
        count = len(positions)
        classification, projection, fusion = stages.classification, stages.projection, stages.fusion
        regions, refinement = stages.regions, stages.refinement
        internal_rebar, complete_rebar, execution = stages.internal_rebar, stages.complete_rebar, stages.execution
        prior_report = None
        if through_step == 7:
            for name, dtype in COMPLETE_ATTRIBUTES.items():
                shapes[name] = ((count,), dtype)
                arrays[name] = np.lib.format.open_memmap(directory / f'{name}.npy', mode='w+', dtype=dtype, shape=(count,))
            with threadpool_limits(limits=1):
                complete_rebar = refine_instances(context, internal_rebar, inventory, mode=prior_mode, workers=workers,
                    output={name: arrays[name] for name in COMPLETE_ATTRIBUTES}, progress=progress)
            complete_rebar['designReview'].update(snapshotFingerprint=design_prior['fingerprint'],
                modelInfo=design_prior.get('modelInfo', {}), preparation=design_prior.get('preparation', {}))
            timing['guidedInstancesS'] = complete_rebar['elapsedS']
        t0 = time.perf_counter()
        progress("持久化点云及法向量属性", 0, 1)
        las_name = "pointcloud-with-classes.las" if classification else "pointcloud-with-normals.las"
        subsets = {3: directory / 'steel-only.las', 2: directory / 'fixture-only.las'} if fusion else {}
        if internal_rebar is not None:
            subsets['internal'] = directory / 'internal-steel.las'
            subsets['noise'] = directory / 'noise-only.las'
        if complete_rebar is not None:
            subsets.update(complete=directory / 'complete-steel.las', noise=directory / 'noise-only.las',
                           resolved=directory / 'resolved-steel.las', pending=directory / 'pending-steel.las')
        if prior_report is not None:
            subsets.update({'prior-steel': directory/'prior-steel.las', 'prior-noise': directory/'prior-noise.las'})
        write_las(source, directory / las_name, context, subsets=subsets)
        if complete_rebar is not None:
            atomic_json(directory / 'complete-instances.json', complete_rebar)
        if prior_report is not None:
            atomic_json(directory/'design-prior.json', prior_report)
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
            "schema": "pointcloud-steps-v1", "algorithmVersion": VERSION + ("+design-guided-instances-v1" if through_step == 7 else ""), "runId": run_id,
            "createdAt": datetime.now(timezone.utc).isoformat(), "completed": True,
            "runMode": "fresh-source-all-steps", "orientation": "unoriented",
            "source": {"name": source.name, "path": str(source), "sha256": digest, "pointCount": count,
                       "sizeBytes": stamp[2], "unchangedDuringRun": True},
            "priorMode": prior_mode,
            "parameters": {"k": k, "effectiveK": computation["effectiveK"], "workers": workers, "kIncludesSelf": True, "throughStep": through_step},
            "validNormalCount": valid, "invalidNormalCount": count - valid,
            "steps": [{"id": "00-source", "pointCount": count}, {"id": "01-normals", "pointCount": count}]
                     + ([{"id": "01-table-removal", "pointCount": count, "dependsOn": ["01-normals"]},
                         {"id": "01-partition", "pointCount": count, "dependsOn": ["01-table-removal"]}] if preprocessing else [])
                     + ([{"id": "02-classification", "pointCount": count, "dependsOn": ["01-partition"]}] if classification else [])
                     + ([{"id": "02-projection", "pointCount": count, "dependsOn": "01-partition"}] if projection else [])
                     + ([{"id": "03-fusion", "pointCount": count, "dependsOn": ["02-classification", "02-projection"]}] if fusion else [])
                     + ([{"id": "05-internal-rebar", "pointCount": internal_rebar['pointCount'], "dependsOn": ["03-fusion"]}] if internal_rebar is not None else [])
                     + ([{'id': '06-design-guided-instances', 'pointCount': count, 'dependsOn': ['05-internal-rebar']}] if complete_rebar is not None else [])
                     + ([{'id': '07-design-prior', 'pointCount': count, 'dependsOn': ['06-complete-rebar']}] if prior_report is not None else []),
            **({"preprocessing": preprocessing} if preprocessing else {}),
            **({"classification": classification} if classification else {}),
            **({"projection": projection, "branchExecution": execution} if projection else {}),
            **({"fusion": fusion, "regions": regions} if fusion else {}),
            **({"refinement": refinement} if refinement else {}),
            **({"internalRebar": internal_rebar} if internal_rebar is not None else {}),
            **({"completeRebar": complete_rebar} if complete_rebar is not None else {}),
            **({'designPrior': prior_report, 'priorMode': prior_mode} if prior_report is not None else {}),
            "timings": timing,
            "performance": {**computation, "pointsPerSecond": count / timing["totalS"],
                            "cpuS": time.process_time() - cpu_started,
                            "peakRssMB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                            "peakRssScope": "process lifetime high-water mark", 'normalComputation': computation},
            "timingScope": "server pipeline wall time; excludes HTTP queue/download/browser render; OS page cache not flushed",
            "cache": {"scope": "live StepRun.context for subsequent steps of this run", "treeBuildCount": 1,
                      "sourceSha256": digest, "positionMutationAllowed": False,
                      "restart": "NPY attributes persist; KD tree is rebuilt, never unpickled"},
            "attributes": {"identity": "array row i = original source LAS record i (zero-based)",
                           "coordinateFrame": "native LAS XYZ, scaled float64; normals in the same frame",
                           "normal": "unit eigenvector of smallest PCA eigenvalue; sign not oriented",
                           "invalid": "normal_valid=0 and normal=(0,0,0) for degenerate neighborhoods",
                           **({"shared_table_mask": "1 tabletop excluded before both classifiers; source records retained",
                               "partition_zone": "0 unlocated, 1 strict inner, 2 frame band, 3 exterior; source XY ownership"} if preprocessing else {}),
                           **({"geometry_class": "Geometry retention: 1 table, 2 fixture, 3 steel including recovery and shared-region retention; independent evidence stored separately",
                               "geometry_recovered": "1 = measured rebar recovery during this run; only final steel rows"} if classification else {}),
                           **({"projection_class": "Projection retention: 1 table, 2 fixture, 3 steel including upper/lower height and shared-region retention; independent evidence stored separately",
                               "projection_layer": "0 outside detected Z bands; other ids refer to projection.layers"} if projection else {}),
                           **({"fused_class": "Evidence fusion: 1 table, 2 fixture, 3 rebar",
                               "fused_region": "0 table, 1 interior steel, 2 exterior steel, 3 fixture, 4 unlocated steel",
                               "fused_recovered": "1 = recovered projection junction or B non-steel-to-fused steel",
                               "fused_reason": "Rule id, see fusion.reasonNames; not a confidence probability",
                               "fused_steel_score": "0..1 rule support for steel, not calibrated probability; >=.9 protects retained steel in downstream denoising",
                               "fused_steel_evidence": "Bitmask: 1 geometry and recovery support, 2 projection shape or accepted upper/lower height support, 4 axis recovery, 8 shared retention candidate",
                               "source_record_index": "Zero-based original source record index, also retained in steel/fixture subset LAS"} if fusion else {}),
                           **({"refined_class": "Compatibility alias of fused_class; no additional classification pass",
                               "refined_region": "Compatibility alias of fused_region",
                               "refined_zone": "0 unlocated, 1 inner, 2 frame band, 3 outside; independent of semantic class",
                               "refined_changed": "1 iff refined_class differs from fused_class",
                               "refined_reason": "Rule id, see refinement.reasonNames"} if refinement else {}),
                           **({"internal_type": "0 outside strict inner steel scope; 1 lower, 2 upper, 3 straight web, 4 unassigned, 5 floating noise",
                               "internal_instance": "Run-local stable geometry-sorted cylinder ID; 0 unassigned/outside",
                               "internal_segment": "Cylinder fit part; a mildly curved horizontal bar may have multiple parts in one instance; each web straight segment is one instance",
                               "internal_confidence": "Geometric fit score, not calibrated probability; 0 unassigned/outside"} if internal_rebar is not None else {}),
                           **({'complete_class': '1 table, 2 fixture, 3 rebar, 4 noise',
                               'complete_instance': 'Observed instance IDs after design-assisted merge/split; 0 pending or non-rebar',
                               'complete_segment': 'Internal fit part or exterior cluster attachment segment; does not imply a cylinder fit to hook points',
                               'complete_cluster': 'Pre-refinement connected component of retained unassigned steel; 0 existing internal instance/out of scope',
                               'complete_confidence': 'Internal fit score or exterior cluster attachment score; not pointwise hook fit or calibrated probability'} if complete_rebar is not None else {}),
                           "lasExtraBytes": {**ATTRIBUTES, **(SCENE_ATTRIBUTES if preprocessing else {}), **(CLASS_ATTRIBUTES if classification else {}),
                                             **(PROJECTION_ATTRIBUTES if projection else {}), **(FUSION_LAS_ATTRIBUTES if fusion else {}),
                                             **(REFINEMENT_ATTRIBUTES if refinement else {}), **(INTERNAL_ATTRIBUTES if internal_rebar is not None else {}), **(COMPLETE_ATTRIBUTES if complete_rebar is not None else {}),
                                             **(PRIOR_ATTRIBUTES if prior_report is not None else {})},
                           "columns": {name: name + ".npy" for name in ("positions", "colors", *shapes)}},
            "preview": preview,
            "files": {**({'completeSteelLasUrl': f'/runs/{run_id}/complete-steel.las',
                                'noiseLasUrl': f'/runs/{run_id}/noise-only.las',
                                'resolvedSteelLasUrl': f'/runs/{run_id}/resolved-steel.las',
                                'pendingSteelLasUrl': f'/runs/{run_id}/pending-steel.las',
                                'completeInstancesUrl': f'/runs/{run_id}/complete-instances.json'} if complete_rebar is not None else {}),
                      **({'priorSteelLasUrl': f'/runs/{run_id}/prior-steel.las', 'priorNoiseLasUrl': f'/runs/{run_id}/prior-noise.las',
                          'designPriorUrl': f'/runs/{run_id}/design-prior.json'} if prior_report is not None else {}),
                      "lasUrl": f"/runs/{run_id}/{las_name}", "manifestUrl": f"/runs/{run_id}/manifest.json",
                      "subsetClassAttribute": "refined_class with internal_type=5 mapped to noise" if internal_rebar is not None else "refined_class" if refinement else "fused_class" if fusion else None,
                      **({"steelLasUrl": f"/runs/{run_id}/steel-only.las", "fixtureLasUrl": f"/runs/{run_id}/fixture-only.las"} if fusion else {}),
                      **({"noiseLasUrl": f"/runs/{run_id}/noise-only.las", "internalSteelLasUrl": f"/runs/{run_id}/internal-steel.las", "internalInstancesUrl": f"/runs/{run_id}/internal-instances.json"} if internal_rebar is not None else {})},
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
