"""Disk-backed source records, spatial cores and complete bounded halos."""
from __future__ import annotations

import hashlib
from pathlib import Path
import numpy as np

RECORD = np.dtype([("source_index", "<u8"), ("xyz", "<f8", (3,))])


class SpatialBudgetExceeded(ValueError):
    """Complete raw support cannot be materialized within the spatial budget."""


class SpatialStore:
    def __init__(self, directory, params):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.p = params
        self.cells = {}
        self.count = 0
        self.fingerprint = ""

    def build(self, chunks):
        digest = hashlib.sha256()
        last_index = -1
        for indices, xyz in chunks:
            raw_indices=np.asarray(indices)
            if not np.issubdtype(raw_indices.dtype,np.integer) or np.any(raw_indices<0):
                raise ValueError('source indices must be non-negative integers')
            indices = np.asarray(indices, dtype=np.uint64)
            xyz = np.asarray(xyz, dtype=np.float64)
            if indices.ndim != 1:
                raise ValueError("source indices must be one-dimensional")
            if xyz.shape != (len(indices), 3):
                raise ValueError("source indices and coordinates disagree")
            # Validate the reader's ordering before discarding invalid XYZ rows:
            # a NaN row must not let a later chunk silently reuse an index.
            if len(indices) and (int(indices[0]) <= last_index or np.any(indices[1:] <= indices[:-1])):
                raise ValueError("source reader indices must increase without renumbering")
            if len(indices):
                last_index = int(indices[-1])
            finite = np.isfinite(xyz).all(axis=1)
            indices, xyz = indices[finite], xyz[finite]
            if not len(indices):
                continue
            records = np.empty(len(indices), RECORD)
            records["source_index"], records["xyz"] = indices, xyz
            digest.update(records.tobytes())
            keys = np.floor(xyz/self.p.block_size).astype(np.int64)
            unique, inverse = np.unique(keys, axis=0, return_inverse=True)
            order = np.argsort(inverse, kind="stable")
            counts = np.bincount(inverse)
            offset = 0
            for key, count in zip(unique, counts):
                key = tuple(map(int, key))
                path = self.directory/("_".join(map(str,key))+".bin")
                with path.open("ab") as out:
                    records[order[offset:offset+count]].tofile(out)
                self.cells[key] = (path, self.cells.get(key, (None, 0))[1]+int(count))
                offset += count
            self.count += len(xyz)
        self.fingerprint = digest.hexdigest()
        if self.count < 3:
            raise ValueError("V5 requires at least three finite source points")
        return self

    def records(self, key):
        path, count = self.cells[key]
        return np.memmap(path, dtype=RECORD, mode="r", shape=(count,))

    def query(self, lo, hi):
        """Half-open box; bounded accumulation, never concatenate the raw cloud."""
        parts, count = [], 0
        for key in sorted(self.cells):
            a = np.asarray(key)*self.p.block_size
            if np.any(a >= hi) or np.any(a+self.p.block_size <= lo):
                continue
            data = self.records(key)
            for start in range(0, len(data), 100_000):
                rows = data[start:start+100_000]
                mask = np.all((rows["xyz"] >= lo) & (rows["xyz"] < hi), axis=1)
                selected = np.asarray(rows[mask])
                count += len(selected)
                if count > self.p.neighbourhood_point_limit:
                    raise SpatialBudgetExceeded("spatial neighbourhood exceeds V5 memory budget")
                if len(selected):
                    parts.append(selected)
        if not parts:
            return np.empty(0, RECORD)
        result = np.concatenate(parts)
        return result[np.argsort(result["source_index"], kind="stable")]

    def cores(self):
        """Split dense cores before materializing their buffered neighbourhood."""
        def count_side(key, lo, hi, axis, mid):
            """Count a child by streaming memmap records, never dense arrays."""
            count = 0
            data = self.records(key)
            for start in range(0, len(data), 100_000):
                xyz = data[start:start + 100_000]["xyz"]
                mask = np.all((xyz >= lo) & (xyz < hi), axis=1)
                count += int(np.count_nonzero(mask & (xyz[:, axis] < mid)))
            return count

        def occupied_bounds(key, lo, hi):
            minimum = np.full(3, np.inf)
            maximum = np.full(3, -np.inf)
            data = self.records(key)
            for start in range(0, len(data), 100_000):
                xyz = data[start:start + 100_000]["xyz"]
                chosen = xyz[np.all((xyz >= lo) & (xyz < hi), axis=1)]
                if len(chosen):
                    minimum = np.minimum(minimum, chosen.min(axis=0))
                    maximum = np.maximum(maximum, chosen.max(axis=0))
            # Cores are half-open. Advance the upper edge by one representable
            # float so a point at the observed maximum remains in its core.
            return minimum, np.nextafter(maximum, np.inf)

        def split(key, lo, hi, count, depth=0):
            if count <= self.p.block_point_limit:
                yield key, lo, hi
                return
            if depth >= 24:
                raise ValueError("coincident source cluster exceeds V5 block budget")
            # Split the occupied bounds rather than the parent cell midpoint.
            # A cluster near one cell edge must not burn recursion depth crossing
            # empty space before it can be divided.
            occupied_lo, occupied_hi = occupied_bounds(key, lo, hi)
            split_axis = None
            left_count = 0
            for axis in np.argsort(occupied_hi - occupied_lo)[::-1]:
                mid = (occupied_lo[axis] + occupied_hi[axis]) / 2
                if not occupied_lo[axis] < mid < occupied_hi[axis]:
                    continue
                candidate = count_side(key, lo, hi, int(axis), mid)
                if 0 < candidate < count:
                    split_axis, left_count = int(axis), candidate
                    break
            if split_axis is None:
                raise ValueError("coincident source cluster exceeds V5 block budget")
            mid = (occupied_lo[split_axis] + occupied_hi[split_axis]) / 2
            left_hi = occupied_hi.copy(); left_hi[split_axis] = mid
            right_lo = occupied_lo.copy(); right_lo[split_axis] = mid
            yield from split(key, occupied_lo, left_hi, left_count, depth + 1)
            yield from split(key, right_lo, occupied_hi, count - left_count, depth + 1)
        for key in sorted(self.cells):
            lo = np.asarray(key,dtype=float)*self.p.block_size
            yield from split(key, lo, lo+self.p.block_size, self.cells[key][1])
