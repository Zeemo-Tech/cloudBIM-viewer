"""A separate computational population after the design cloth hard deletion.

Source-order arrays exist only for export/diagnostics. All geometric indexes,
normal estimates, rasters and classifier caches belong to retained rows.
"""
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from .pointcloud_normals import PointCloudContext, estimate_normals
from .projection_geometry_classifier import prepare_projection


NORMAL_NAMES = ('normals', 'normal_valid', 'curvature', 'neighbor_radius')
PREPROCESS_NAMES = ('shared_table_mask', 'partition_zone', 'shared_layer', 'shared_floating_noise')


def build_retained_context(source, ids, directory, *, k, workers, progress):
    directory = Path(directory); directory.mkdir(exist_ok=True)
    xyz = np.ascontiguousarray(source.positions[ids])
    xyz.flags.writeable = False
    context = PointCloudContext(xyz, cKDTree(xyz, leafsize=32))
    context.source_record_indices = ids
    arrays = {}
    for name in NORMAL_NAMES:
        value = getattr(source, name)
        shape = (len(ids), *value.shape[1:])
        arrays[name] = np.lib.format.open_memmap(directory/f'{name}.npy', mode='w+', dtype=value.dtype, shape=shape)
    if len(ids) >= 3:
        estimate_normals(context, k=k, workers=workers, output=arrays,
                         progress=lambda done, total: progress('01D：仅用包络内点重建邻域法向', done, total))
    else:
        for name, value in arrays.items():
            value[:] = 0; setattr(context, name, value)
    for name in PREPROCESS_NAMES:
        value = np.asarray(getattr(source, name)[ids]).copy()
        value.flags.writeable = False
        setattr(context, name, value)
    context.region_cache = source.region_cache
    context.scene_cache = dict(source.scene_cache)
    context.scene_cache['region_owned'] = source.scene_cache['region_owned'][ids].copy()
    if len(ids):
        # Keep the already measured table/frame metadata, but rebuild ALL
        # population evidence. Slicing the old raster would retain deleted votes.
        prepared = prepare_projection(xyz, context.normals, context.normal_valid, progress=progress,
            fixed_table=source.scene_cache['projection']['table'], fixed_table_mask=context.shared_table_mask)
        owned = context.scene_cache['region_owned']
        prepared['fixture_candidate_pixels'] = (np.bincount(prepared['source_to_pixel'][~owned & ~context.shared_table_mask.astype(bool)],
            minlength=prepared['pixels']) > 0).reshape(prepared['ny'], prepared['nx'])
        for value in prepared.values():
            if isinstance(value, np.ndarray): value.flags.writeable = False
        context.scene_cache['projection'] = prepared
    else:
        context.scene_cache['projection'] = None
    context.scene_cache['region_owned'].flags.writeable = False
    np.save(directory/'source_record_indices.npy', ids.astype(np.uint64))
    return context, arrays


def scatter_outputs(source, retained, ids, arrays, source_arrays, shapes, directory):
    """Restore diagnostics to source order without putting removed rows in a tree."""
    count = len(source.positions)
    for name, values in arrays.items():
        if name in NORMAL_NAMES or name in PREPROCESS_NAMES: continue
        shape = (count, *values.shape[1:])
        target = np.lib.format.open_memmap(Path(directory)/f'{name}.npy', mode='w+', dtype=values.dtype, shape=shape)
        fill = 4 if name in ('geometry_class', 'projection_class', 'fused_class', 'refined_class', 'complete_class', 'fused_region', 'refined_region') else 5 if name == 'internal_type' else 8 if name == 'fused_reason' else 0
        target[:] = source.partition_zone if name == 'refined_zone' else fill
        target[ids] = values
        source_arrays[name] = target; shapes[name] = (shape, values.dtype.str)
        setattr(source, name, target)
    # Computational caches explicitly retain their compact source mapping.
    # Consumers that calculate further must use retained_context, not this
    # source-order export context (see the design-guided continuation).
    for name in ('classification_cache', 'projection_cache', 'fusion_cache', 'refinement_cache', 'internal_rebar_cache', 'dimension_priors'):
        setattr(source, name, getattr(retained, name))
    for value in arrays.values():
        if hasattr(value, 'flush'): value.flush()
    source.tree = None  # The full-source tree must never be reused after deletion.
    source.retained_context = retained
    source.retained_source_indices = ids


def annotate_population(reports, source, ids):
    count = len(source.positions); removed = count-len(ids)
    population = {'sourcePointCount': count, 'inputPointCount': len(ids), 'excludedBeforeComputation': removed,
                  'policy': 'only envelope-interior rows enter all post-01D trees, rasters, neighborhoods and fitting',
                  'sourceIndexFile': 'retained/source_record_indices.npy'}
    for report in reports:
        if report is not None: report['computationalPopulation'] = dict(population)
    for report, name in zip(reports[:3], ('geometry_class', 'projection_class', 'fused_class')):
        if report is None: continue
        report['counts'] = dict(zip(('table', 'fixture', 'rebar', 'noise'), map(int, np.bincount(getattr(source,name), minlength=5)[1:5])))
        veto = report.setdefault('floatingDenoising', {})
        veto.update(designHardRemovedPointCount=removed, excludedBeforeComputation=True,
                    removedPointCount=veto.get('removedPointCount', 0)+removed, additionalDesignRemovedPointCount=removed)
    internal = reports[-1]
    if internal is not None:
        internal['retainedScopePointCount'] = internal['pointCount']
        internal['counts'] = dict(zip(('lower', 'upper', 'web', 'unassigned', 'noise'), map(int, np.bincount(source.internal_type, minlength=6)[1:6])))
        internal['pointCount'] = sum(internal['counts'].values())
        denoising = internal.setdefault('denoising', {})
        denoising.update(designHardRemovedPointCount=removed, excludedBeforeComputation=True,
                         removedPointCount=denoising.get('removedPointCount', 0)+removed)
    return population
