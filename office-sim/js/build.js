// Geometry builders for the office model.
//
// Everything is assembled from primitives — no external assets — so the whole
// prototype is a handful of text files with no build step or asset pipeline.
// The look aimed for is a clean architectural model: crisp boxes, low walls the
// camera can see over, muted palette, greenery for warmth.

import * as THREE from 'three';
import { PALETTE } from './palette.js';
import { LAYOUT } from './layout.js';

// A mesh built at yaw 0 has its "front" pointing north (-Z). Given a facing
// direction, this returns the Y rotation that turns it to point that way.
export function yawToFace(dir) {
  return Math.atan2(-dir[0], -dir[1]);
}

// Shared materials, so 200 boxes don't mean 200 shader programs.
const mat = (color, opts = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness: 0.78, metalness: 0.02, ...opts });

const M = {
  ground: mat(PALETTE.ground, { roughness: 0.95 }),
  slab: mat(PALETTE.slabEdge, { roughness: 0.9 }),
  floor: mat(PALETTE.floor, { roughness: 0.85 }),
  wallSide: mat(PALETTE.wallSide),
  wallTop: mat(PALETTE.wallTop),
  deskTop: mat(PALETTE.deskTop, { roughness: 0.65 }),
  deskFrame: mat(PALETTE.deskFrame, { roughness: 0.55, metalness: 0.25 }),
  chair: mat(PALETTE.chair, { roughness: 0.7 }),
  chairSoft: mat(PALETTE.chairSoft, { roughness: 0.85 }),
  monitorBody: mat(PALETTE.monitorBody, { roughness: 0.5 }),
  monitorScreen: mat(PALETTE.monitorScreen, { roughness: 0.25, emissive: 0x1b2a31, emissiveIntensity: 0.4 }),
  boardFace: mat(PALETTE.boardFace, { roughness: 0.35 }),
  boardFrame: mat(PALETTE.boardFrame, { roughness: 0.5, metalness: 0.2 }),
  shelf: mat(PALETTE.shelf, { roughness: 0.7 }),
  cabinet: mat(PALETTE.cabinet, { roughness: 0.7 }),
  counter: mat(PALETTE.counter, { roughness: 0.7 }),
  counterTop: mat(PALETTE.counterTop, { roughness: 0.45 }),
  fridge: mat(PALETTE.fridge, { roughness: 0.4, metalness: 0.15 }),
  appliance: mat(PALETTE.appliance, { roughness: 0.5 }),
  tableTop: mat(PALETTE.tableTop, { roughness: 0.65 }),
  paper: mat(PALETTE.paper, { roughness: 0.9 }),
  plantPot: mat(PALETTE.plantPot, { roughness: 0.85 }),
  leafA: mat(PALETTE.leafA, { roughness: 0.9, flatShading: true }),
  leafB: mat(PALETTE.leafB, { roughness: 0.9, flatShading: true }),
  rug: mat(PALETTE.rug, { roughness: 0.96 }),
  sofa: mat(PALETTE.sofa, { roughness: 0.92 }),
  sofaCushion: mat(PALETTE.sofaCushion, { roughness: 0.95 }),
  rugTrim: mat(PALETTE.rugTrim, { roughness: 0.96 }),
  water: new THREE.MeshStandardMaterial({
    color: PALETTE.water,
    roughness: 0.15,
    transparent: true,
    opacity: 0.72,
  }),
};

function box(w, h, d, material, x = 0, y = 0, z = 0) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

function cyl(rTop, rBot, h, material, x = 0, y = 0, z = 0, seg = 18) {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(rTop, rBot, h, seg), material);
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

// ---------------------------------------------------------------- environment

function buildGround() {
  const g = new THREE.Mesh(new THREE.PlaneGeometry(220, 220), M.ground);
  g.rotation.x = -Math.PI / 2;
  g.position.y = -0.34;
  g.receiveShadow = true;
  return g;
}

function buildSlabAndFloor() {
  const group = new THREE.Group();
  const { minX, maxX, minZ, maxZ } = LAYOUT.bounds;
  const pad = LAYOUT.wallThickness / 2 + 0.28;
  const w = maxX - minX + pad * 2;
  const d = maxZ - minZ + pad * 2;

  // The slab the office sits on — its exposed edge gives the model depth.
  const slab = box(w, 0.32, d, M.slab, (minX + maxX) / 2, -0.16, (minZ + maxZ) / 2);
  group.add(slab);

  // Interior floor surface, a hair above the slab top so it reads as a finish.
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(maxX - minX, maxZ - minZ), M.floor);
  floor.rotation.x = -Math.PI / 2;
  floor.position.set((minX + maxX) / 2, 0.006, (minZ + maxZ) / 2);
  floor.receiveShadow = true;
  group.add(floor);

  return group;
}

function buildZones() {
  const group = new THREE.Group();
  for (const zone of LAYOUT.zones) {
    const [x1, z1, x2, z2] = zone.rect;
    const geo = new THREE.PlaneGeometry(Math.abs(x2 - x1), Math.abs(z2 - z1));
    const m = new THREE.Mesh(geo, mat(PALETTE.zone[zone.color], { roughness: 0.9 }));
    m.rotation.x = -Math.PI / 2;
    m.position.set((x1 + x2) / 2, 0.012, (z1 + z2) / 2);
    m.receiveShadow = true;
    group.add(m);
  }
  return group;
}

// Zone names painted flat on the floor, drawn to a canvas texture. Cheap, and
// it means the model labels itself without any DOM overlay to keep in sync.
function buildFloorLabel(text, x, z, size = 0.62) {
  const pad = 24;
  const fontPx = 64;
  const tracking = fontPx * 0.34;

  const measure = document.createElement('canvas').getContext('2d');
  measure.font = `600 ${fontPx}px "JetBrains Mono", ui-monospace, monospace`;
  const chars = [...text];
  const widths = chars.map((c) => measure.measureText(c).width);
  const textWidth = widths.reduce((a, b) => a + b, 0) + tracking * (chars.length - 1);

  const canvas = document.createElement('canvas');
  canvas.width = Math.ceil(textWidth + pad * 2);
  canvas.height = fontPx + pad * 2;
  const ctx = canvas.getContext('2d');
  ctx.font = measure.font;
  ctx.fillStyle = PALETTE.labelInk;
  ctx.textBaseline = 'middle';
  let cx = pad;
  chars.forEach((c, i) => {
    ctx.fillText(c, cx, canvas.height / 2);
    cx += widths[i] + tracking;
  });

  const tex = new THREE.CanvasTexture(canvas);
  tex.anisotropy = 8;
  tex.colorSpace = THREE.SRGBColorSpace;

  const h = size;
  const w = (canvas.width / canvas.height) * h;
  const m = new THREE.Mesh(
    new THREE.PlaneGeometry(w, h),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.72, depthWrite: false })
  );
  m.rotation.x = -Math.PI / 2;
  m.position.set(x, 0.02, z);
  return m;
}

function buildWalls() {
  const group = new THREE.Group();
  const h = LAYOUT.wallHeight;
  const t = LAYOUT.wallThickness;
  // BoxGeometry face order is [+X, -X, +Y, -Y, +Z, -Z]; only +Y is the top.
  const materials = [M.wallSide, M.wallSide, M.wallTop, M.wallSide, M.wallSide, M.wallSide];

  for (const seg of LAYOUT.walls) {
    const [x1, z1] = seg.a;
    const [x2, z2] = seg.b;
    const dx = x2 - x1;
    const dz = z2 - z1;
    const len = Math.hypot(dx, dz);
    const m = new THREE.Mesh(new THREE.BoxGeometry(len + t, h, t), materials);
    m.position.set((x1 + x2) / 2, h / 2, (z1 + z2) / 2);
    m.rotation.y = -Math.atan2(dz, dx);
    m.castShadow = true;
    m.receiveShadow = true;
    group.add(m);
  }
  return group;
}

// ----------------------------------------------------------------- furniture

function makeDesk() {
  const g = new THREE.Group();
  g.add(box(2.6, 0.08, 1.1, M.deskTop, 0, 0.74, 0));
  g.add(box(0.08, 0.7, 0.98, M.deskFrame, -1.2, 0.35, 0));
  g.add(box(0.08, 0.7, 0.98, M.deskFrame, 1.2, 0.35, 0));
  g.add(box(2.3, 0.06, 0.06, M.deskFrame, 0, 0.2, 0.3));
  return g;
}

function makeChair() {
  const g = new THREE.Group();
  // Office chair: sitter faces north at yaw 0, so the backrest sits to the south.
  g.add(box(0.52, 0.09, 0.5, M.chairSoft, 0, 0.46, 0));
  // Deliberately a low back. The camera looks from behind a seated figure, so
  // a full-height backrest hides the tier-coloured shirt, which is the one part
  // of an agent that carries meaning at a glance.
  g.add(box(0.5, 0.34, 0.09, M.chairSoft, 0, 0.65, 0.22));
  g.add(cyl(0.05, 0.05, 0.42, M.chair, 0, 0.23, 0));
  g.add(cyl(0.3, 0.32, 0.05, M.chair, 0, 0.04, 0, 20));
  return g;
}

function makeMonitor() {
  const g = new THREE.Group();
  // Screen faces south (+Z) at yaw 0 — toward whoever sits at the desk.
  g.add(box(0.34, 0.03, 0.2, M.monitorBody, 0, 0.8, 0));
  g.add(box(0.07, 0.22, 0.07, M.monitorBody, 0, 0.91, 0));
  const head = box(0.68, 0.42, 0.05, M.monitorBody, 0, 1.22, 0);
  head.rotation.x = -0.12;
  g.add(head);
  const screen = box(0.62, 0.36, 0.01, M.monitorScreen, 0, 1.22, 0.035);
  screen.rotation.x = -0.12;
  g.add(screen);
  return g;
}

function makeWhiteboard(width = 5.2) {
  const g = new THREE.Group();
  // Board face points north (-Z) at yaw 0; layout rotates it to face the room.
  g.add(box(width, 1.06, 0.08, M.boardFrame, 0, 0.95, 0));
  g.add(box(width - 0.18, 0.92, 0.03, M.boardFace, 0, 0.97, -0.055));
  g.add(box(width - 0.6, 0.05, 0.16, M.boardFrame, 0, 0.4, -0.08));
  // Markers in the tier colours, a small nod to who uses this board
  const tiers = [PALETTE.tier.cto, PALETTE.tier.product, PALETTE.tier.engineering];
  tiers.forEach((c, i) => {
    const pen = box(0.14, 0.04, 0.04, mat(c, { roughness: 0.5 }), -0.6 + i * 0.28, 0.44, -0.12);
    pen.rotation.y = 0.1 * i;
    g.add(pen);
  });
  return g;
}

function makeShelf() {
  const g = new THREE.Group();
  // Front faces north at yaw 0; deep along Z so it sits flush to a side wall.
  g.add(box(0.44, 1.5, 2.6, M.shelf, 0, 0.75, 0));
  const bookTones = [0x8d6e63, 0x6d7f8b, 0x9c7c5c, 0x77856b];
  for (let row = 0; row < 3; row++) {
    for (let i = 0; i < 4; i++) {
      const b = box(
        0.3,
        0.26,
        0.42,
        mat(bookTones[(row + i) % bookTones.length], { roughness: 0.85 }),
        0.02,
        0.42 + row * 0.44,
        -0.95 + i * 0.63
      );
      g.add(b);
    }
  }
  return g;
}

function makeCabinet() {
  const g = new THREE.Group();
  g.add(box(0.9, 1.0, 0.62, M.cabinet, 0, 0.5, 0));
  for (let i = 0; i < 2; i++) {
    g.add(box(0.5, 0.03, 0.02, M.deskFrame, 0, 0.35 + i * 0.36, -0.32));
  }
  return g;
}

function makeCounter(width = 6.6) {
  const g = new THREE.Group();
  g.add(box(width, 0.86, 0.86, M.counter, 0, 0.43, 0));
  g.add(box(width + 0.08, 0.07, 0.94, M.counterTop, 0, 0.9, 0));
  // Cupboard seams
  const n = Math.max(2, Math.round(width / 1.6));
  for (let i = 1; i < n; i++) {
    g.add(box(0.02, 0.7, 0.02, M.deskFrame, -width / 2 + (width / n) * i, 0.45, -0.44));
  }
  return g;
}

function makeCoffeeMachine() {
  const g = new THREE.Group();
  g.add(box(0.44, 0.52, 0.42, M.appliance, 0, 1.19, 0));
  g.add(box(0.3, 0.04, 0.3, M.counterTop, 0, 0.99, 0.02));
  g.add(box(0.36, 0.1, 0.03, mat(0x7a8288, { roughness: 0.4 }), 0, 1.36, -0.22));
  g.add(cyl(0.05, 0.045, 0.1, M.paper, 0, 1.03, 0.04, 12)); // a waiting cup
  return g;
}

function makeFridge() {
  const g = new THREE.Group();
  // Door faces north (-Z) at yaw 0.
  g.add(box(1.1, 1.92, 0.95, M.fridge, 0, 0.96, 0));
  g.add(box(1.06, 0.02, 0.02, mat(0xb4bbc0, { roughness: 0.4 }), 0, 1.24, -0.48)); // door seam
  g.add(box(0.05, 0.5, 0.05, M.appliance, 0.38, 1.5, -0.5)); // upper handle
  g.add(box(0.05, 0.36, 0.05, M.appliance, 0.38, 0.82, -0.5)); // lower handle
  return g;
}

function makeTable() {
  const g = new THREE.Group();
  g.add(cyl(0.92, 0.92, 0.07, M.tableTop, 0, 0.73, 0, 28));
  g.add(cyl(0.08, 0.08, 0.68, M.deskFrame, 0, 0.37, 0));
  g.add(cyl(0.42, 0.46, 0.05, M.deskFrame, 0, 0.04, 0, 24));
  return g;
}

function makePapers() {
  const g = new THREE.Group();
  for (let i = 0; i < 3; i++) {
    const p = box(0.3, 0.012, 0.42, M.paper, i * 0.012, 0.79 + i * 0.014, 0);
    p.rotation.y = (i - 1) * 0.08;
    g.add(p);
  }
  return g;
}

function makePlant() {
  const g = new THREE.Group();
  g.add(cyl(0.27, 0.2, 0.42, M.plantPot, 0, 0.21, 0, 16));
  const a = new THREE.Mesh(new THREE.IcosahedronGeometry(0.44, 0), M.leafA);
  a.position.set(0, 0.7, 0);
  a.castShadow = true;
  g.add(a);
  const b = new THREE.Mesh(new THREE.IcosahedronGeometry(0.3, 0), M.leafB);
  b.position.set(0.16, 1.02, -0.06);
  b.castShadow = true;
  g.add(b);
  const c = new THREE.Mesh(new THREE.IcosahedronGeometry(0.22, 0), M.leafA);
  c.position.set(-0.2, 0.96, 0.12);
  c.castShadow = true;
  g.add(c);
  return g;
}

// A rug, to mark the spot the team gathers on rather than leaving the meeting
// area as bare floor. Built as a thin slab so it catches an edge of shadow.
function makeRug(width = 7, depth = 4) {
  const g = new THREE.Group();
  const base = box(width, 0.035, depth, M.rugTrim, 0, 0.033, 0);
  base.castShadow = false;
  g.add(base);
  const inner = box(width - 0.44, 0.02, depth - 0.44, M.rug, 0, 0.049, 0);
  inner.castShadow = false;
  g.add(inner);
  return g;
}

// Breakout seating. Sitter faces north at yaw 0, matching the chairs.
function makeSofa() {
  const g = new THREE.Group();
  g.add(box(2.4, 0.34, 0.92, M.sofa, 0, 0.26, 0)); // base
  g.add(box(2.4, 0.5, 0.22, M.sofa, 0, 0.66, 0.35)); // backrest
  g.add(box(0.22, 0.28, 0.92, M.sofa, -1.09, 0.57, 0)); // arms
  g.add(box(0.22, 0.28, 0.92, M.sofa, 1.09, 0.57, 0));
  g.add(box(1.02, 0.14, 0.78, M.sofaCushion, -0.47, 0.5, -0.04));
  g.add(box(1.02, 0.14, 0.78, M.sofaCushion, 0.47, 0.5, -0.04));
  return g;
}

function makeLowTable() {
  const g = new THREE.Group();
  g.add(box(1.25, 0.07, 0.68, M.tableTop, 0, 0.41, 0));
  for (const [dx, dz] of [[-0.52, -0.25], [0.52, -0.25], [-0.52, 0.25], [0.52, 0.25]]) {
    g.add(box(0.06, 0.38, 0.06, M.deskFrame, dx, 0.19, dz));
  }
  g.add(box(0.26, 0.03, 0.34, M.paper, 0.2, 0.46, 0.02)); // a magazine, left out
  return g;
}

function makeWaterCooler() {
  const g = new THREE.Group();
  g.add(box(0.44, 0.96, 0.44, M.fridge, 0, 0.48, 0));
  g.add(cyl(0.2, 0.24, 0.52, M.water, 0, 1.22, 0, 16));
  g.add(cyl(0.12, 0.2, 0.12, M.fridge, 0, 1.0, 0, 16));
  g.add(box(0.12, 0.1, 0.1, M.appliance, 0, 0.72, -0.24));
  return g;
}

function makePrinter() {
  const g = new THREE.Group();
  g.add(box(0.7, 0.62, 0.6, M.cabinet, 0, 0.31, 0)); // stand
  g.add(box(0.78, 0.44, 0.66, M.appliance, 0, 0.84, 0)); // body
  g.add(box(0.6, 0.03, 0.4, M.paper, 0, 1.07, -0.12)); // output tray
  g.add(box(0.2, 0.06, 0.06, mat(0x6f7780, { roughness: 0.4 }), 0.24, 1.0, -0.34));
  return g;
}

const BUILDERS = {
  desk: makeDesk,
  rug: (item) => makeRug(item.width, item.depth),
  waterCooler: makeWaterCooler,
  printer: makePrinter,
  sofa: makeSofa,
  lowTable: makeLowTable,
  chair: makeChair,
  monitor: makeMonitor,
  whiteboard: (item) => makeWhiteboard(item.width),
  shelf: makeShelf,
  cabinet: makeCabinet,
  counter: (item) => makeCounter(item.width),
  coffeeMachine: makeCoffeeMachine,
  fridge: makeFridge,
  table: makeTable,
  papers: makePapers,
  plant: makePlant,
};

function buildFurniture() {
  const group = new THREE.Group();
  for (const item of LAYOUT.furniture) {
    const builder = BUILDERS[item.type];
    if (!builder) {
      console.warn(`No builder for furniture type "${item.type}"`);
      continue;
    }
    const mesh = builder(item);
    mesh.position.set(item.at[0], 0, item.at[1]);
    mesh.rotation.y = yawToFace(item.face ?? [0, -1]);
    if (item.scale) mesh.scale.setScalar(item.scale);
    group.add(mesh);
  }
  return group;
}

// Debug overlay: a ring at every named station, so it is obvious whether the
// anchors Layers 2-5 depend on actually land on chairs and doorways.
function buildStationMarkers() {
  const group = new THREE.Group();
  group.visible = false;
  group.name = 'stationMarkers';
  const ringMat = new THREE.MeshBasicMaterial({
    color: 0xe5533d,
    transparent: true,
    opacity: 0.9,
    side: THREE.DoubleSide,
    depthTest: false,
  });
  const lookMat = new THREE.MeshBasicMaterial({
    color: 0x2fb8a8,
    transparent: true,
    opacity: 0.85,
    depthTest: false,
  });

  // Floated above seat height: at floor level the chair bases hide exactly the
  // anchors most worth checking.
  const y = 0.62;

  for (const [, s] of Object.entries(LAYOUT.stations)) {
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.22, 0.3, 20), ringMat);
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(s.at[0], y, s.at[1]);
    group.add(ring);

    // Short spur pointing at whatever the station faces
    const dx = s.look[0] - s.at[0];
    const dz = s.look[1] - s.at[1];
    const len = Math.hypot(dx, dz) || 1;
    const spur = new THREE.Mesh(new THREE.PlaneGeometry(0.07, 0.5), lookMat);
    spur.rotation.x = -Math.PI / 2;
    spur.rotation.z = -Math.atan2(dz / len, dx / len) + Math.PI / 2;
    spur.position.set(s.at[0] + (dx / len) * 0.42, y, s.at[1] + (dz / len) * 0.42);
    group.add(spur);
  }
  return group;
}

// Assemble the whole static office and return it as one group.
export function buildOffice() {
  const office = new THREE.Group();
  office.name = 'office';
  office.add(buildGround());
  office.add(buildSlabAndFloor());
  office.add(buildZones());
  office.add(buildWalls());
  office.add(buildFurniture());

  const labels = new THREE.Group();
  for (const zone of LAYOUT.zones) {
    labels.add(buildFloorLabel(zone.label, zone.labelAt[0], zone.labelAt[1]));
  }
  office.add(labels);

  office.add(buildStationMarkers());
  return office;
}
