// Entry point. Builds the stage, drops the office model into it, wires the few
// keyboard affordances, and starts the frame loop.
//
// Layer 1 has no moving parts — later layers register their own per-frame
// updates through stage.onFrame(), so this file stays a thin wiring sheet.

import { createStage } from './scene.js';
import { buildOffice } from './build.js';

const stage = createStage(document.getElementById('view'));
const office = buildOffice();
stage.scene.add(office);

const markers = office.getObjectByName('stationMarkers');

// ?anchors=1 shows the station rings on load — handy when checking that the
// anchors Layers 2-5 depend on actually land on chairs and doorways.
if (new URLSearchParams(location.search).get('anchors') === '1') {
  markers.visible = true;
  document.getElementById('hint-anchors').classList.add('on');
}

window.addEventListener('keydown', (e) => {
  if (e.repeat) return;
  const key = e.key.toLowerCase();
  if (key === 'r') {
    stage.resetView();
  } else if (key === 'g') {
    markers.visible = !markers.visible;
    document.getElementById('hint-anchors').classList.toggle('on', markers.visible);
  }
});

stage.start();

// Handy for poking at the model from the devtools console.
window.SIM = { stage, office };
