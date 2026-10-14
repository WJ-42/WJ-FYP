// Observation: picking an agent out of the scene and showing what it is doing.
//
// This is what turns the office from an animation into something you can
// interrogate. Up to here the only way to find out why someone was walking
// across the room was to read the badge over their head as they went past.
//
// Picking raycasts against the agent rigs and walks up to the nearest root,
// which is exactly what Layer 2 left `root.userData.agent` on the rig for.
//
// Selection is shown with a ring on the floor rather than by recolouring the
// figure. The shirt colour is the role tier and carries meaning, so tinting it
// to show selection would overwrite the one piece of information the figure
// already conveys.

import * as THREE from 'three';
import { PALETTE } from './palette.js';

const HOVER_COLOUR = 0xeef1ec;
const CLICK_SLOP = 5; // px of movement still counted as a click, not a drag
const CLICK_MS = 500;

function ring(inner, outer, colour, opacity) {
  const mesh = new THREE.Mesh(
    new THREE.RingGeometry(inner, outer, 40),
    new THREE.MeshBasicMaterial({
      color: colour,
      transparent: true,
      opacity,
      side: THREE.DoubleSide,
      depthWrite: false,
    })
  );
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.y = 0.035;
  mesh.visible = false;
  return mesh;
}

export function createInspector({ stage, agents, bhv, loco, el }) {
  const canvas = stage.renderer.domElement;
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const roots = agents.map((a) => a.root);

  const hoverRing = ring(0.34, 0.4, HOVER_COLOUR, 0.5);
  const selectRing = ring(0.36, 0.46, PALETTE.status.active, 0.95);
  stage.scene.add(hoverRing, selectRing);

  let hovered = null;
  let selected = null;

  function agentFromObject(object) {
    for (let o = object; o; o = o.parent) {
      if (o.userData && o.userData.agent) return o.userData.agent;
    }
    return null;
  }

  /** Returns the agent under a point in client (CSS pixel) coordinates. */
  function pickAtClient(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    ndc.x = ((clientX - rect.left) / rect.width) * 2 - 1;
    ndc.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(ndc, stage.camera);
    const hits = raycaster.intersectObjects(roots, true);
    for (const hit of hits) {
      const agent = agentFromObject(hit.object);
      if (agent) return agent;
    }
    return null;
  }

  function select(agentOrId) {
    const next =
      typeof agentOrId === 'string' ? agents.find((a) => a.id === agentOrId) ?? null : agentOrId;
    selected = next ?? null;
    render();
  }

  function setHover(agent) {
    if (hovered === agent) return;
    hovered = agent;
    canvas.style.cursor = agent ? 'pointer' : '';
  }

  // --- the panel -----------------------------------------------------------

  function row(label, value, accent) {
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    dd.textContent = value;
    if (accent) dd.style.color = accent;
    return [dt, dd];
  }

  // What the panel should currently say, as plain data. Kept separate from the
  // drawing so the two can be compared cheaply: the panel has to track a target
  // that is walking around, but rebuilding a dozen DOM nodes on every frame to
  // show text that changes every few seconds is a waste of a frame budget.
  function describe(agent) {
    const spec = agent.spec;
    const state = bhv.stateOf(agent.id) ?? {};
    const ticket = bhv.ticket;
    const mode = loco.modeOf(agent.id);
    const station = loco.stationOf(agent.id);

    const rows = [
      ['Role', `${spec.role} (tier ${spec.tier})`],
      ['Agent id', spec.id],
      ['Model', spec.model],
      ['Doing', state.detail ? `${state.activity} — ${state.detail}` : (state.activity ?? '—')],
      ['Where', mode === 'idle' ? (station ?? 'standing') : `walking (${mode})`],
    ];

    if (agent.isCarrying) rows.push(['Carrying', 'a document']);

    // The ticket is shown against the agent that actually holds it rather than
    // on everyone, so "what is this person working on" can honestly answer
    // "nothing" when that is the case.
    if (ticket && ticket.assignee === agent.id) {
      rows.push(['Ticket', `${ticket.id} · ${ticket.status}`]);
      rows.push(['Attempts', `${ticket.retryCount} of ${ticket.retryCap}`]);
      rows.push(['Escalations', `${ticket.escalationCount} of ${ticket.escalationCap}`]);
    } else if (ticket && state.engaged) {
      rows.push(['Ticket', `${ticket.id} · ${ticket.status}`]);
    } else {
      rows.push(['Ticket', 'none']);
    }

    return rows;
  }

  let drawn = null;

  function render() {
    if (!selected) {
      el.panel.hidden = true;
      drawn = null;
      return;
    }

    const rows = describe(selected);
    const signature = `${selected.id}|${rows.map((r) => r.join('=')).join('|')}`;
    if (signature === drawn) return;
    drawn = signature;

    el.name.textContent = selected.spec.label;
    el.dot.style.background = `#${selected.spec.tierColor.toString(16).padStart(6, '0')}`;

    el.body.replaceChildren();
    for (const [label, value] of rows) el.body.append(...row(label, value));

    el.panel.hidden = false;
  }

  // --- pointer -------------------------------------------------------------

  let down = null;

  canvas.addEventListener('pointermove', (e) => {
    setHover(pickAtClient(e.clientX, e.clientY));
  });

  canvas.addEventListener('pointerleave', () => setHover(null));

  canvas.addEventListener('pointerdown', (e) => {
    down = { x: e.clientX, y: e.clientY, t: performance.now() };
  });

  // Left-drag pans the camera, so a press that moved is a pan and must not
  // also select. Only a press that stayed put counts as a click.
  canvas.addEventListener('pointerup', (e) => {
    if (!down) return;
    const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
    const held = performance.now() - down.t;
    down = null;
    if (moved > CLICK_SLOP || held > CLICK_MS) return;
    select(pickAtClient(e.clientX, e.clientY));
  });

  el.close.addEventListener('click', () => select(null));

  // --- frame ---------------------------------------------------------------

  function update() {
    if (hovered && hovered !== selected) {
      hoverRing.position.set(hovered.root.position.x, 0.035, hovered.root.position.z);
      hoverRing.visible = true;
    } else {
      hoverRing.visible = false;
    }

    if (selected) {
      selectRing.position.set(selected.root.position.x, 0.035, selected.root.position.z);
      selectRing.material.color.setHex(selected.spec.tierColor);
      selectRing.visible = true;
      // The panel tracks a moving target, so it has to be redrawn rather than
      // only refreshed when the selection changes.
      render();
    } else {
      selectRing.visible = false;
    }
  }

  // Verification aid: project each agent back to screen coordinates and pick
  // there. Picking is otherwise the one part of this layer that cannot be
  // checked from a screenshot, since it needs a pointer. Two agents standing on
  // top of each other can legitimately shadow one another, so a mismatch is
  // worth reading rather than treating as an automatic failure.
  function selfTest() {
    const rect = canvas.getBoundingClientRect();
    const v = new THREE.Vector3();
    return agents.map((agent) => {
      v.set(agent.root.position.x, 0.9, agent.root.position.z);
      v.project(stage.camera);
      const x = rect.left + ((v.x + 1) / 2) * rect.width;
      const y = rect.top + ((1 - v.y) / 2) * rect.height;
      const hit = pickAtClient(x, y);
      return { id: agent.id, hit: hit ? hit.id : null, ok: hit?.id === agent.id };
    });
  }

  return {
    update,
    select,
    pickAtClient,
    selfTest,
    selected: () => selected,
    hovered: () => hovered,
  };
}
