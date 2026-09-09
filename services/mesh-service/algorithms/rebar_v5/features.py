"""Conservative cleanup and multi-scale point features with spatial support."""
from __future__ import annotations
import os
import numpy as np
from ..spatial_keys import unique_integer_rows
from scipy.spatial import cKDTree

try:
    _PCA_QUERY_WORKERS = min(4, len(os.sched_getaffinity(0)))
except (AttributeError, OSError):
    _PCA_QUERY_WORKERS = min(4, os.cpu_count() or 1)


def _query_workers(count):
    # Only parallelize substantial neighbour searches. Small face probes lose
    # time to thread startup; BLAS remains single-threaded in the adapter.
    return _PCA_QUERY_WORKERS if count >= 1024 else 1


def denoise(points, queries, p):
    points = np.asarray(points, dtype=float)
    queries = np.asarray(queries, dtype=float)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("denoise support must be an Nx3 array")
    if queries.ndim != 2 or queries.shape[1:] != (3,):
        raise ValueError("denoise queries must be an Nx3 array")
    if not len(queries):
        return np.empty(0, bool), np.empty(0, bool)
    finite_points = np.isfinite(points).all(axis=1)
    points = points[finite_points]
    noise = np.ones(len(queries), dtype=bool)
    suspect = np.zeros(len(queries), dtype=bool)
    finite_queries = np.isfinite(queries).all(axis=1)
    if not len(points):
        return noise, suspect

    tree = cKDTree(points)
    k = min(9, len(points))
    # Each support point's own nearest-neighbour spacing is a local density
    # reference. It avoids judging a sparse web bar against a dense table.
    support_spacing = np.full(len(points), np.inf)
    if len(points) > 1:
        for start in range(0, len(points), p.query_batch_size):
            stop = min(start + p.query_batch_size, len(points))
            distances, _ = tree.query(points[start:stop], k=2, workers=1)
            support_spacing[start:stop] = distances[:, 1]

    query_indices = np.flatnonzero(finite_queries)
    for start in range(0, len(query_indices), p.query_batch_size):
        rows = query_indices[start:start + p.query_batch_size]
        # The store provides this complete halo already.  Sparse physical scans
        # must be judged against that support, not an arbitrary 25 mm radius.
        distances, indices = tree.query(queries[rows], k=k, distance_upper_bound=p.halo, workers=1)
        if k == 1:
            distances, indices = distances[:, None], indices[:, None]
        finite = np.isfinite(distances)
        # Queries are normally part of support, so the first neighbour is self.
        # Keep the code correct when a caller supplies an independent query set.
        self_match = finite[:, 0] & (distances[:, 0] <= 1e-12)
        counts = finite.sum(axis=1) - self_match.astype(int)
        safe = np.minimum(indices, len(points) - 1)
        neighbour_spacing = support_spacing[safe]
        local_spacing = np.nanmedian(np.where(finite, neighbour_spacing, np.nan), axis=1)
        local_spacing = np.where(np.isfinite(local_spacing), local_spacing, p.min_radius)
        nearest = distances[:, 1] if k > 1 else np.full(len(rows), np.inf)
        nearest = np.where(self_match, nearest, distances[:, 0])
        density_gap = nearest > p.noise_distance_ratio * np.maximum(local_spacing, 1e-12)
        # Small compact components are noise even when their internal spacing is
        # tight. A linear/hooked fragment is deliberately retained for later
        # primitive growth; this protects short visible bar ends.
        # A sparse plane can legitimately have only a few neighbours at 36 mm
        # spacing.  Treat it as noise only when the entire small component is
        # physically compact, rather than just under-populated.
        extent = np.where(finite, distances, 0).max(axis=1)
        compact_small = (counts <= 4) & (extent <= p.noise_radius * .25)
        compact_rows = np.flatnonzero(compact_small)
        if len(compact_rows):
            # Shape only affects this small-component predicate. Dense support
            # cannot satisfy it, so fitting every point's covariance is wasted.
            cloud = points[safe[compact_rows]]
            weights = finite[compact_rows, :, None]
            center = (cloud * weights).sum(axis=1) / np.maximum(finite[compact_rows].sum(axis=1)[:, None], 1)
            delta = (cloud - center[:, None]) * weights
            eig = np.linalg.eigvalsh(np.einsum("nki,nkj->nij", delta, delta))
            elongated = (eig[:, -1] - eig[:, -2]) / np.maximum(eig[:, -1], 1e-15) > 0.65
            compact_small[compact_rows] &= ~elongated
        # A local spacing gap is useful evidence, but not proof: a genuine
        # sparse table sample can border a denser patch. Only isolation or a
        # physically compact disconnected component is removed here.
        local_noise = (counts == 0) | compact_small
        noise[rows] = local_noise
        suspect[rows] = ((counts < p.feature_min_neighbors) | density_gap) & ~local_noise
    return noise, suspect


def prepare_pca_context(support):
    """Prepare finite feature support and its index for repeated PCA queries."""
    support = np.asarray(support, dtype=float)
    if support.ndim != 2 or support.shape[1:] != (3,):
        raise ValueError("feature support must be an Nx3 array")
    support = support[np.isfinite(support).all(axis=1)]
    return support, cKDTree(support) if len(support) else None


def pca_features(support, query, radius, p, context=None):
    support = np.asarray(support, dtype=float)
    query = np.asarray(query, dtype=float)
    if support.ndim != 2 or support.shape[1:] != (3,):
        raise ValueError("feature support must be an Nx3 array")
    if query.ndim != 2 or query.shape[1:] != (3,):
        raise ValueError("feature query must be an Nx3 array")
    n = len(query)
    out = {"normal":np.zeros((n,3),np.float32),"tangent":np.zeros((n,3),np.float32),
           "linearity":np.zeros(n,np.float32),"planarity":np.zeros(n,np.float32),
           "spacing":np.zeros(n,np.float32),"neighbor_count":np.zeros(n,np.uint16),
           "valid":np.zeros(n,np.uint8),"normal_valid":np.zeros(n,np.uint8),
           "tangent_valid":np.zeros(n,np.uint8),"neighborhood_radius":np.zeros(n,np.float32)}
    valid_queries = np.isfinite(query).all(axis=1)
    if not n or not valid_queries.any():
        return out
    if context is None:
        support, tree = prepare_pca_context(support)
    else:
        support, tree = context
    if not len(support):
        return out
    k = min(p.feature_max_neighbors,len(support))

    def statistics(distances, indices):
        if k == 1:
            distances, indices = distances[:, None], indices[:, None]
        finite = np.isfinite(distances)
        counts = finite.sum(axis=1)
        cloud = support[np.minimum(indices, len(support)-1)]
        mean = (cloud*finite[...,None]).sum(axis=1)/np.maximum(counts[:,None],1)
        centered = (cloud-mean[:,None])*finite[...,None]
        values, vectors = np.linalg.eigh(np.einsum("nki,nkj->nij",centered,centered)/np.maximum(counts-1,1)[:,None,None])
        return distances, finite, counts, values, vectors

    valid_indices = np.flatnonzero(valid_queries)
    for start in range(0,len(valid_indices),p.query_batch_size):
        output_rows=valid_indices[start:start+p.query_batch_size]
        d,idx=tree.query(query[output_rows],k=k,distance_upper_bound=radius,workers=_query_workers(len(output_rows)))
        d, valid, counts, values, vectors = statistics(d, idx)
        # Surface normals need rank two; tangents only need a non-zero first
        # principal direction.  Retry insufficient primary support inside the
        # existing halo and record that adaptive search for downstream audit.
        tangent_good = (counts >= p.feature_min_neighbors) & (values[:,2] > 1e-15)
        normal_good = tangent_good & (values[:,1] > 1e-15)
        retry = ~normal_good if radius == p.surface_radius else ~tangent_good
        used_radius = np.full(len(output_rows), radius, dtype=np.float32)
        if retry.any() and p.halo > radius:
            retry_rows = np.flatnonzero(retry)
            retry_d, retry_idx = tree.query(query[output_rows[retry_rows]], k=k, distance_upper_bound=p.halo, workers=_query_workers(len(retry_rows)))
            retry_d, retry_valid, retry_counts, retry_values, retry_vectors = statistics(retry_d, retry_idx)
            d[retry_rows] = retry_d
            valid[retry_rows] = retry_valid
            counts[retry_rows] = retry_counts
            values[retry_rows] = retry_values
            vectors[retry_rows] = retry_vectors
            used_radius[retry_rows] = p.halo
        scale=np.maximum(values[:,2],1e-15)
        # The adaptive halo may contain a physically sparse plane with only
        # three non-collinear samples, or a bar end with two collinear samples.
        # Those are sufficient for the respective stable direction; dense data
        # keeps the stricter configured threshold at the primary radius.
        adaptive = used_radius > radius
        tangent_min = np.where(adaptive, 2, p.feature_min_neighbors)
        normal_min = np.where(adaptive, 3, p.feature_min_neighbors)
        tangent_good=(counts>=tangent_min)&(values[:,2]>1e-15)
        normal_good=(counts>=normal_min)&(values[:,1]>1e-15)
        for name,array,good in (("normal",vectors[:,:,0],normal_good),("tangent",vectors[:,:,2],tangent_good)):
            major=np.argmax(np.abs(array),axis=1)
            array*=np.where(array[np.arange(len(array)),major]<0,-1,1)[:,None]
            array[~good]=0
            out[name][output_rows]=array
        out["linearity"][output_rows]=np.where(tangent_good,(values[:,2]-values[:,1])/scale,0)
        out["planarity"][output_rows]=np.where(normal_good,(values[:,1]-values[:,0])/scale,0)
        out["neighbor_count"][output_rows]=counts
        out["valid"][output_rows]=normal_good if radius==p.surface_radius else tangent_good
        out["normal_valid"][output_rows]=normal_good
        out["tangent_valid"][output_rows]=tangent_good
        out["neighborhood_radius"][output_rows]=used_radius
        if k>1: out["spacing"][output_rows]=np.where(np.isfinite(d[:,1]),d[:,1],0)
    return out


def multiscale(support, query, p):
    # A dense scanner otherwise makes the nearest 64 points occupy only a
    # tiny tangent patch even at the nominal axis radius. Spatial support
    # thinning changes neighbourhood sampling, not the output point set.
    if len(support):
        keys=np.floor(np.asarray(support)/p.detection_voxel_size).astype(np.int64)
        _,rows=unique_integer_rows(keys,return_index=True)
        axis_support=np.asarray(support)[np.sort(rows)]
    else:axis_support=support
    result={f"surface_{name}":value for name,value in pca_features(support,query,p.surface_radius,p).items()}
    result.update({f"axis_{name}":value for name,value in pca_features(axis_support,query,p.axis_radius,p).items()})
    return result
