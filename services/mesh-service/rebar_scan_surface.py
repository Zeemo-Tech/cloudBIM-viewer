"""Observed rebar tube evidence and same-side surface correspondence.

The tube supplies macro outward normals, not a reconstructed/observed back face.
Only real scan samples within supported sections can become correspondences.
"""
from __future__ import annotations

import math
import numpy as np
from scipy.spatial import cKDTree


class ObservedRebarSurface:
    def __init__(self, segments, unit_points, profile, *, unit_point_indices=None, independent_axes=False):
        self.explicit_point_indices = unit_point_indices is not None
        self.units = []
        self.diagnostics = {"method": "observed-section-radial-normals-v1",
                            "supportedPointCount": 0, "rejectedPointCount": 0,
                            "supportedSectionCount": sum(r['observedCenterM'] is not None for r in profile),
                            "sectionCount": len(profile)}
        point_base = 0
        for unit_id, start, end in segments:
            length = float(np.linalg.norm(end - start))
            if length <= 1e-9:
                continue
            tangent = (end - start) / length
            points = unit_points.get(unit_id, np.empty((0, 3)))
            rows = [r for r in profile if r['designUnitId'] == unit_id]
            stations = (points - start) @ tangent
            fitted_rows = [r for r in rows if r.get('axisStartM') is not None]
            radial_tangent = tangent
            if independent_axes and fitted_rows:
                frame = fitted_rows[0]
                radial_tangent = np.asarray(frame['axisTangent'])
                scan_stations = (points - np.asarray(frame['axisStartM'])) @ radial_tangent
                scan_rows = sorted(({**r, 'stationM': r['axisStationM']} for r in fitted_rows), key=lambda r: r['stationM'])
                centers, radii, supported = self._evaluate(scan_rows, scan_stations, radial_tangent)
            else:
                centers, radii, supported = self._evaluate(rows, stations, tangent)
            radial = points - centers
            # Production sections use the design transverse plane; the independent
            # workbench uses the scan-derived frame. These macro normals ignore
            # rib slopes and never create an unobserved back face.
            radial -= (radial @ radial_tangent)[:, None] * radial_tangent
            radial_length = np.linalg.norm(radial, axis=1)
            supported &= radial_length > 1e-9
            supported &= np.abs(radial_length - radii) <= np.maximum(.001, .25 * radii)
            normals = radial[supported] / radial_length[supported, None]
            original_indices = (np.asarray(unit_point_indices.get(unit_id), dtype=np.int64)
                                if unit_point_indices is not None and unit_id in unit_point_indices
                                else point_base + np.arange(len(points), dtype=np.int64))
            if len(original_indices) != len(points):
                raise ValueError("unit point indices must align with unit points")
            self.units.append({"id": unit_id, "start": start, "tangent": tangent, "length": length,
                               "rows": rows, "points": points[supported], "normals": normals,
                               "stations": stations[supported], "pointIndices": original_indices[supported],
                               "tree": None})
            self.diagnostics['supportedPointCount'] += int(supported.sum())
            self.diagnostics['rejectedPointCount'] += int((~supported).sum())
            point_base += len(points)

    @staticmethod
    def _evaluate(rows, stations, tangent):
        """Interpolate adjacent supported sections, never across a missing row.

        An isolated fitted section supports only its actual sampling window.
        No extrapolated end or hidden angular surface is claimed as observation.
        """
        centers = np.full((len(stations), 3), np.nan)
        radii = np.full(len(stations), np.nan)
        supported = np.zeros(len(stations), bool)
        for row in rows:
            if row['observedCenterM'] is None:
                continue
            delta = stations - row['stationM']
            take = np.abs(delta) <= row['windowM'] + 1e-9
            centers[take] = np.asarray(row['observedCenterM']) + delta[take, None] * tangent
            radii[take] = row['radiusM']
            supported[take] = True
        for left, right in zip(rows, rows[1:]):
            if left['observedCenterM'] is None or right['observedCenterM'] is None:
                continue
            span = right['stationM'] - left['stationM']
            if span <= 0:
                continue
            take = (stations >= left['stationM']) & (stations <= right['stationM'])
            f = (stations[take] - left['stationM']) / span
            centers[take] = ((1 - f[:, None]) * np.asarray(left['observedCenterM'])
                             + f[:, None] * np.asarray(right['observedCenterM']))
            radii[take] = (1 - f) * left['radiusM'] + f * right['radiusM']
            supported[take] = True
        return centers, radii, supported

    def match(self, vertices, normals, *, k=32, max_angle_deg=30., max_search_distance=.2,
              half_space_only=False, trace=False):
        """Return signed NORMAL separation, observed index and supported mask.

        Search in station/outward-direction coordinates, not world-space nearest
        neighbours. k is an initial batch size; exhausted batches expand. The
        original 3-D distance remains a hard cap. No design-solid crossing veto:
        a valid same-side displacement may exceed the design diameter.
        """
        if not math.isfinite(max_search_distance) or not .0001 <= max_search_distance <= .2:
            raise ValueError('max_search_distance must be between 0.0001 and 0.2 m')
        if not math.isfinite(max_angle_deg) or not 0 < max_angle_deg <= 90:
            raise ValueError('max_angle_deg must be between 0 and 90 degrees')
        values = np.full(len(vertices), np.nan)
        selected = np.full(len(vertices), -1, np.int64)
        reasons = np.full(len(vertices), "no-valid-candidate", dtype=object) if trace else None
        owner = np.full(len(vertices), -1, int)
        best = np.full(len(vertices), np.inf)
        # Unit association comes from design topology, not a shifted scan limb.
        for index, unit in enumerate(self.units):
            along = np.clip((vertices - unit['start']) @ unit['tangent'], 0, unit['length'])
            delta = vertices - unit['start'] - along[:, None] * unit['tangent']
            squared = np.einsum('ij,ij->i', delta, delta)
            take = squared < best
            owner[take], best[take] = index, squared[take]
        cosine = math.cos(math.radians(max_angle_deg))
        chord = max(1e-8, math.sqrt(2 - 2 * cosine))
        if trace:
            self.last_trace = {"unitIds": [self.units[i]['id'] if i >= 0 else None for i in owner],
                               "transverseNormals": np.zeros_like(vertices),
                               "candidateCount": np.zeros(len(vertices), dtype=int),
                               "orientationPassCount": np.zeros(len(vertices), dtype=int),
                               "axialPassCount": np.zeros(len(vertices), dtype=int),
                               "distancePassCount": np.zeros(len(vertices), dtype=int)}
        point_offset = 0
        for index, unit in enumerate(self.units):
            points = unit['points']
            if not len(points):
                if trace:
                    reasons[owner == index] = "no-supported-observed-points"
                continue
            tangent = unit['tangent']
            ids = np.flatnonzero(owner == index)
            stations = (vertices[ids] - unit['start']) @ tangent
            _, _, supported = self._evaluate(unit['rows'], stations, tangent)
            n = normals[ids] - (normals[ids] @ tangent)[:, None] * tangent
            norm = np.linalg.norm(n, axis=1)
            valid_normal = norm > 1e-8
            side_normal = np.abs(normals[ids] @ tangent) <= .7
            valid_station = (stations >= -1e-8) & (stations <= unit['length'] + 1e-8)
            if trace:
                reasons[ids[~supported]] = "unsupported-section"
                reasons[ids[~valid_station]] = "outside-unit"
                reasons[ids[~side_normal]] = "end-cap"
                reasons[ids[~valid_normal]] = "invalid-normal"
            supported &= valid_normal & side_normal & valid_station
            n /= np.maximum(norm[:, None], 1e-12)
            if trace: self.last_trace["transverseNormals"][ids] = n
            # A local axial window is independent of transverse displacement.
            window = max((r['windowM'] for r in unit['rows']), default=.001)
            tree_key = (window, chord)
            if unit.get('treeKey') != tree_key:
                coords = np.column_stack((unit['stations'] / window, unit['normals'] / chord))
                unit['tree'] = cKDTree(coords)
                unit['treeKey'] = tree_key
            query = np.column_stack((stations / window, n / chord))
            pending = np.flatnonzero(supported)
            batch = min(max(1, int(k)), 64, len(points))
            while len(pending):
                retry = []
                chunk = max(1, 262144 // batch)
                for offset in range(0, len(pending), chunk):
                    local = pending[offset:offset + chunk]
                    dist, ix = unit['tree'].query(query[local], k=batch, distance_upper_bound=math.sqrt(2) + 1e-9)
                    dist, ix = dist.reshape(len(local), batch), ix.reshape(len(local), batch)
                    valid = np.isfinite(dist)
                    safe_ix = np.minimum(ix, len(points) - 1)
                    delta = points[safe_ix] - vertices[ids[local], None, :]
                    normal_dot = np.einsum('ijk,ik->ij', unit['normals'][safe_ix], n[local])
                    axial = np.abs(unit['stations'][safe_ix] - stations[local, None])
                    signed = np.einsum('ijk,ik->ij', delta, n[local])
                    if trace:
                        trace_ids = ids[local]
                        # Counts describe this final evaluated candidate batch, not cumulative retries.
                        self.last_trace["candidateCount"][trace_ids] = valid.sum(axis=1)
                        self.last_trace["orientationPassCount"][trace_ids] = (valid & (normal_dot + 1e-12 >= cosine)).sum(axis=1)
                        self.last_trace["axialPassCount"][trace_ids] = (valid & (axial <= window + 1e-9)).sum(axis=1)
                        self.last_trace["distancePassCount"][trace_ids] = (valid & (np.linalg.norm(delta, axis=2) <= max_search_distance + 1e-12)).sum(axis=1)
                    valid &= normal_dot + 1e-12 >= cosine
                    valid &= axial <= window + 1e-9
                    valid &= np.linalg.norm(delta, axis=2) <= max_search_distance + 1e-12
                    if half_space_only:
                        valid &= signed >= 0
                    first = np.argmax(valid, axis=1)
                    exists = valid[np.arange(len(local)), first]
                    good = np.flatnonzero(exists)
                    values[ids[local[good]]] = signed[good, first[good]]
                    selected[ids[local[good]]] = (unit['pointIndices'][safe_ix[good, first[good]]] if self.explicit_point_indices else point_offset + safe_ix[good, first[good]])
                    if trace:
                        reasons[ids[local[good]]] = "matched"
                    more = ~exists & np.isfinite(dist[:, -1])
                    if batch < len(points):
                        retry.extend(local[more])
                pending = np.asarray(retry, dtype=int)
                batch = min(batch * 2, len(points))
            point_offset += len(points)
        if trace:
            reasons[owner < 0] = "no-topology-unit"
            return values, selected, np.isfinite(values), reasons.tolist()
        return values, selected, np.isfinite(values)

    def display_normals(self, point_count):
        """Observed normals in caller point-index space, using the matcher gate."""
        normals = np.zeros((point_count, 3), dtype=float)
        supported = np.zeros(point_count, dtype=bool)
        for unit in self.units:
            indices = unit['pointIndices']
            valid = (indices >= 0) & (indices < point_count)
            normals[indices[valid]] = unit['normals'][valid]
            supported[indices[valid]] = True
        return normals, supported
