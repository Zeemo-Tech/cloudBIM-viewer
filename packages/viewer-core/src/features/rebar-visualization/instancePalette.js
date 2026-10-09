// Shared by the app and the unbundled point-cloud workbench. Work only on
// instance centerlines, never on individual points or the currently visible LOD.
const palette = ['#ff573d', '#24d9e8', '#f5d328', '#8b6cff', '#46df62', '#fa65bc',
  '#ff9c35', '#408cff', '#b7df38', '#c34de8', '#28bfa0', '#f4a7a0'].map(hex => {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255];
});

function oklab(rgb) {
  const [r, g, b] = rgb.map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
  const l = Math.cbrt(.4122214708*r + .5363325363*g + .0514459929*b);
  const m = Math.cbrt(.2119034982*r + .6806995451*g + .1073969566*b);
  const s = Math.cbrt(.0883024619*r + .2817188376*g + .6299787005*b);
  return [.2104542553*l + .793617785*m - .0040720468*s,
    1.9779984951*l - 2.428592205*m + .4505937099*s,
    .0259040371*l + .7827717662*m - .808675766*s];
}

export function instanceColorDistance(a, b) {
  const left = oklab(a), right = oklab(b);
  return Math.hypot(...left.map((value, i) => value - right[i]));
}

const contrast = palette.map(a => palette.map(b => instanceColorDistance(a, b)));
const subtract = (a, b) => a.map((v, i) => v - b[i]);
const dot = (a, b) => a.reduce((sum, v, i) => sum + v * b[i], 0);
const clamp = v => Math.max(0, Math.min(1, v));

// Closest distance between finite segments, including parallel/degenerate ones.
// Centroid distance would miss staggered long bars that overlap at their ends.
function segmentDistance([p, q], [r, s]) {
  const u = subtract(q, p), v = subtract(s, r), w = subtract(p, r);
  const a = dot(u, u), b = dot(u, v), c = dot(v, v), d = dot(u, w), e = dot(v, w);
  let t = 0, h = 0;
  if (a <= 1e-20) h = c > 1e-20 ? clamp(e / c) : 0;
  else if (c <= 1e-20) t = clamp(-d / a);
  else {
    const denominator = a*c - b*b;
    t = denominator > 1e-12*a*c ? clamp((b*e - c*d) / denominator) : 0;
    h = (b*t + e) / c;
    if (h < 0) { h = 0; t = clamp(-d / a); }
    else if (h > 1) { h = 1; t = clamp((b - d) / a); }
  }
  return Math.hypot(...w.map((value, i) => value + t*u[i] - h*v[i]));
}

function barExtent(segments) {
  // Use the longest observed straight segment for direction, but the full
  // instance for span. Tile/occlusion fragments must not shorten a long bar.
  let axis = [0, 0, 0], squaredLength = 0;
  for (const [a, b] of segments) {
    const direction = subtract(b, a), length = dot(direction, direction);
    if (length > squaredLength) { axis = direction; squaredLength = length; }
  }
  const length = Math.sqrt(squaredLength);
  return { axis: axis.map(value => length ? value / length : 0), points: segments.flat() };
}

function overlappingParallelSpan(a, b) {
  if (Math.abs(dot(a.axis, b.axis)) < .94) return 0;
  const origin = a.points[0];
  const range = points => {
    const positions = points.map(point => dot(subtract(point, origin), a.axis));
    return [Math.min(...positions), Math.max(...positions)];
  };
  const [a0, a1] = range(a.points), [b0, b1] = range(b.points);
  const span = Math.min(a1 - a0, b1 - b0);
  return Math.min(a1, b1) - Math.max(a0, b0) >= .25 * span ? span : 0;
}

/** Deterministic spatial coloring; distant instances may reuse a color.
 * @param {Array<{id: number, paths: number[][][]}>} instances
 * @returns {Map<number, [number, number, number]>}
 */
export function buildInstancePalette(instances) {
  const byId = new Map();
  for (const instance of instances) {
    if (!Number.isSafeInteger(instance.id) || instance.id <= 0 || instance.id >= 0xffffffff) continue;
    const segments = byId.get(instance.id) || [];
    for (const path of instance.paths) {
      // Do not bridge an invalid point or a gap between observed paths.
      for (let i = 1; i < path.length; i++) {
        if ([path[i-1], path[i]].every(p => p?.length === 3 && p.every(Number.isFinite))) {
          segments.push([path[i-1], path[i]]);
        }
      }
    }
    if (segments.length) byId.set(instance.id, segments);
  }
  const bars = [...byId].sort((a, b) => a[0] - b[0]);
  const nearest = bars.map(() => []);
  const parallel = bars.map(() => []);
  const extents = bars.map(([, segments]) => barExtent(segments));
  const graph = bars.map(() => new Map());
  const remember = (lists, i, j, distance) => {
    if (!Number.isFinite(distance)) return;
    const list = lists[i];
    list.push({ index: j, distance });
    list.sort((a, b) => a.distance - b.distance || bars[a.index][0] - bars[b.index][0]);
    if (list.length > 4) list.pop();
  };
  for (let i = 0; i < bars.length; i++) {
    for (let j = i + 1; j < bars.length; j++) {
      let distance = Infinity;
      let parallelDistance = Infinity;
      for (const a of bars[i][1]) for (const b of bars[j][1]) {
        const gap = segmentDistance(a, b);
        distance = Math.min(distance, gap);
        const u = subtract(a[1], a[0]), v = subtract(b[1], b[0]);
        const lengthProduct = dot(u, u) * dot(v, v);
        if (lengthProduct > 1e-20 && dot(u, v) ** 2 > .94 ** 2 * lengthProduct) parallelDistance = Math.min(parallelDistance, gap);
      }
      remember(nearest, i, j, distance);
      remember(nearest, j, i, distance);
      remember(parallel, i, j, parallelDistance);
      remember(parallel, j, i, parallelDistance);
      // A fixed nearest-neighbor limit misses parallel chords underneath web
      // bars: other rods can fill every slot, even when the chords run alongside
      // each other for metres. Keep ALL overlapping parallel bars within a
      // length-relative neighborhood, including pairs separated into layers.
      const span = overlappingParallelSpan(extents[i], extents[j]);
      if (span > 0 && parallelDistance <= .35 * span) {
        // These broader edges stay weaker than immediate neighbors, so a dense
        // rack with more bars than colors still reserves contrast for the
        // closest ones. The full span keeps layered long chords connected.
        const weight = .04 / (1 + parallelDistance / (.05 * span)) ** 2;
        graph[i].set(j, weight);
        graph[j].set(i, weight);
      }
    }
  }
  // Keep parallel neighbors even when many crossing bars are closer in 3D.
  [nearest, parallel].forEach(lists => lists.forEach((neighbors, i) => {
    // Relative spacing keeps the assignment independent of scene units/scale.
    const scale = Math.max(neighbors[Math.min(1, neighbors.length - 1)]?.distance ?? 0, 1e-9);
    for (const { index: j, distance } of neighbors) {
      // A touching neighbor must not make all other adjacency weights vanish.
      const weight = (lists === parallel ? 2 : 1) * Math.max(.02, 1 / (1 + distance / scale) ** 2);
      const symmetricWeight = Math.max(graph[i].get(j) || 0, weight);
      graph[i].set(j, symmetricWeight);
      graph[j].set(i, symmetricWeight);
    }
  }));
  const order = bars.map((_, i) => i).sort((a, b) => graph[b].size - graph[a].size || bars[a][0] - bars[b][0]);
  const assigned = new Map();
  // Greedy assignment plus two bounded relaxation passes. Penalize similar
  // colors most strongly on the closest edges, including adjacent parallel bars.
  for (let pass = 0; pass < 3; pass++) {
    for (const i of order) {
      let best = assigned.get(i) ?? (bars[i][0] % palette.length), bestCost = Infinity;
      const start = best;
      for (let offset = 0; offset < palette.length; offset++) {
        const candidate = (start + offset) % palette.length;
        let cost = 0;
        for (const [j, weight] of graph[i]) {
          const other = assigned.get(j);
          if (other !== undefined) {
            const difference = contrast[candidate][other];
            // Treat near-identical hues as a conflict too. Merely avoiding an
            // exact RGB match still permits two greens/blue-violets side by side.
            cost += weight * ((difference < .18 ? 1000 : 0) + 1 / (.0001 + difference ** 2));
          }
        }
        if (cost < bestCost - 1e-9) { bestCost = cost; best = candidate; }
      }
      assigned.set(i, best);
    }
  }
  return new Map(bars.map(([id], i) => [id, [...palette[assigned.get(i)]]]));
}
