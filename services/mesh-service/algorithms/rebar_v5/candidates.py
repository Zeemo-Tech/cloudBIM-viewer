"""Bounded point index for conservative model broad-phase queries."""
import numpy as np


class PointBoundsIndex:
    """Intersect sorted coordinate ranges; keep exact tests in the caller.

    At most 48 bytes per point are retained. Oversized blocks fall back to a
    linear box scan, so an index is never required to classify a point.
    """
    def __init__(self, points, budget_bytes=64 * 1024**2):
        self.points = np.asarray(points, dtype=float)
        self.orders = self.coordinates = None
        if len(points) * 48 <= max(0, budget_bytes):
            self.orders = [np.argsort(self.points[:, axis], kind='stable') for axis in range(3)]
            self.coordinates = [self.points[order, axis] for axis, order in enumerate(self.orders)]

    def query(self, lower, upper):
        lower, upper = np.asarray(lower), np.asarray(upper)
        if self.orders is None or not np.isfinite(np.r_[lower, upper]).all():
            return np.flatnonzero(np.all((self.points >= lower) & (self.points <= upper), axis=1))
        starts = [np.searchsorted(column, lower[axis], side='left')
                  for axis, column in enumerate(self.coordinates)]
        stops = [np.searchsorted(column, upper[axis], side='right')
                 for axis, column in enumerate(self.coordinates)]
        axis = int(np.argmin(np.asarray(stops)-starts))
        rows = self.orders[axis][starts[axis]:stops[axis]]
        xyz = self.points[rows]
        rows = rows[np.all((xyz >= lower) & (xyz <= upper), axis=1)]
        # Geometry and tie-breaking still see the original source row order.
        return np.sort(rows)
