// Entry point. Builds the stage, drops the office and its occupants into it,
// wires the few keyboard affordances, and starts the frame loop.
//
// Layers register their own per-frame updates through stage.onFrame(), so this
// file stays a thin wiring sheet. Nothing moves yet: Layer 2 only places the
// agents at their home stations.

import { createStage } from './scene.js';
import { buildOffice } from './build.js';
import { spawnAgents, setLabelsVisible } from './agents.js';

const params = new URLSearchParams(location.search);
const still = params.get('still') === '1';

const stage = createStage(document.getElementById('view'), { preserveDrawingBuffer: still });

const office = buildOffice();
stage.scene.add(office);

const { agents, byId } = spawnAgents(stage.scene);

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
  }
});

// ?still=1 draws a single frame instead of running the loop, so headless
// screenshots terminate instead of waiting on a page that never goes idle.
// Drawn inside a double rAF so the first frame is composited before capture.
if (still) {
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
      });
      document.body.appendChild(el);
    })
  );
} else {
  stage.start();
}

// Handy for poking at the model from the devtools console.
window.SIM = { stage, office, agents, byId };
