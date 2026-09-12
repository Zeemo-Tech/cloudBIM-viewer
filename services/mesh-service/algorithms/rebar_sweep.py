"""Recover circular sweep rings without moving or collapsing source vertices.

Only a complete, connected ring strip is reconstructed. Arbitrary triangulations,
fixtures and ambiguous topology are returned unchanged with an explicit reason.
All lengths are metres in the source-model frame.
"""
from __future__ import annotations

import math
import time
import numpy as np
import trimesh
from scipy.interpolate import CubicHermiteSpline
from .base import RemeshAlgorithm, RemeshResult, register

MAX_VERTICES = 2_000_000


class UnsupportedSweep(ValueError):
    pass


def check_vertex_budget(count):
    if count > MAX_VERTICES:
        raise ValueError('整个模型超过钢筋网格顶点预算，请增大轴向间距或减少截面边数')


def _circle(points):
    origin = points.mean(axis=0)
    _, _, axes = np.linalg.svd(points - origin, full_matrices=False)
    uv = (points - origin) @ axes[:2].T
    fit, _, rank, _ = np.linalg.lstsq(np.c_[2 * uv, np.ones(len(uv))], (uv * uv).sum(axis=1), rcond=None)
    radius2 = fit[2] + fit[:2] @ fit[:2]
    if rank < 3 or radius2 <= 0:
        raise UnsupportedSweep('degenerate cross section')
    center = origin + fit[:2] @ axes[:2]
    radius = float(np.sqrt(radius2))
    tolerance = max(2e-6, radius * .002)
    if (np.max(np.abs((points - center) @ axes[2])) > tolerance
            or np.max(np.abs(np.linalg.norm(points - center, axis=1) - radius)) > tolerance):
        raise UnsupportedSweep('cross section is not circular and planar')
    angles = np.sort(np.arctan2(uv[:, 1] - fit[1], uv[:, 0] - fit[0]))
    gaps = np.diff(np.r_[angles, angles[0] + 2 * np.pi])
    if np.max(np.abs(gaps - 2 * np.pi / len(points))) > .03:
        raise UnsupportedSweep('cross section is not a regular polygon')
    return center, radius, axes[2]


def recover_rings(mesh):
    """Walk topology from an end cap; never connect spatially nearby bars."""
    source = mesh.copy()
    # Only weld coincident export seams at micrometre precision; no mm cleanup.
    source.merge_vertices(digits_vertex=7)
    source.remove_unreferenced_vertices()
    if not source.is_watertight:
        raise UnsupportedSweep('requires a closed manifold sweep')
    caps = []
    for facet, boundary in zip(source.facets, source.facets_boundary):
        ids = np.unique(boundary)
        if len(ids) < 8 or len(ids) > 256:
            continue
        try:
            _circle(source.vertices[ids])
        except UnsupportedSweep:
            continue
        caps.append((ids, np.unique(source.faces[facet])))
    if len(caps) != 2 or len(caps[0][0]) != len(caps[1][0]):
        raise UnsupportedSweep('requires two unambiguous circular end caps')
    ring = set(map(int, caps[0][0]))
    end = set(map(int, caps[1][0]))
    visited = set(map(int, caps[0][1])) - ring
    end_interior = set(map(int, caps[1][1])) - end
    neighbors = source.vertex_neighbors
    centers, radii, normals, rings = [], [], [], []
    while ring:
        if len(ring) != len(caps[0][0]):
            raise UnsupportedSweep('incomplete or branching ring strip')
        points = source.vertices[sorted(ring)]
        center, radius, normal = _circle(points)
        centers.append(center); radii.append(radius); normals.append(normal); rings.append(points)
        visited.update(ring)
        if ring == end:
            break
        ring = {j for i in ring for j in neighbors[i]} - visited - end_interior
    if ring != end or len(visited | end_interior) != len(source.vertices):
        raise UnsupportedSweep('ring strip does not cover the complete solid')
    centers, normals = np.asarray(centers), np.asarray(normals)
    radius = float(np.median(radii))
    if np.max(np.abs(np.asarray(radii) - radius)) > max(2e-6, radius * .002):
        raise UnsupportedSweep('varying radius')
    delta = np.diff(centers, axis=0)
    lengths = np.linalg.norm(delta, axis=1)
    if np.any(lengths < 1e-7):
        raise UnsupportedSweep('coincident centerline rings')
    direction = delta / lengths[:, None]
    for i in range(len(normals)):
        forward = direction[min(i, len(direction) - 1)]
        if normals[i] @ forward < 0:
            normals[i] *= -1
        if normals[i] @ forward < .5:
            raise UnsupportedSweep('cross section normal disagrees with centerline')
    return centers, normals, radius, rings[0][0] - centers[0]


def resample_axis(centers, tangents, spacing, chord_error):
    """Piecewise tangent-constrained cubic arcs, uniformly sampled by arc length.

Circular-arc handle lengths reproduce bends without averaging away straight/bend
junctions. A dense integration table inverts arc length, not a PCA projection.
"""
    dense, derivatives = [], []
    effective_spacing = spacing
    for i, chord in enumerate(np.linalg.norm(np.diff(centers, axis=0), axis=1)):
        theta = math.acos(float(np.clip(tangents[i] @ tangents[i + 1], -1, 1)))
        if theta > math.pi / 2 + .01:
            raise UnsupportedSweep('bend sampling is too coarse')
        bisector = tangents[i] + tangents[i + 1]
        bisector /= np.linalg.norm(bisector)
        chord_axis = (centers[i + 1] - centers[i]) / chord
        # Circular sweeps have a chord parallel to the tangent bisector. A
        # sparse sharp corner violates this and Hermite would invent a bow.
        if bisector @ chord_axis < math.cos(math.radians(2)):
            raise UnsupportedSweep('section tangents do not support a circular arc or straight segment')
        scale = 1 / math.cos(theta / 4) ** 2
        spline = CubicHermiteSpline([0., 1.], centers[i:i + 2], tangents[i:i + 2] * chord * scale)
        count = max(8, int(math.ceil(theta / .01)))
        u = np.linspace(0., 1., count + 1)
        p, d = spline(u), spline(u, 1)
        curvature = np.linalg.norm(np.cross(d, spline(u, 2)), axis=1) / np.maximum(np.linalg.norm(d, axis=1), 1e-12) ** 3
        k = float(curvature.max())
        if k > 1e-8:
            # Safety margin covers the integration and chord approximation.
            effective_spacing = min(effective_spacing, math.sqrt(6 * chord_error / k))
        dense.append(p if i == 0 else p[1:])
        derivatives.append(d if i == 0 else d[1:])
    dense = np.concatenate(dense); derivatives = np.concatenate(derivatives)
    arc = np.r_[0., np.cumsum(np.linalg.norm(np.diff(dense, axis=0), axis=1))]
    segments = max(1, int(math.ceil(arc[-1] / effective_spacing)))
    if segments > MAX_VERTICES // 8:
        raise ValueError('轴向间距过小，超过钢筋网格顶点预算')
    target = np.linspace(0., arc[-1], segments + 1)
    points = np.column_stack([np.interp(target, arc, dense[:, k]) for k in range(3)])
    normals = np.column_stack([np.interp(target, arc, derivatives[:, k]) for k in range(3)])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    return points, normals, float(arc[-1]), float(arc[-1] / segments)


def sweep_mesh(points, tangents, radius, sides, initial):
    """Rotation-minimizing frames prevent roll and flipped faces through 3D bends."""
    if len(points) * sides + 2 > MAX_VERTICES:
        raise ValueError('截面边数与轴向间距组合超过钢筋网格顶点预算')
    u = initial - tangents[0] * (initial @ tangents[0])
    u /= np.linalg.norm(u)
    frames = []
    for i, tangent in enumerate(tangents):
        if i:
            cross = np.cross(tangents[i - 1], tangent)
            cosine = float(tangents[i - 1] @ tangent)
            u = u + np.cross(cross, u) + np.cross(cross, np.cross(cross, u)) / max(1 + cosine, 1e-12)
            u -= tangent * (u @ tangent); u /= np.linalg.norm(u)
        frames.append((u.copy(), np.cross(tangent, u)))
    frames = np.asarray(frames)
    angles = np.arange(sides) * 2 * np.pi / sides
    rings = points[:, None, :] + radius * (np.cos(angles)[None, :, None] * frames[:, 0, None, :] + np.sin(angles)[None, :, None] * frames[:, 1, None, :])
    vertices = np.vstack([rings.reshape(-1, 3), points[0], points[-1]])
    a = (np.arange(len(points) - 1)[:, None] * sides + np.arange(sides)).ravel()
    b = (a // sides) * sides + (a + 1) % sides
    faces = np.vstack([np.c_[a, b, a + sides], np.c_[b, b + sides, a + sides]])
    j = np.arange(sides); last = (len(points) - 1) * sides
    faces = np.vstack([faces, np.c_[np.full(sides, len(vertices) - 2), (j + 1) % sides, j],
                       np.c_[np.full(sides, len(vertices) - 1), last + j, last + (j + 1) % sides]])
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def remesh_rebar(mesh, params):
    started = time.perf_counter()
    sides = params.get('crossSectionSides', 16)
    spacing = params.get('axialSpacing', .01)
    error = params.get('maxChordError', .0001)
    if isinstance(sides, bool) or not isinstance(sides, int) or not 8 <= sides <= 128:
        raise ValueError('crossSectionSides must be an integer from 8 to 128')
    for key, value in [('axialSpacing', spacing), ('maxChordError', error)]:
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{key} must be finite and positive')
    try:
        centers, tangents, radius, initial = recover_rings(mesh)
        points, directions, length, step = resample_axis(centers, tangents, spacing, error)
        result = sweep_mesh(points, directions, radius, sides, initial)
        diagnostic = dict(status='rebuilt', sourceRingCount=len(centers), ringCount=len(points),
                          crossSectionSides=sides, radiusM=radius, axisLengthM=length,
                          effectiveAxialSpacingM=step, requestedAxialSpacingM=spacing,
                          maxChordErrorM=error, polygonSagittaM=radius * (1 - math.cos(math.pi / sides)))
    except UnsupportedSweep as exc:
        result = mesh.copy()
        diagnostic = dict(status='preserved', reason=str(exc))
    diagnostic['elapsedSeconds'] = time.perf_counter() - started
    return result, diagnostic


def remesh_solid_parts(mesh, params):
    """Process disconnected solids separately, including flat legacy PLY input."""
    source = mesh.copy()
    source.merge_vertices(digits_vertex=7)
    if np.any(np.bincount(source.edges_unique_inverse) > 2):
        check_vertex_budget(len(mesh.vertices))
        return mesh.copy(), [{'status': 'preserved', 'reason': 'coincident or non-manifold solids; original topology retained'}]
    solids = source.split(only_watertight=False)
    if len(solids) <= 1:
        output, report = remesh_rebar(mesh, params)
        check_vertex_budget(len(output.vertices))
        return output, [report]
    outputs, reports = [], []
    vertex_count = 0
    for solid in solids:
        output, report = remesh_rebar(solid, params)
        vertex_count += len(output.vertices)
        check_vertex_budget(vertex_count)
        outputs.append(output); reports.append(report)
    return trimesh.util.concatenate(outputs), reports


@register
class RebarSweepRemesh(RemeshAlgorithm):
    name = 'rebar_sweep'
    label = '钢筋保形均匀化（多边形截面 / 沿轴等距）'
    implementation_version = '1.0.0'
    contract_version = '1'

    def describe_params(self):
        return [
            dict(key='cross_section_sides', label='截面边数', type='int', default=16, min=8, max=128,
                 tooltip='圆截面用正多边形表示；8、16、32 或更多边，边数越多截面误差越小。'),
            dict(key='axial_spacing', label='轴向间距 (m)', type='float', default=.01, min=.0001, max=1.,
                 tooltip='沿中心线弧长等距划分的最大间距。弯曲较急时按弦高误差统一缩小间距。'),
            dict(key='max_chord_error', label='弦高误差 (m)', type='float', default=.0001, min=.000001, max=.01,
                 tooltip='限制弯曲轴线的折线近似误差；不包含截面多边形自身的内接误差。'),
        ]

    def run(self, input_path, output_path, params):
        unknown = set(params) - {row['key'] for row in self.describe_params()}
        if unknown:
            raise ValueError('不支持的钢筋均匀化参数: ' + ', '.join(sorted(unknown)))
        scene = trimesh.load(input_path, force='scene', process=False)
        before_v = before_f = 0
        outputs, reports = [], []
        vertex_count = 0
        effective = dict(crossSectionSides=params.get('cross_section_sides', 16),
                         axialSpacing=params.get('axial_spacing', .01),
                         maxChordError=params.get('max_chord_error', .0001))
        for node in scene.graph.nodes_geometry:
            transform, geometry = scene.graph[node]
            mesh = scene.geometry[geometry].copy(); mesh.apply_transform(transform)
            before_v += len(mesh.vertices); before_f += len(mesh.faces)
            output, diagnostics = remesh_solid_parts(mesh, effective)
            vertex_count += len(output.vertices)
            check_vertex_budget(vertex_count)
            outputs.append(output); reports.extend(diagnostics)
        if not outputs:
            raise ValueError('输入不包含三角网格')
        result = trimesh.util.concatenate(outputs)
        result.export(output_path)
        return RemeshResult(output_path, before_v, before_f, len(result.vertices), len(result.faces), {'solids': reports})
