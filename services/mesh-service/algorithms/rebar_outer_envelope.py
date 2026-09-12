"""A closed, continuous cloth around steel, shared by display and exclusion.

The two height surfaces bound the whole cage (including inter-layer voids).
Their local supports follow actual design centerlines. This is a 2.5-D outer
wrap, not a union of boxes or separate tubes around individual bars.
"""
import numpy as np
from scipy.spatial import Delaunay, ConvexHull
from scipy import sparse


def _truss_height_patches(inventory, nodes, origin, axes, lateral, registration):
    """Planar tension faces around a web assembly supported by its long chords.

    A repeating zigzag without a chord retains its actual profile. A truss with
    a continuous chord has a continuous supported face, not isolated bumps at
    the sampled zigzag vertices.
    """
    records = []
    for bar in inventory.get('bars', []):
        p = np.asarray(bar['points'], float).copy()
        p[:, :2] = (p[:, :2]-origin) @ axes.T
        records.append((p, float(bar['radiusM'])))
    groups = []
    for i, (points, radius) in enumerate(records):
        extent = np.ptp(points, axis=0)
        along = int(np.argmax(extent[:2])); across = 1-along
        if extent[2] < .02 or len(points) < 4 or extent[along] < .3: continue
        candidates = {i}
        for j, (cord, cord_radius) in enumerate(records):
            if np.ptp(cord[:, 2]) > 1.e-6 or not .8*extent[along] <= np.ptp(cord[:, along]) <= 1.1*extent[along]: continue
            if np.ptp(cord[:, across]) > 1.e-6: continue
            reach = 4*max(radius, cord_radius)+lateral
            if (points[:, across].min()-reach <= cord[0, across] <= points[:, across].max()+reach
                    and points[:, 2].min()-.012 <= cord[0, 2] <= points[:, 2].max()+.012
                    and min(cord[:, along].max(), points[:, along].max())-max(cord[:, along].min(), points[:, along].min()) >= .8*extent[along]):
                candidates.add(j)
        if len(candidates) > 1: groups.append(candidates)
    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            for j in range(i):
                if groups[i] & groups[j]:
                    groups[j] |= groups.pop(i); changed = True; break
            if changed: break
    patches = []
    signs = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
    for group in groups:
        support = []
        for i in group:
            points, radius = records[i]
            offsets = signs*np.array([radius+lateral, radius+lateral, radius+registration])
            support.append((points[:, None, :]+offsets).reshape(-1, 3))
        cloud = np.vstack(support)
        hull = ConvexHull(cloud)
        eq = hull.equations
        high = np.full(len(nodes), np.inf); low = np.full(len(nodes), -np.inf)
        valid = np.ones(len(nodes), bool)
        for nx, ny, nz, offset in eq:
            value = -(nodes[:, 0]*nx+nodes[:, 1]*ny+offset)
            if nz > 1.e-9: high = np.minimum(high, value/nz)
            elif nz < -1.e-9: low = np.maximum(low, value/nz)
            else: valid &= value >= -1.e-9
        valid &= high >= low-1.e-9
        patches.append((valid, high, low))
    return patches


def _design_surface_heights(inventory, nodes, origin, axes, lateral, registration, web_margin, external):
    """Flat shared sheets for coplanar bars; analytic profiles for inclined runs.

    Height never depends on the distance to a sampled point or the mesh budget.
    Parallel bars in a layer support one plane across their intervening gaps.
    Separate short-bar patches stay separate when their pitch has a large gap.
    """
    upper = np.full(len(nodes), -np.inf)
    lower = np.full(len(nodes), np.inf)
    nearest_distance = np.full(len(nodes), np.inf)
    nearest_upper = np.zeros(len(nodes)); nearest_lower = np.zeros(len(nodes))
    planes = {}
    for bar in inventory.get('bars', []):
        points = np.asarray(bar['points'], float)
        radius = float(bar['radiusM'])
        local = (points[:, :2]-origin) @ axes.T
        for index, (a, b) in enumerate(zip(points[:-1], points[1:])):
            delta = b-a; length = np.linalg.norm(delta)
            if length < 1.e-9: continue
            start, end = local[index:index+2]
            d = end-start; length_xy = np.linalg.norm(d)
            if length_xy < 1.e-9:
                distance = np.linalg.norm(nodes-start, axis=1)
                high = np.full(len(nodes), max(a[2], b[2])+radius+web_margin)
                low = np.full(len(nodes), min(a[2], b[2])-radius-web_margin)
            else:
                t = np.clip(((nodes-start) @ d)/(length_xy**2), 0., 1.)
                distance = np.linalg.norm(nodes-(start+t[:, None]*d), axis=1)
                z = a[2]+t*delta[2]
                margin = registration if abs(delta[2])/length < 1.e-6 else web_margin
                # Vertical section of the inclined physical cylinder. Clamp
                # its caps to the actual end surfaces instead of extrapolating.
                vertical = radius*length/length_xy+margin
                high = np.minimum(z+vertical, max(a[2], b[2])+radius+margin)
                low = np.maximum(z-vertical, min(a[2], b[2])-radius-margin)
            closer = distance < nearest_distance
            nearest_distance[closer] = distance[closer]
            nearest_upper[closer] = high[closer]; nearest_lower[closer] = low[closer]
            near = distance <= radius+lateral+1.e-9
            upper[near] = np.maximum(upper[near], high[near])
            lower[near] = np.minimum(lower[near], low[near])
            if abs(delta[2])/length < 1.e-6 and length_xy > 1.e-9:
                along = int(np.argmax(np.abs(d))); across = 1-along
                if abs(d[across])/length_xy > 1.e-5: continue
                key = (round(float(a[2]), 7), round(radius, 7), along)
                planes.setdefault(key, []).append((min(start[along], end[along])-external,
                    max(start[along], end[along])+external, float((start[across]+end[across])/2)))
    pitch_sources = [np.unique(np.round([item[2] for item in group], 8)) for group in planes.values()]
    reference = max(pitch_sources, key=len, default=np.array([]))
    reference_pitch = np.median(np.diff(reference)) if len(reference) > 1 else np.inf
    patch_count = 0
    for (height, radius, along), bars in planes.items():
        across = 1-along
        # Connect overlapping longitudinal runs at the usual transverse pitch.
        # This keeps isolated pairs of raised short ties from becoming one huge
        # raised rectangle spanning unrelated patches elsewhere in the cage.
        positions = np.unique(np.round([item[2] for item in bars], 8))
        pitches = np.diff(positions)
        pitch_limit = max(.04, 2.5*float(np.median(pitches[pitches > 1.e-7]))) if len(pitches) else .04
        pitch_limit = min(pitch_limit, max(.04, 2.5*reference_pitch))
        parent = list(range(len(bars)))
        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]; i = parent[i]
            return i
        for i, left in enumerate(bars):
            for j in range(i):
                right = bars[j]
                if abs(left[2]-right[2]) <= pitch_limit and min(left[1], right[1]) >= max(left[0], right[0])-1.e-9:
                    parent[root(i)] = root(j)
        groups = {}
        for i, item in enumerate(bars): groups.setdefault(root(i), []).append(item)
        for group in groups.values():
            # The rectangles connect only mutually overlapping runs. At every
            # longitudinal station use that station's actual transverse span.
            cuts = np.unique([edge for item in group for edge in item[:2]])
            for a, b in zip(cuts[:-1], cuts[1:]):
                active = [item for item in group if item[0] <= (a+b)/2 <= item[1]]
                if not active: continue
                lo = min(item[2] for item in active)-radius-lateral
                hi = max(item[2] for item in active)+radius+lateral
                selected = ((nodes[:, along] >= a-1.e-9) & (nodes[:, along] <= b+1.e-9)
                            & (nodes[:, across] >= lo-1.e-9) & (nodes[:, across] <= hi+1.e-9))
                upper[selected] = np.maximum(upper[selected], height+radius+registration)
                lower[selected] = np.minimum(lower[selected], height-radius-registration)
            patch_count += 1
    trusses = _truss_height_patches(inventory, nodes, origin, axes, lateral, web_margin)
    for selected, high, low in trusses:
        upper[selected] = np.maximum(upper[selected], high[selected])
        lower[selected] = np.minimum(lower[selected], low[selected])
    missing = ~np.isfinite(upper)
    upper[missing], lower[missing] = nearest_upper[missing], nearest_lower[missing]
    return upper, lower, patch_count


def _build_cloth_envelope(inventory, parameters):
    """Build one closed cloth with exact orthogonal footprint boundaries."""
    from .rebar_orthogonal_footprint import build_orthogonal_footprint
    lateral = float(parameters['in_plane_half_width_m'])
    registration = float(parameters['layer_registration_allowance_m'])
    web_margin = max(registration, float(parameters['web_registration_allowance_m']))
    external = float(parameters['external_length_tolerance_m'])
    footprint = build_orthogonal_footprint(inventory, lateral_clearance_m=lateral,
        external_length_tolerance_m=external,
        target_spacing_m=float(parameters['envelope_grid_spacing_m']), max_cells=60000)
    origin, axes = np.asarray(footprint['xyOriginM']), np.asarray(footprint['xyAxes'])
    xs, ys = np.asarray(footprint['gridXM']), np.asarray(footprint['gridYM'])
    active = np.asarray(footprint['activeCells'], bool)
    used = np.zeros((len(ys), len(xs)), bool)
    for dy, dx in ((0, 0), (0, 1), (1, 0), (1, 1)):
        used[dy:dy+active.shape[0], dx:dx+active.shape[1]] |= active
    grid_indices = np.full(used.shape, -1, dtype=int)
    grid_indices[used] = np.arange(np.count_nonzero(used))
    row, column = np.nonzero(used)
    nodes = np.c_[xs[column], ys[row]]
    row, column = np.nonzero(active)
    ll, lr = grid_indices[row, column], grid_indices[row, column+1]
    ul, ur = grid_indices[row+1, column], grid_indices[row+1, column+1]
    # The diagonal only triangulates each *allowed* top/bottom rectangle. No
    # triangle or sidewall is ever permitted to bridge an excluded corner.
    triangles = np.vstack((np.c_[ll, lr, ur], np.c_[ll, ur, ul]))
    step = max(float(np.max(np.diff(xs))), float(np.max(np.diff(ys))))
    upper, lower, patch_count = _design_surface_heights(inventory, nodes, origin, axes,
        lateral, registration, web_margin, external)
    support_radius = float(parameters['envelope_support_radius_m'])
    # One-sided relaxation rounds discontinuities only outward: smoothing must
    # never shave away a steel sample or its specified clearance.
    edges = np.vstack((triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]))
    graph = sparse.coo_matrix((np.ones(len(edges)*2),
        (np.r_[edges[:, 0], edges[:, 1]], np.r_[edges[:, 1], edges[:, 0]])), shape=(len(nodes), len(nodes))).tocsr()
    graph.data[:] = 1
    mass = np.asarray(graph.sum(axis=1)).ravel()
    relaxation_passes = int(parameters['envelope_relaxation_passes'])
    for _ in range(relaxation_passes):
        upper = np.maximum(upper, (graph @ upper)/mass)
        lower = np.minimum(lower, (graph @ lower)/mass)
    global_xy = nodes @ axes + origin
    top = np.c_[global_xy, upper]
    bottom = np.c_[global_xy, lower]
    count = len(nodes)
    # Delaunay triangles are CCW: upper faces point up, lower faces down.
    faces = [triangles, triangles[:, ::-1]+count]
    counts = {}
    for a, b in edges:
        key = tuple(sorted((int(a), int(b))))
        counts.setdefault(key, []).append((int(a), int(b)))
    walls = []
    for uses in counts.values():
        if len(uses) != 1:
            continue
        a, b = uses[0]
        walls.extend([[b, a, a+count], [b, a+count, b+count]])
    faces.append(np.asarray(walls, int))
    return {'kind': 'triangulated-cloth', 'closed': True,
        'verticesM': np.vstack((top, bottom)).tolist(), 'triangles': np.vstack(faces).tolist(),
        'upperVertexCount': count, 'xyLocalM': nodes.tolist(), 'xyOriginM': origin.tolist(),
        'xyAxes': axes.tolist(), 'trianglesXY': triangles.tolist(),
        'gridXM': xs.tolist(), 'gridYM': ys.tolist(),
        'activeCells': active.tolist(), 'gridNodeIndices': grid_indices.tolist(),
        'upperHeightsM': upper.tolist(), 'lowerHeightsM': lower.tolist(),
        'parameters': {'gridSpacingM': step, 'lateralAllowanceM': lateral,
            'registrationAllowanceM': registration, 'webAllowanceM': web_margin,
            'externalLengthAllowanceM': external,
            'supportRadiusM': support_radius, 'relaxationPasses': relaxation_passes,
            'footprintRule': 'orthogonal-row-column-wrap', 'lateralReference': 'steel-surface',
            'footprint': footprint.get('params', {}), 'heightRule': 'shared-planar-layers-analytic-webs',
            'planarPatchCount': patch_count},
        'meaning': 'cloth follows orthogonal steel silhouette; concave corners stay excluded and internal gaps stay enclosed',
        'surfaceRule': 'two exact triangles per active orthogonal cell; vertical boundary walls without diagonal corner bridges'}


def build_outer_envelope(inventory, parameters):
    """Planar body sheets and common inner/outer cloths across each bend row.

    A return bend is not a height field: its inside gap must never be filled
    merely to make the curved sheet share an XY roof/floor with the main cage.
    """
    from .rebar_curve_envelope import split_curve_bars
    from .rebar_bend_cloth import build_bend_cloths
    # Expand the displayed and queried shell together. Keep the caller's layer
    # metadata untouched and add the allowance once to each boundary family.
    parameters = dict(parameters)
    expansion = float(parameters.get('envelope_expansion_m', 0.))
    for name in ('in_plane_half_width_m', 'layer_registration_allowance_m',
                 'web_registration_allowance_m', 'external_length_tolerance_m',
                 'hook_surface_allowance_m'):
        parameters[name] += expansion
    body_inventory, curves = split_curve_bars(inventory)
    body = _build_cloth_envelope(body_inventory, parameters) if body_inventory['bars'] else None
    shells = build_bend_cloths(curves, float(parameters['hook_surface_allowance_m']),
                               float(parameters['in_plane_half_width_m']))
    vertices, triangles = [], []
    for part in ([body] if body else []) + shells:
        offset = len(vertices)
        vertices.extend(part['verticesM'])
        triangles.extend((np.asarray(part['triangles'], int)+offset).tolist())
    settings = dict(body['parameters']) if body else {
        'lateralAllowanceM': parameters['in_plane_half_width_m'],
        'registrationAllowanceM': parameters['layer_registration_allowance_m'],
        'webAllowanceM': parameters['web_registration_allowance_m']}
    settings.update(hookSurfaceAllowanceM=parameters['hook_surface_allowance_m'],
                    envelopeExpansionM=expansion,
                    hookProtection='shared-inner-outer-bend-cloth', curveShellCount=len(shells))
    return {'kind': 'composite-steel-envelope', 'closed': True,
            'body': body, 'curveShells': shells, 'verticesM': vertices, 'triangles': triangles,
            'xyAxes': body['xyAxes'] if body else [[1., 0.], [0., 1.]], 'parameters': settings,
            'meaning': 'planar body cloth union with shared inner/outer bend sheets; return-bend interior gaps remain outside',
            'surfaceRule': 'body triangle interpolation and membership against the exact displayed bend meshes'}


def _inside_orthogonal_cloth(points, envelope, chunk_size):
    """Evaluate precisely the two displayed triangles of each active cell."""
    xs, ys = np.asarray(envelope['gridXM']), np.asarray(envelope['gridYM'])
    active = np.asarray(envelope['activeCells'], bool)
    node_ids = np.asarray(envelope['gridNodeIndices'], int)
    upper, lower = np.asarray(envelope['upperHeightsM']), np.asarray(envelope['lowerHeightsM'])
    origin, axes = np.asarray(envelope['xyOriginM']), np.asarray(envelope['xyAxes'])
    result = np.zeros(len(points), bool)
    tolerance = 1.e-9

    def locate(values, edges):
        initial = np.searchsorted(edges, values, side='right')-1
        low = edges[np.clip(initial, 0, len(edges)-1)]
        high = edges[np.clip(initial+1, 0, len(edges)-1)]
        snapped = np.where(np.abs(values-low) <= tolerance, low,
                           np.where(np.abs(values-high) <= tolerance, high, values))
        index = np.clip(np.searchsorted(edges, snapped, side='right')-1, 0, len(edges)-2)
        return snapped, index, np.abs(snapped-edges[index]) <= tolerance

    for start in range(0, len(points), chunk_size):
        xyz = np.asarray(points[start:start+chunk_size])
        xy = (xyz[:, :2]-origin) @ axes.T
        x, ix, on_x = locate(xy[:, 0], xs)
        y, iy, on_y = locate(xy[:, 1], ys)
        within = (np.isfinite(xyz).all(axis=1) & (x >= xs[0]-tolerance) & (x <= xs[-1]+tolerance)
                  & (y >= ys[0]-tolerance) & (y <= ys[-1]+tolerance))
        found = np.zeros(len(xyz), bool)
        # Closed faces on cell boundaries may belong to the left/bottom cell
        # when the right/top one is excluded at an inward orthogonal corner.
        for dx, dy in ((0, 0), (-1, 0), (0, -1), (-1, -1)):
            cx, cy = ix+dx, iy+dy
            valid = within & ~found & (cx >= 0) & (cy >= 0)
            if dx: valid &= on_x
            if dy: valid &= on_y
            selected = np.flatnonzero(valid)
            if not len(selected): continue
            selected = selected[active[cy[selected], cx[selected]]]
            if not len(selected): continue
            col, row = cx[selected], cy[selected]
            u = np.clip((x[selected]-xs[col])/(xs[col+1]-xs[col]), 0., 1.)
            v = np.clip((y[selected]-ys[row])/(ys[row+1]-ys[row]), 0., 1.)
            ll = node_ids[row, col]; ur = node_ids[row+1, col+1]
            side = np.where(u >= v, node_ids[row, col+1], node_ids[row+1, col])
            w0, w1, w2 = 1-np.maximum(u, v), np.abs(u-v), np.minimum(u, v)
            hi = upper[ll]*w0+upper[side]*w1+upper[ur]*w2
            lo = lower[ll]*w0+lower[side]*w1+lower[ur]*w2
            found[selected] = (xyz[selected, 2] >= lo-tolerance) & (xyz[selected, 2] <= hi+tolerance)
        result[start:start+len(xyz)] = found
    return result


def inside_outer_envelope(points, envelope, chunk_size=65536):
    """Membership against the same triangles exported to the debug viewer."""
    if envelope.get('kind') == 'composite-steel-envelope':
        from .rebar_curve_envelope import inside_curve_shells
        body = envelope.get('body')
        result = inside_outer_envelope(points, body, chunk_size) if body else np.zeros(len(points), bool)
        ids = np.flatnonzero(~result)
        if len(ids):
            result[ids] = inside_curve_shells(np.asarray(points)[ids], envelope.get('curveShells', []), chunk_size)
        return result
    if 'gridXM' in envelope:
        return _inside_orthogonal_cloth(points, envelope, chunk_size)
    nodes = np.asarray(envelope['xyLocalM'], float)
    triangles = np.asarray(envelope['trianglesXY'], int)
    triangulation = Delaunay(nodes)
    if not np.array_equal(triangulation.simplices, triangles):
        raise ValueError('outer envelope triangulation does not match its persisted surface')
    upper = np.asarray(envelope['upperHeightsM'], float)
    lower = np.asarray(envelope['lowerHeightsM'], float)
    origin, axes = np.asarray(envelope['xyOriginM']), np.asarray(envelope['xyAxes'])
    result = np.zeros(len(points), bool)
    for start in range(0, len(points), chunk_size):
        xyz = np.asarray(points[start:start+chunk_size])
        xy = (xyz[:, :2]-origin) @ axes.T
        simplex = triangulation.find_simplex(xy)
        selected = np.flatnonzero((simplex >= 0) & np.isfinite(xyz).all(axis=1))
        if not len(selected):
            continue
        ids = simplex[selected]
        transform = triangulation.transform[ids]
        bary = np.einsum('nij,nj->ni', transform[:, :2], xy[selected]-transform[:, 2])
        weights = np.c_[bary, 1-bary.sum(axis=1)]
        corners = triangles[ids]
        hi = np.sum(upper[corners]*weights, axis=1)
        lo = np.sum(lower[corners]*weights, axis=1)
        result[start+selected] = (xyz[selected, 2] >= lo-1.e-9) & (xyz[selected, 2] <= hi+1.e-9)
    return result
