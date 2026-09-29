// The office floor plan, as data.
//
// Everything downstream reads this file: Layer 1 builds geometry from it,
// Layer 2 places agents at `stations`, Layer 3 walks them between `stations`
// via the nav graph, Layer 4/5 refer to stations by name ('board', 'coffee').
// Nothing else should hardcode a coordinate.
//
// Coordinates are metres. X runs east, Z runs south, Y is up.
// The office interior spans X [-14, 14], Z [-10, 10] — 28m x 20m. Kept
// deliberately tight: five agents in a warehouse read as a ghost town, and
// shorter walks mean the choreography in Layer 5 stays watchable.
// Facing is stored as a point to look at (`look`), not an angle, because
// "stand here facing the whiteboard" survives layout tweaks better than a yaw.

const W = 14; // half-width  (east/west interior face)
const D = 10; // half-depth  (north/south interior face)

export const LAYOUT = {
  bounds: { minX: -W, maxX: W, minZ: -D, maxZ: D },
  wallHeight: 1.35,
  wallThickness: 0.22,

  // Tinted floor patches, each with a label decal laid flat on the floor.
  zones: [
    {
      id: 'cto',
      label: 'CTO OFFICE',
      rect: [-W, -D, -7, -3.2],
      color: 'cto',
      labelAt: [-10.4, -5.2],
    },
    {
      id: 'board',
      label: 'DRAWING BOARD',
      rect: [-2.8, -D, 7.6, -4.6],
      color: 'board',
      labelAt: [2.4, -5.2],
    },
    {
      id: 'eng',
      label: 'ENGINEERING',
      rect: [-W, 0.4, -2.6, 5.6],
      color: 'eng',
      labelAt: [-8.2, 4.9],
    },
    {
      id: 'product',
      label: 'PRODUCT',
      rect: [4.6, -3.0, 11.2, 2.4],
      color: 'product',
      labelAt: [7.9, 1.7],
    },
    {
      id: 'kitchen',
      label: 'KITCHEN',
      rect: [5.6, 4.2, W, D],
      color: 'kitchen',
      labelAt: [11.0, 5.9],
    },
  ],

  // Wall segments as centre-lines. Door gaps are simply absent segments —
  // the nav graph in Layer 3 routes through the same gaps by name.
  walls: [
    // Perimeter
    { a: [-W, -D], b: [W, -D] }, // north
    { a: [-W, D], b: [W, D] }, // south
    { a: [-W, -D], b: [-W, D] }, // west
    { a: [W, -D], b: [W, D] }, // east

    // CTO office: solid east wall, south wall with a 2.4m door gap at X=-10.4
    { a: [-7, -D], b: [-7, -3.2] },
    { a: [-W, -3.2], b: [-11.6, -3.2] },
    { a: [-9.2, -3.2], b: [-7, -3.2] },

    // Kitchen nook: solid west partition, north partition with a door at X=10.5
    { a: [5.6, 4.2], b: [5.6, D] },
    { a: [5.6, 4.2], b: [9.4, 4.2] },
    { a: [11.6, 4.2], b: [W, 4.2] },
  ],

  // Doorway centres, so later layers can route through them explicitly
  doors: {
    ctoOffice: [-10.4, -3.2],
    kitchen: [10.5, 4.2],
  },

  // Furniture. `face` is the direction the item's "front" points, used to
  // orient desks/monitors/boards. Omitted means north-facing (0, -1).
  furniture: [
    // --- CTO office ---
    { type: 'desk', at: [-10.4, -7.4] },
    { type: 'chair', at: [-10.4, -6.05] },
    { type: 'monitor', at: [-10.4, -7.7] },
    { type: 'shelf', at: [-13.2, -6.6], face: [1, 0] },
    { type: 'plant', at: [-8.0, -4.4], scale: 1.1 },
    { type: 'papers', at: [-9.5, -7.1] },

    // --- Drawing board area ---
    { type: 'rug', at: [2.0, -7.4], width: 5.8, depth: 2.9 },
    { type: 'whiteboard', at: [2.0, -D + 0.15], face: [0, 1], width: 5.0 },
    { type: 'plant', at: [6.9, -8.9] },
    { type: 'cabinet', at: [12.7, -8.9] },
    { type: 'plant', at: [10.6, -6.4], scale: 0.95 },
    { type: 'plant', at: [-2.0, -8.9], scale: 0.9 },

    // --- Engineering pod: three desks in a row ---
    { type: 'desk', at: [-11.6, 2.2] },
    { type: 'chair', at: [-11.6, 3.55] },
    { type: 'monitor', at: [-11.6, 1.9] },
    { type: 'desk', at: [-8.0, 2.2] },
    { type: 'chair', at: [-8.0, 3.55] },
    { type: 'monitor', at: [-8.0, 1.9] },
    { type: 'desk', at: [-4.4, 2.2] },
    { type: 'chair', at: [-4.4, 3.55] },
    { type: 'monitor', at: [-4.4, 1.9] },
    { type: 'printer', at: [-13.3, 0.0], face: [1, 0] },
    { type: 'plant', at: [-13.3, 5.2] },

    // --- Between the pods: shared props the idle behaviours use ---
    { type: 'waterCooler', at: [-1.6, 6.6] },
    { type: 'sofa', at: [-7.6, 7.4] },
    { type: 'lowTable', at: [-7.6, 5.9] },
    { type: 'plant', at: [-4.6, 8.4], scale: 1.05 },

    // --- Product desk ---
    { type: 'desk', at: [7.2, -0.6] },
    { type: 'chair', at: [7.2, 0.75] },
    { type: 'monitor', at: [7.2, -0.9] },
    { type: 'cabinet', at: [10.3, -1.4] },
    { type: 'plant', at: [12.8, -2.2], scale: 1.15 },

    // --- Kitchen ---
    { type: 'counter', at: [13.4, 8.3], width: 3.0, face: [-1, 0] },
    { type: 'coffeeMachine', at: [13.3, 7.6], face: [-1, 0] },
    { type: 'fridge', at: [13.35, 6.0], face: [-1, 0] },
    { type: 'table', at: [8.4, 6.4] },
    { type: 'chair', at: [8.4, 7.75], scale: 0.95 },
    { type: 'chair', at: [8.4, 5.05], face: [0, 1], scale: 0.95 },
    { type: 'plant', at: [6.5, 9.2], scale: 0.9 },
  ],

  // Named anchors an agent can occupy. `look` is a point they turn toward.
  // Layers 2-5 address these by key only.
  stations: {
    cto_desk: { at: [-10.4, -6.05], look: [-10.4, -7.4], seated: true, zone: 'cto' },

    eng1_desk: { at: [-11.6, 3.55], look: [-11.6, 2.2], seated: true, zone: 'eng' },
    eng2_desk: { at: [-8.0, 3.55], look: [-8.0, 2.2], seated: true, zone: 'eng' },
    eng3_desk: { at: [-4.4, 3.55], look: [-4.4, 2.2], seated: true, zone: 'eng' },

    product_desk: { at: [7.2, 0.75], look: [7.2, -0.6], seated: true, zone: 'product' },

    // Presenter spot at the whiteboard, and the ring of listeners in front of it
    board_present: { at: [2.0, -7.9], look: [2.0, -9.6], zone: 'board' },
    board_seat_1: { at: [-0.9, -5.9], look: [2.0, -8.8], zone: 'board' },
    board_seat_2: { at: [1.0, -5.4], look: [2.0, -8.8], zone: 'board' },
    board_seat_3: { at: [3.2, -5.4], look: [2.0, -8.8], zone: 'board' },
    board_seat_4: { at: [5.0, -5.9], look: [2.0, -8.8], zone: 'board' },

    // Idle-behaviour destinations (Layer 4 uses these for coffee/snack runs)
    coffee: { at: [12.1, 7.6], look: [13.3, 7.6], zone: 'kitchen' },
    fridge: { at: [12.05, 6.0], look: [13.35, 6.0], zone: 'kitchen' },
    break_table: { at: [8.4, 7.75], look: [8.4, 6.4], seated: true, zone: 'kitchen' },
    water_cooler: { at: [-1.6, 7.8], look: [-1.6, 6.6], zone: 'eng' },
    printer: { at: [-12.0, 0.0], look: [-13.3, 0.0], zone: 'eng' },
    sofa: { at: [-7.6, 7.3], look: [-7.6, 5.9], seated: true, zone: 'eng' },
  },
};

// Convenience: zone rect lookup by id
export function zoneRect(id) {
  const z = LAYOUT.zones.find((z) => z.id === id);
  return z ? z.rect : null;
}
