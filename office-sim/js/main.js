// Entry point. Builds the stage, drops the office and its occupants into it,
// wires the few keyboard affordances, and starts the frame loop.
//
// Layers register their own per-frame updates through stage.onFrame(), so this
// file stays a thin wiring sheet. Layer 3 adds the nav grid and the locomotion
// controller, plus the debug overlays and URL parameters used to verify them.
//
// The gather/disperse keys here are a test harness, not a scenario: Layer 5
// owns scripted choreography and playback controls. These exist only so a walk
// can be triggered and watched without one.

import { createStage } from './scene.js';
import { buildOffice } from './build.js';
import { spawnAgents, setLabelsVisible, ROSTER } from './agents.js';
import { buildNav, buildNavOverlay, createPathOverlay } from './nav.js';
import { createLocomotion } from './locomotion.js';

const params = new URLSearchParams(location.search);
const still = params.get('still') === '1';

const stage = createStage(document.getElementById('view'), { preserveDrawingBuffer: still });

const office = buildOffice();
stage.scene.add(office);

const { agents, byId } = spawnAgents(stage.scene);

// --- Layer 3: navigation --------------------------------------------------
// Built after the office, because the obstacle footprints come from the
// geometry that buildOffice() actually produced.
const nav = buildNav(office);

const navOverlay = buildNavOverlay(nav);
stage.scene.add(navOverlay);

const pathOverlay = createPathOverlay();
stage.scene.add(pathOverlay.group);

const loco = createLocomotion(agents, nav, { pathOverlay });
stage.onFrame((dt) => loco.update(dt));

// Where everyone gathers when summoned to the board, and where they live. The
// presenter spot and the listener ring are both named in layout.js.
const BOARD_PLACES = {
  'cto-1': 'board_present',
  'product-1': 'board_seat_2',
  'engineering-1': 'board_seat_1',
  'engineering-2': 'board_seat_3',
  'engineering-3': 'board_seat_4',
};

const HOME_PLACES = Object.fromEntries(ROSTER.map((r) => [r.id, r.station]));

function sendAll(places) {
  for (const [id, station] of Object.entries(places)) {
    if (byId.has(id)) loco.goTo(id, station);
  }
}

const markers = office.getObjectByName('stationMarkers');
let labelsVisible = true;

// ?anchors=1 shows the station rings on load — handy when checking that the
// anchors Layers 2-5 depend on actually land on chairs and doorways.
if (params.get('anchors') === '1') {
  markers.visible = true;
  document.getElementById('hint-anchors').classList.add('on');
}

// ?labels=0 hides the name tags, for a clean screenshot of the room itself.
if (params.get('labels') === '0') {
  labelsVisible = false;
  setLabelsVisible(agents, false);
  document.getElementById('hint-labels').classList.add('off');
}

// ?nav=1 draws every blocked cell of the nav grid. This is the quickest way to
// see that the derivation is right: a desk that failed to register shows as a
// hole, and over-inflated walls show as a doorway sealed shut.
if (params.get('nav') === '1') {
  navOverlay.visible = true;
  document.getElementById('hint-nav').classList.add('on');
}

// ?paths=1 draws each walking agent's route in its own tier colour.
if (params.get('paths') === '1') {
  pathOverlay.group.visible = true;
  document.getElementById('hint-paths').classList.add('on');
}

// ?goto=cto-1:board_present;engineering-2:coffee sends agents somewhere on load.
// ?goto=board and ?goto=home are shorthands for the two full-office moves.
const goto = params.get('goto');
if (goto === 'board') {
  sendAll(BOARD_PLACES);
} else if (goto === 'home') {
  sendAll(HOME_PLACES);
} else if (goto) {
  for (const pair of goto.split(';')) {
    const [id, station] = pair.split(':');
    if (id && station) loco.goTo(id.trim(), station.trim());
  }
}

// ?focus=x,z&zoom=n frames one part of the office, for close inspection.
const focus = params.get('focus');
if (focus) {
  const [fx, fz] = focus.split(',').map(Number);
  if (Number.isFinite(fx) && Number.isFinite(fz)) {
    stage.focusOn(fx, fz, Number(params.get('zoom')) || 0);
  }
} else if (params.get('zoom')) {
  stage.focusOn(stage.controls.target.x, stage.controls.target.z, Number(params.get('zoom')));
}

window.addEventListener('keydown', (e) => {
  if (e.repeat) return;
  const key = e.key.toLowerCase();

  if (key === 'r') {
    stage.resetView();
  } else if (key === 'g') {
    markers.visible = !markers.visible;
    document.getElementById('hint-anchors').classList.toggle('on', markers.visible);
  } else if (key === 'l') {
    labelsVisible = !labelsVisible;
    setLabelsVisible(agents, labelsVisible);
    document.getElementById('hint-labels').classList.toggle('off', !labelsVisible);
  } else if (key === 'n') {
    navOverlay.visible = !navOverlay.visible;
    document.getElementById('hint-nav').classList.toggle('on', navOverlay.visible);
  } else if (key === 'p') {
    pathOverlay.group.visible = !pathOverlay.group.visible;
    document.getElementById('hint-paths').classList.toggle('on', pathOverlay.group.visible);
  } else if (key === '1') {
    sendAll(BOARD_PLACES);
  } else if (key === '0') {
    sendAll(HOME_PLACES);
  }
});

// ?still=1 draws a single frame instead of running the loop, so headless
// screenshots terminate instead of waiting on a page that never goes idle.
// Drawn inside a double rAF so the first frame is composited before capture.
if (still) {
  // ?t=seconds winds the simulation forward in fixed steps before drawing.
  // A walk can only be judged part-way through a stride, and a single large
  // dt would step the agent straight past the moment worth looking at.
  const t = Number(params.get('t')) || 0;
  if (t > 0) {
    const h = 1 / 60;
    for (let elapsed = 0; elapsed < t; elapsed += h) {
      stage.step(Math.min(h, t - elapsed));
    }
  }

  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      stage.renderOnce();
      // Hand the rendered frame back as a data URL. Reading the canvas
      // directly is deterministic, unlike a headless screenshot, which
      // captures whatever the compositor happens to hold at that moment.
      const shot = document.createElement('span');
      shot.id = 'shot';
      shot.dataset.png = stage.renderer.domElement.toDataURL('image/png');
      document.body.appendChild(shot);

      const el = document.createElement('pre');
      el.id = 'diag';
      el.style.display = 'none'; // read via --dump-dom, never shown
      el.textContent = JSON.stringify({
        sceneChildren: stage.scene.children.length,
        agents: agents.length,
        calls: stage.renderer.info.render.calls,
        tris: stage.renderer.info.render.triangles,
        canvas: [stage.renderer.domElement.width, stage.renderer.domElement.height],
        cam: stage.camera.position.toArray().map((n) => +n.toFixed(2)),
        zoom: stage.camera.zoom,
        frustum: [stage.camera.left, stage.camera.right, stage.camera.top, stage.camera.bottom],
        // Layer 3: enough state to check routing without reading the picture.
        t,
        navCells: [nav.nx, nav.nz],
        navBlocked: nav.blockedCount,
        navFootprints: nav.footprints.length,
        who: agents.map((a) => ({
          id: a.id,
          at: [+a.root.position.x.toFixed(2), +a.root.position.z.toFixed(2)],
          yaw: +a.root.rotation.y.toFixed(2),
          pose: a.pose,
          mode: loco.modeOf(a.id),
          station: loco.stationOf(a.id),
        })),
      });
      document.body.appendChild(el);
    })
  );
} else {
  stage.start();
}

// Handy for poking at the model from the devtools console.
window.SIM = { stage, office, agents, byId, nav, loco };
