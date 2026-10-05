// The agent roster, and placing it in the office.
//
// The identities here are the real ones the orchestrator builds, not invented
// characters: config/roles.yaml declares cto (tier 3), product (tier 2) and
// engineering (tier 1, count 3), and claude_agent.build_agent_pool names the
// instances `<roleId>-<n>`. Keeping the same ids means a message from
// `engineering-2` in a real run already names the figure to animate, with no
// mapping layer invented in between.

import * as THREE from 'three';
import { PALETTE } from './palette.js';
import { LAYOUT } from './layout.js';
import { yawToFace } from './build.js';
import { makeAgent, applyPose } from './characters.js';

// Appearance varies only so five figures do not read as five copies of one
// person. The shirt is the one part that carries meaning: it is always the
// role-tier colour, matching the 2D dashboard's tier colouring.
export const ROSTER = [
  {
    id: 'cto-1',
    roleId: 'cto',
    role: 'CTO',
    label: 'CTO',
    tier: 3,
    model: 'claude-opus-5',
    station: 'cto_desk',
    tierColor: PALETTE.tier.cto,
    skin: 0x8d5a34,
    hair: 0x1c1512,
    hairStyle: 'short',
    trousers: 0x3a3f46,
    heightScale: 1.03,
  },
  {
    id: 'product-1',
    roleId: 'product',
    role: 'Product',
    label: 'Product',
    tier: 2,
    model: 'claude-sonnet-5',
    station: 'product_desk',
    tierColor: PALETTE.tier.product,
    skin: 0xefc69e,
    hair: 0x4a3425,
    hairStyle: 'bun',
    trousers: 0x4a4a52,
    heightScale: 0.97,
  },
  {
    id: 'engineering-1',
    roleId: 'engineering',
    role: 'Engineering',
    label: 'Engineering 1',
    tier: 1,
    model: 'claude-haiku-4-5',
    station: 'eng1_desk',
    tierColor: PALETTE.tier.engineering,
    skin: 0xd99f6f,
    hair: 0x241a14,
    hairStyle: 'short',
    trousers: 0x3e4a52,
    heightScale: 1.0,
  },
  {
    id: 'engineering-2',
    roleId: 'engineering',
    role: 'Engineering',
    label: 'Engineering 2',
    tier: 1,
    model: 'claude-haiku-4-5',
    station: 'eng2_desk',
    tierColor: PALETTE.tier.engineering,
    skin: 0xa56c45,
    hair: 0x111111,
    hairStyle: 'none',
    trousers: 0x454b45,
    heightScale: 1.05,
  },
  {
    id: 'engineering-3',
    roleId: 'engineering',
    role: 'Engineering',
    label: 'Engineering 3',
    tier: 1,
    model: 'claude-haiku-4-5',
    station: 'eng3_desk',
    tierColor: PALETTE.tier.engineering,
    skin: 0xf0d0ae,
    hair: 0x6b4a2f,
    hairStyle: 'bun',
    trousers: 0x4c4550,
    heightScale: 0.95,
  },
];

// Puts an agent at a named station, facing whatever that station looks at, and
// seated or standing according to the station itself rather than a guess.
export function placeAtStation(agent, stationName) {
  const station = LAYOUT.stations[stationName];
  if (!station) {
    console.warn(`Unknown station "${stationName}" for agent ${agent.id}`);
    return;
  }
  agent.root.position.set(station.at[0], 0, station.at[1]);

  const dx = station.look[0] - station.at[0];
  const dz = station.look[1] - station.at[1];
  const len = Math.hypot(dx, dz) || 1;
  agent.root.rotation.y = yawToFace([dx / len, dz / len]);

  applyPose(agent, station.seated ? 'seated' : 'standing');
  agent.station = stationName;
}

export function spawnAgents(parent) {
  const group = new THREE.Group();
  group.name = 'agents';
  const agents = [];

  for (const spec of ROSTER) {
    const agent = makeAgent(spec);
    placeAtStation(agent, spec.station);
    group.add(agent.root);
    agents.push(agent);
  }

  parent.add(group);
  return { group, agents, byId: new Map(agents.map((a) => [a.id, a])) };
}

export function setLabelsVisible(agents, visible) {
  for (const agent of agents) agent.label.visible = visible;
}
