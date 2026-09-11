"""Local circular-surface evidence for crowded bars, independent of projections."""
from concurrent.futures import ThreadPoolExecutor
from itertools import islice

import numpy as np
from scipy.spatial import cKDTree


def recover_cylindrical_cells(points, projectors, tree, features, candidates, *,
                              strong_bars, protected, voxel_size=.003, workers=1):
    # Import here to keep the shared projector representation in one module.
    from .normal_geometry_classifier import matrix6, projector6

    rows = np.flatnonzero(candidates)
    accepted = np.zeros(len(points), bool)
    axes = np.zeros((len(points), 3), np.float32)
    centers = np.zeros((len(points), 3), np.float64)
    radii = np.zeros(len(points), np.float32)
    anchored_cells = np.zeros(len(points), bool)
    if not len(rows):
        return accepted, axes, centers, radii, anchored_cells
    _, normal_axes = np.linalg.eigh(matrix6(projectors))
    normals = normal_axes[:, :, 2]
    padded_points = np.vstack((points, np.zeros((1, 3))))
    padded_q = np.vstack((projectors, np.zeros((1, 6))))
    padded_normals = np.vstack((normals, np.zeros((1, 3))))
    component = features['support_component']
    fragment = features['fragment_support']
    anchor_tree = cKDTree(points[strong_bars]) if len(strong_bars) else None

    def process(start):
        query = rows[start:start+256]
        distances, ids = tree.query(points[query], k=min(256, len(points)),
                                    distance_upper_bound=.04, workers=1)
        if ids.ndim == 1:
            distances, ids = distances[:, None], ids[:, None]
        safe = np.minimum(ids, len(points)-1)
        valid = np.isfinite(distances)
        valid &= ((component[query, None] == component[safe])
                  | fragment[query, None] | fragment[safe])
        delta = padded_points[ids]-points[query, None]
        mass = np.maximum(valid.sum(axis=1), 1)
        avg = np.einsum('bk,bki->bi', valid, padded_q[ids])/mass[:, None]
        eigen, vectors = np.linalg.eigh(matrix6(avg))
        # Parallel cylinders share a normal-null direction even when spatial
        # PCA mistakes their combined cross-section for a wide/round object.
        axis = vectors[:, :, 0]
        radial = normals[query]-np.sum(normals[query]*axis, axis=1)[:, None]*axis
        radial /= np.maximum(np.linalg.norm(radial, axis=1)[:, None], 1e-12)
        other = np.cross(axis, radial)
        axial = np.einsum('bki,bi->bk', delta, axis)
        transverse = delta-axial[:, :, None]*axis[:, None]
        n = padded_normals[ids]
        na = np.einsum('bki,bi->bk', n, radial)
        nb = np.einsum('bki,bi->bk', n, other)
        nlength2 = np.maximum(na*na+nb*nb, 1e-12)
        cos4 = (na**4-6*na*na*nb*nb+nb**4)/(nlength2*nlength2)
        sin4 = 4*na*nb*(na*na-nb*nb)/(nlength2*nlength2)
        eligible = (eigen[:, 0] < .06) & (eigen[:, 1] > .08) & (mass >= 24)
        if anchor_tree is not None:
            anchor_distance, anchor_ids = anchor_tree.query(points[query], k=min(24, len(strong_bars)),
                                                            distance_upper_bound=.05, workers=1)
            if anchor_ids.ndim == 1:
                anchor_distance, anchor_ids = anchor_distance[:, None], anchor_ids[:, None]
            anchor_ids = strong_bars[np.minimum(anchor_ids, len(strong_bars)-1)]
            anchor_valid = np.isfinite(anchor_distance)
            anchor_valid &= np.abs(np.einsum('bki,bi->bk', features['axis'][anchor_ids], axis)) > .94
            anchor_valid &= ((component[query, None] == component[anchor_ids])
                             | fragment[query, None] | fragment[anchor_ids])
            anchor_delta = features['axis_center'][anchor_ids]-points[query, None]
            anchor_along = np.einsum('bki,bi->bk', anchor_delta, axis)
            anchor_transverse = anchor_delta-anchor_along[:, :, None]*axis[:, None]
        best = np.zeros(len(query), np.int32)
        # Search both orientations of an unoriented normal. Each model must
        # explain an observed circular shell and rotating normals, not merely
        # points anywhere inside a larger protection cylinder.
        for radius in (.003, .004, .005, .006, .008, .010):
            for sign in (-1, 1):
                offset = sign*radius*radial
                cross = transverse-offset[:, None]
                distance = np.linalg.norm(cross, axis=2)
                direction = cross/np.maximum(distance[:, :, None], 1e-12)
                alignment = np.einsum('bkc,bkc->bk', padded_q[ids],
                    projector6(direction.reshape(-1, 3)).reshape(*direction.shape[:2], 6)*[1, 1, 1, 2, 2, 2])
                shell = valid & (np.abs(distance-radius) <= voxel_size*.4) & (alignment > .8)
                size = shell.sum(axis=1)
                interior = np.count_nonzero(valid & (distance < radius-voxel_size*.4), axis=1)
                denominator = np.maximum(size, 1)
                balance = np.linalg.norm(np.einsum('bk,bki->bi', shell, direction), axis=1)/denominator
                span = (np.max(np.where(shell, axial, -np.inf), axis=1)
                        - np.min(np.where(shell, axial, np.inf), axis=1))
                aa = np.sum(shell*na*na/nlength2, axis=1)/denominator
                bb = np.sum(shell*nb*nb/nlength2, axis=1)/denominator
                ab = np.sum(shell*na*nb/nlength2, axis=1)/denominator
                spread = .5*(aa+bb-np.sqrt((aa-bb)**2+4*ab*ab))
                fourfold = np.hypot(np.sum(shell*cos4, axis=1), np.sum(shell*sin4, axis=1))/denominator
                anchored = np.zeros(len(query), bool)
                if anchor_tree is not None:
                    supports = (anchor_valid & (np.linalg.norm(anchor_transverse-offset[:, None], axis=2) <= .003)
                                & (np.abs(features['width'][anchor_ids]*.35-radius) <= .0025))
                    anchor_span = (np.max(np.where(supports, anchor_along, -np.inf), axis=1)
                                   - np.min(np.where(supports, anchor_along, np.inf), axis=1))
                    anchored = (supports.sum(axis=1) >= 3) & (anchor_span >= .006)
                strict = eligible & ~protected[query] & (spread >= .12) & (fourfold < .75) & (balance < .75)
                continuation = (anchored & (eigen[:, 0] < .1) & (spread >= .06)
                                & (fourfold < .9) & (balance < .95))
                good = ((strict | continuation) & (size >= 16) & (span >= .024)
                        & (interior <= np.maximum(2, size*.05)))
                update = good & (size > best)
                chosen = query[update]
                accepted[chosen] = True
                axes[chosen] = axis[update]
                centers[chosen] = points[chosen]+offset[update]
                radii[chosen] = radius
                anchored_cells[chosen] = anchored[update]
                best[update] = size[update]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Workers write only their disjoint query rows; bound outstanding jobs.
        starts = iter(range(0, len(rows), 256))
        while batch := list(islice(starts, workers*2)):
            list(pool.map(process, batch))
    return accepted, axes, centers, radii, anchored_cells
