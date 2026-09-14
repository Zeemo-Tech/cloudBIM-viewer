"""Stable integer grid grouping without structured-record comparison sorting."""
import numpy as np


def _sorted_rows(keys):
    keys = np.asarray(keys)
    if keys.ndim != 2 or keys.shape[1] == 0 or keys.dtype.kind not in 'iu':
        raise ValueError('spatial keys must be a two-dimensional integer array')
    # lexsort is stable: the first row in each group is the lowest source row.
    # Keep integer columns separate; float conversion or packed strides would
    # lose precision/overflow for large or widely separated world coordinates.
    order = np.lexsort(keys.T[::-1])
    ordered = keys[order]
    starts = np.r_[0, np.flatnonzero(np.any(ordered[1:] != ordered[:-1], axis=1)) + 1] if len(keys) else np.empty(0, np.intp)
    return keys, order, starts


def unique_integer_rows(keys, *, return_index=False, return_inverse=False):
    """Equivalent of unique(axis=0) for integer grids with >=1 coordinate."""
    keys, order, starts = _sorted_rows(keys)
    first = order[starts]
    result = [keys[first]]
    if return_index:
        result.append(first)
    if return_inverse:
        inverse = np.empty(len(keys), np.intp)
        inverse[order] = np.repeat(np.arange(len(starts)), np.diff(np.r_[starts, len(keys)]))
        result.append(inverse)
    return tuple(result) if len(result) > 1 else result[0]


def integer_row_groups(keys):
    """Yield lexicographic cells and their source-ordered rows in one sort."""
    keys, order, starts = _sorted_rows(keys)
    for start, stop in zip(starts, np.r_[starts[1:], len(keys)]):
        rows = order[start:stop]
        yield keys[rows[0]], rows


def integer_pair_membership(keys, observed):
    """Exact occupied-cell lookup with bounded, collision-free integer keys."""
    keys, observed = np.asarray(keys), np.asarray(observed)
    if not len(keys) or not len(observed):
        return np.zeros(len(keys), bool)
    # Two signed 32-bit coordinates fit bijectively in one uint64. Wider or
    # noninteger model cells retain tuple equality, including uint64 extremes.
    limit = np.iinfo(np.int32)
    if (keys.dtype.kind in 'iu' and observed.dtype.kind in 'iu'
            and keys.min() >= limit.min and keys.max() <= limit.max
            and observed.min() >= limit.min and observed.max() <= limit.max):
        def packed(rows):
            columns = rows.astype(np.uint32).astype(np.uint64)
            return (columns[:, 0] << np.uint64(32)) | columns[:, 1]
        known = np.sort(packed(observed))
        query = packed(keys)
        positions = np.searchsorted(known, query)
        return (positions < len(known)) & (known[np.minimum(positions, len(known)-1)] == query)
    cells = {tuple(row) for row in observed}
    return np.fromiter((tuple(row) in cells for row in keys), bool, count=len(keys))
