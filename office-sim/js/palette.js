// Colour tokens for the office model. Kept in one place so the whole prototype
// can be reskinned without hunting through geometry code.
//
// Tier colours match the 2D dashboard's role tiers (CTO warm red, Product
// violet, Engineering teal) so the same agent reads the same in both views.

export const PALETTE = {
  // Environment
  bg: 0xc9cfc6,
  ground: 0xc0c7bd,
  slabEdge: 0xd6d2c7,
  floor: 0xe9e5dd,

  // Zone carpets. Tinted floor patches that name each part of the office.
  zone: {
    cto: 0xe0d2c0,
    board: 0xc9d7d2,
    eng: 0xccd5de,
    product: 0xd9cfe3,
    kitchen: 0xe3dcc6,
    backlog: 0xdcd3cd,
  },

  // Activity badge colours (Layer 4). Deliberately a small set rather than one
  // hue per ticket state: nine colours would be unreadable at this camera
  // distance, so the badge carries the state as text and the colour says only
  // how much attention it wants. `active` and `waiting` are lifted from the 2D
  // dashboard's accent and tier-3 tokens so the two views agree.
  status: {
    idle: 0x6b7a8f,
    active: 0x5b8def,
    rest: 0xc9a35f,
    waiting: 0xe2a53a,
    alert: 0xe5533d,
    halted: 0x7d5f5f,
  },

  // A ticket as a physical object: carried in hand, or left at the backlog.
  ticketPaper: 0xfaf8f2,
  ticketEdge: 0xc8c3b4,

  // Walls are deliberately low (see LAYOUT.wallHeight) so the angled camera
  // always sees over them. Two tones read as a lit top face vs shaded side.
  wallTop: 0xf6f3ec,
  wallSide: 0xe2ddd2,

  // Furniture
  deskTop: 0xc9a881,
  deskFrame: 0x3b3f47,
  chair: 0x4f555d,
  chairSoft: 0x636a73,
  monitorBody: 0x2b2f35,
  monitorScreen: 0xa3b6bf,
  boardFace: 0xfcfbf7,
  boardFrame: 0x99a0a5,
  shelf: 0xb08a63,
  cabinet: 0xb8a995,
  counter: 0xc9b59a,
  counterTop: 0xb6bdc1,
  fridge: 0xd2d7db,
  appliance: 0x33383e,
  tableTop: 0xd8cbb6,
  paper: 0xf7f5ef,
  rug: 0xb9bdb2,
  rugTrim: 0xa6ab9e,
  sofa: 0x7f8a84,
  sofaCushion: 0x8e9892,
  water: 0x8fb8c9,

  // Greenery, for warmth in an otherwise grey model
  plantPot: 0xb1735a,
  leafA: 0x6c8c5b,
  leafB: 0x83a36b,

  // Role tiers — mirrors the dashboard's tier colours
  tier: {
    cto: 0xe5533d,
    product: 0xa06ce0,
    engineering: 0x2fb8a8,
  },

  // Floor decal text
  labelInk: '#70766e',
};
