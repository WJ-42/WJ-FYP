// Navigation: turning the floor plan into something an agent can cross.
//
// Layer 3 needs a route from wherever an agent is standing to any named
// station, one that does not cut through a wall, a desk or a pot plant.
//
// The walkable area is derived rather than authored. Hand-placing a waypoint
// graph would mean a second set of coordinates to keep in step with layout.js,
// and it would rot silently the first time a desk moved. Instead a coarse grid
// is laid over the floor and cells are marked blocked from two sources: the
// wall segments declared in LAYOUT, and the bounding boxes of the furniture
// Layer 1 actually built. Doorways need no special case at all, because a door
// gap is just an absent wall segment, so its cells are never marked.
//
// Obstacles are inflated by the agent's own radius, which means a path that
// clears the grid clears it for a body with width, and the walk can follow the
// centre line without a separate collision pass.
//
// A* over that grid is correct but staircased, so the result is string-pulled
// back down to a handful of long straight runs before anyone walks it.

import * as THREE from 'three';
import { LAYOUT } from './layout.js';

const CELL = 0.25; // grid resolution in metres
const AGENT_RADIUS = 0.26; // half shoulder width plus clearance
const OBSTACLE_MIN_TOP = 0.28; // below this an item is flat enough to walk over
const OBSTACLE_MAX_BOTTOM = 1.0; // above this an item is overhead, not in the way
const CARVE = 0.52; // forced-clear radius at each end of a route

// Both ends of a route are carved clear before searching. An agent getting out
// of its chair starts inside the chair's own inflated footprint, and arriving at
// a desk means ending inside it, so without this every seated station would be
// unreachable from itself.

function distToSegment(px, pz, x1, z1, x2, z2) {
  const dx = x2 - x1;
  const dz = z2 - z1;
  const l2 = dx * dx + dz * dz;
  let t = l2 ? ((px - x1) * dx + (pz - z1) * dz) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), pz - (z1 + t * dz));
}

export function buildNav(office) {
  const { minX, maxX, minZ, maxZ } = LAYOUT.bounds;

  // Padded by a cell on each side so the perimeter walls themselves sit inside
  // the grid rather than on its edge.
  const originX = minX - CELL;
  const originZ = minZ - CELL;
  const nx = Math.ceil((maxX - minX + CELL * 2) / CELL);
  const nz = Math.ceil((maxZ - minZ + CELL * 2) / CELL);
  const blocked = new Uint8Array(nx * nz);

  const cx = (i) => originX + (i + 0.5) * CELL;
  const cz = (j) => originZ + (j + 0.5) * CELL;
  const toI = (x) => Math.floor((x - originX) / CELL);
  const toJ = (z) => Math.floor((z - originZ) / CELL);
  const inside = (i, j) => i >= 0 && j >= 0 && i < nx && j < nz;

  function markRect(x0, z0, x1, z1, fn) {
    const i0 = Math.max(0, toI(x0));
    const i1 = Math.min(nx - 1, toI(x1));
    const j0 = Math.max(0, toJ(z0));
    const j1 = Math.min(nz - 1, toJ(z1));
    for (let j = j0; j <= j1; j++) {
      for (let i = i0; i <= i1; i++) {
        if (fn(cx(i), cz(j))) blocked[j * nx + i] = 1;
      }
    }
  }

  // --- Walls ---------------------------------------------------------------
  // buildWalls() gives each segment a length of len + thickness, squaring off
  // the corners, so the collision segment is extended by half a thickness at
  // each end to match what is actually in the scene.
  const half = LAYOUT.wallThickness / 2;
  const reach = half + AGENT_RADIUS;

  for (const seg of LAYOUT.walls) {
    const [x1, z1] = seg.a;
    const [x2, z2] = seg.b;
    const len = Math.hypot(x2 - x1, z2 - z1) || 1;
    const ux = (x2 - x1) / len;
    const uz = (z2 - z1) / len;
    const ax = x1 - ux * half;
    const az = z1 - uz * half;
    const bx = x2 + ux * half;
    const bz = z2 + uz * half;

    markRect(
      Math.min(ax, bx) - reach,
      Math.min(az, bz) - reach,
      Math.max(ax, bx) + reach,
      Math.max(az, bz) + reach,
      (px, pz) => distToSegment(px, pz, ax, az, bx, bz) <= reach
    );
  }

  // --- Furniture -----------------------------------------------------------
  // Taken from the geometry rather than from a parallel table of sizes, so the
  // two cannot disagree. Anything shorter than OBSTACLE_MIN_TOP is a rug or a
  // floor decal and is walked straight over.
  office.updateMatrixWorld(true);
  const furniture = office.getObjectByName('furniture');
  const footprints = [];

  if (furniture) {
    for (const item of furniture.children) {
      const b = new THREE.Box3().setFromObject(item);
      if (!Number.isFinite(b.min.x)) continue;
      if (b.max.y < OBSTACLE_MIN_TOP) continue;
      if (b.min.y > OBSTACLE_MAX_BOTTOM) continue;
      footprints.push(b);
    }
  } else {
    console.warn('nav: no "furniture" group found, routing around walls only');
  }

  for (const b of footprints) {
    markRect(
      b.min.x - AGENT_RADIUS,
      b.min.z - AGENT_RADIUS,
      b.max.x + AGENT_RADIUS,
      b.max.z + AGENT_RADIUS,
      (px, pz) =>
        px >= b.min.x - AGENT_RADIUS &&
        px <= b.max.x + AGENT_RADIUS &&
        pz >= b.min.z - AGENT_RADIUS &&
        pz <= b.max.z + AGENT_RADIUS
    );
  }

  const nav = {
    cell: CELL,
    nx,
    nz,
    originX,
    originZ,
    blocked,
    footprints,
    inside,
    toI,
    toJ,
    cellX: cx,
    cellZ: cz,
    isBlocked(x, z) {
      const i = toI(x);
      const j = toJ(z);
      return !inside(i, j) || blocked[j * nx + i] === 1;
    },
    blockedCount: blocked.reduce((n, v) => n + v, 0),
  };

  return nav;
}

// Carving works on a copy, so one agent's route cannot quietly open a gap for
// everyone else's.
function carvedGrid(nav, points) {
  const grid = nav.blocked.slice();
  for (const [x, z] of points) {
    const i0 = Math.max(0, nav.toI(x - CARVE));
    const i1 = Math.min(nav.nx - 1, nav.toI(x + CARVE));
    const j0 = Math.max(0, nav.toJ(z - CARVE));
    const j1 = Math.min(nav.nz - 1, nav.toJ(z + CARVE));
    for (let j = j0; j <= j1; j++) {
      for (let i = i0; i <= i1; i++) {
        if (Math.hypot(nav.cellX(i) - x, nav.cellZ(j) - z) <= CARVE) {
          grid[j * nav.nx + i] = 0;
        }
      }
    }
  }
  return grid;
}

// A small binary heap. A linear scan for the cheapest open cell is fine at this
// grid size, but it is the one thing that would stop scaling if the office grew.
class Heap {
  constructor() {
    this.items = [];
  }
  get size() {
    return this.items.length;
  }
  push(node, cost) {
    const a = this.items;
    a.push({ node, cost });
    let i = a.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (a[p].cost <= a[i].cost) break;
      [a[p], a[i]] = [a[i], a[p]];
      i = p;
    }
  }
  pop() {
    const a = this.items;
    const top = a[0];
    const last = a.pop();
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = i * 2 + 1;
        const r = l + 1;
        let m = i;
        if (l < a.length && a[l].cost < a[m].cost) m = l;
        if (r < a.length && a[r].cost < a[m].cost) m = r;
        if (m === i) break;
        [a[m], a[i]] = [a[i], a[m]];
        i = m;
      }
    }
    return top.node;
  }
}

const ROOT2 = Math.SQRT2;

function nearestOpen(nav, grid, x, z, maxRings = 12) {
  const si = nav.toI(x);
  const sj = nav.toJ(z);
  if (nav.inside(si, sj) && grid[sj * nav.nx + si] === 0) return [si, sj];

  for (let r = 1; r <= maxRings; r++) {
    let best = null;
    let bestD = Infinity;
    for (let j = sj - r; j <= sj + r; j++) {
      for (let i = si - r; i <= si + r; i++) {
        if (Math.max(Math.abs(i - si), Math.abs(j - sj)) !== r) continue;
        if (!nav.inside(i, j) || grid[j * nav.nx + i] === 1) continue;
        const d = Math.hypot(nav.cellX(i) - x, nav.cellZ(j) - z);
        if (d < bestD) {
          bestD = d;
          best = [i, j];
        }
      }
    }
    if (best) return best;
  }
  return null;
}

function lineClear(nav, grid, ax, az, bx, bz) {
  const d = Math.hypot(bx - ax, bz - az);
  const steps = Math.max(1, Math.ceil(d / (CELL * 0.5)));
  for (let s = 0; s <= steps; s++) {
    const t = s / steps;
    const i = nav.toI(ax + (bx - ax) * t);
    const j = nav.toJ(az + (bz - az) * t);
    if (!nav.inside(i, j) || grid[j * nav.nx + i] === 1) return false;
  }
  return true;
}

// Pull the staircased grid route taut: from each kept point, keep only the
// furthest point still reachable in a straight line.
function stringPull(nav, grid, pts) {
  if (pts.length <= 2) return pts;
  const out = [pts[0]];
  let i = 0;
  while (i < pts.length - 1) {
    let j = pts.length - 1;
    for (; j > i + 1; j--) {
      if (lineClear(nav, grid, pts[i][0], pts[i][1], pts[j][0], pts[j][1])) break;
    }
    out.push(pts[j]);
    i = j;
  }
  return out;
}

/**
 * Finds a walkable route between two points on the floor.
 *
 * Returns an array of [x, z] waypoints beginning at `from` and ending exactly
 * at `to`, or null if the two are not connected. The exact endpoints are kept
 * even though they may sit inside a carved-out footprint, because an agent
 * really does stand on its own chair at each end.
 */
// Route searches are counted, because they are by far the most expensive thing
// the simulation does and a replay runs through all of them again.
export const pathStats = { calls: 0, ms: 0 };

export function findPath(nav, from, to) {
  pathStats.calls += 1;
  const started = performance.now();
  const result = findPathUncounted(nav, from, to);
  pathStats.ms += performance.now() - started;
  return result;
}

function findPathUncounted(nav, from, to) {
  const grid = carvedGrid(nav, [from, to]);

  const start = nearestOpen(nav, grid, from[0], from[1]);
  const goal = nearestOpen(nav, grid, to[0], to[1]);
  if (!start || !goal) return null;

  const { nx, nz } = nav;
  const n = nx * nz;
  const startIdx = start[1] * nx + start[0];
  const goalIdx = goal[1] * nx + goal[0];

  if (startIdx === goalIdx) return [from.slice(), to.slice()];

  const gScore = new Float32Array(n).fill(Infinity);
  const cameFrom = new Int32Array(n).fill(-1);
  const closed = new Uint8Array(n);
  const open = new Heap();

  const h = (i, j) => {
    const dx = Math.abs(i - goal[0]);
    const dz = Math.abs(j - goal[1]);
    return (Math.max(dx, dz) + (ROOT2 - 1) * Math.min(dx, dz)) * CELL;
  };

  gScore[startIdx] = 0;
  open.push(startIdx, h(start[0], start[1]));

  let found = false;
  while (open.size) {
    const cur = open.pop();
    if (closed[cur]) continue;
    closed[cur] = 1;
    if (cur === goalIdx) {
      found = true;
      break;
    }

    const ci = cur % nx;
    const cj = (cur - ci) / nx;

    for (let dj = -1; dj <= 1; dj++) {
      for (let di = -1; di <= 1; di++) {
        if (!di && !dj) continue;
        const ni = ci + di;
        const nj = cj + dj;
        if (ni < 0 || nj < 0 || ni >= nx || nj >= nz) continue;
        const nIdx = nj * nx + ni;
        if (grid[nIdx] === 1 || closed[nIdx]) continue;

        // No cutting the corner between two blocked cells, which would clip a
        // desk corner or squeeze through a wall join.
        if (di && dj) {
          if (grid[cj * nx + ni] === 1 || grid[nj * nx + ci] === 1) continue;
        }

        const step = di && dj ? ROOT2 * CELL : CELL;
        const tentative = gScore[cur] + step;
        if (tentative < gScore[nIdx]) {
          gScore[nIdx] = tentative;
          cameFrom[nIdx] = cur;
          open.push(nIdx, tentative + h(ni, nj));
        }
      }
    }
  }

  if (!found) return null;

  const cells = [];
  for (let at = goalIdx; at !== -1; at = cameFrom[at]) {
    const i = at % nx;
    const j = (at - i) / nx;
    cells.push([nav.cellX(i), nav.cellZ(j)]);
    if (at === startIdx) break;
  }
  cells.reverse();

  // Swap the grid-snapped ends for the real ones before pulling the line taut,
  // so the first and last legs aim at the actual chair rather than a cell centre.
  cells[0] = from.slice();
  cells[cells.length - 1] = to.slice();

  return stringPull(nav, grid, cells);
}

// --- Debug overlays --------------------------------------------------------

// Every blocked cell as a flat tile. This is the only practical way to confirm
// the derivation is right: a desk that failed to register shows up immediately
// as a hole, and over-inflated walls show up as doorways sealed shut.
export function buildNavOverlay(nav) {
  const group = new THREE.Group();
  group.name = 'navOverlay';
  group.visible = false;

  const count = nav.blockedCount;
  if (!count) return group;

  const geo = new THREE.PlaneGeometry(nav.cell * 0.92, nav.cell * 0.92);
  geo.rotateX(-Math.PI / 2);
  const mesh = new THREE.InstancedMesh(
    geo,
    new THREE.MeshBasicMaterial({
      color: 0xe5533d,
      transparent: true,
      opacity: 0.3,
      depthWrite: false,
    }),
    count
  );

  const m = new THREE.Matrix4();
  let k = 0;
  for (let j = 0; j < nav.nz; j++) {
    for (let i = 0; i < nav.nx; i++) {
      if (nav.blocked[j * nav.nx + i] !== 1) continue;
      m.makeTranslation(nav.cellX(i), 0.07, nav.cellZ(j));
      mesh.setMatrixAt(k++, m);
    }
  }
  mesh.count = k;
  mesh.instanceMatrix.needsUpdate = true;
  group.add(mesh);
  return group;
}

// One polyline per agent currently walking, in that agent's tier colour.
export function createPathOverlay() {
  const group = new THREE.Group();
  group.name = 'pathOverlay';
  group.visible = false;
  const lines = new Map();

  function show(agentId, path, color) {
    clear(agentId);
    if (!path || path.length < 2) return;
    const geo = new THREE.BufferGeometry().setFromPoints(
      path.map(([x, z]) => new THREE.Vector3(x, 0.09, z))
    );
    const line = new THREE.Line(
      geo,
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.95 })
    );
    group.add(line);
    lines.set(agentId, line);
  }

  function clear(agentId) {
    const line = lines.get(agentId);
    if (!line) return;
    group.remove(line);
    line.geometry.dispose();
    line.material.dispose();
    lines.delete(agentId);
  }

  return { group, show, clear };
}
