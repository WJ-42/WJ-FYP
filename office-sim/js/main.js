// Entry point. Builds the stage, drops the office and its occupants into it,
// wires the panel and the keyboard, and starts the frame loop.
//
// Layers register their work through stage.onFrame(), so this file stays a
// wiring sheet. The one piece of real logic here is the frame loop itself:
// every per-frame update is multiplied by the player's time scale, so changing
// the playback speed changes how fast people walk, not just how fast the script
// fires, and pausing freezes the office mid-stride rather than leaving everyone
// walking about with the script stopped.

import { createStage } from './scene.js';
import { buildOffice } from './build.js';
import { spawnAgents, setLabelsVisible } from './agents.js';
import { setBadgesVisible } from './characters.js';
import { buildNav, buildNavOverlay, createPathOverlay } from './nav.js';
import { createLocomotion } from './locomotion.js';
import { createBehaviour, HOME } from './behaviour.js';
import { createTicket } from './tickets.js';
import { SCENARIOS } from './scenarios.js';
import { createPlayer, SPEEDS } from './player.js';

const params = new URLSearchParams(location.search);
const still = params.get('still') === '1';

const stage = createStage(document.getElementById('view'), { preserveDrawingBuffer: still });

const office = buildOffice();
stage.scene.add(office);

const { agents, byId } = spawnAgents(stage.scene);

// --- Navigation (Layer 3) --------------------------------------------------
// Built after the office, because the obstacle footprints come from the
// geometry that buildOffice() actually produced.
const nav = buildNav(office);

const navOverlay = buildNavOverlay(nav);
stage.scene.add(navOverlay);

const pathOverlay = createPathOverlay();
stage.scene.add(pathOverlay.group);

const loco = createLocomotion(agents, nav, { pathOverlay });

// --- Behaviour (Layer 4) ---------------------------------------------------
// A factory rather than one ticket: restarting a scenario needs a ticket with
// its budgets back at zero, not the one the last run exhausted.
const makeTicket = () =>
  createTicket({
    id: params.get('ticket') || 'FYP-42',
    title: 'Demonstration ticket',
    assignee: params.get('assignee') || 'engineering-2',
  });

const bhv = createBehaviour({ agents, byId, loco, scene: stage.scene });
bhv.setTicket(makeTicket());

// --- Scenarios (Layer 5) ---------------------------------------------------
const player = createPlayer({ bhv, loco, makeTicket, onChange: renderPanel });

// ?restartAt=n presses Restart n seconds in, so the reset path can be checked
// headlessly like everything else. Measured in unscaled time, independent of
// the playback speed, so the moment it fires does not move when the speed does.
const restartAt = Number(params.get('restartAt')) || 0;
let sinceLoad = 0;
let hasRestarted = false;

stage.onFrame((dt) => {
  if (restartAt > 0 && !hasRestarted) {
    sinceLoad += dt;
    if (sinceLoad >= restartAt) {
      hasRestarted = true;
      player.restart();
    }
  }

  const scaled = dt * player.timeScale();
  loco.update(scaled);
  bhv.update(scaled);
  player.update(scaled);
  updateProgress();
});

// --- Panel -----------------------------------------------------------------

const el = {
  list: document.getElementById('scenario-list'),
  speeds: document.getElementById('speeds'),
  play: document.getElementById('btn-play'),
  restart: document.getElementById('btn-restart'),
  bar: document.getElementById('scenario-bar'),
  name: document.getElementById('scenario-name'),
  time: document.getElementById('scenario-time'),
  note: document.getElementById('scenario-note'),
};

for (const scenario of SCENARIOS) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = scenario.name;
  button.title = scenario.summary;
  button.dataset.id = scenario.id;
  button.setAttribute('aria-pressed', 'false');
  button.addEventListener('click', () => player.load(scenario.id));
  el.list.appendChild(button);
}

for (const value of SPEEDS) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = `${value}×`;
  button.dataset.speed = String(value);
  button.setAttribute('aria-pressed', 'false');
  button.addEventListener('click', () => player.setSpeed(value));
  el.speeds.appendChild(button);
}

el.play.addEventListener('click', () => player.togglePaused());
el.restart.addEventListener('click', () => player.restart());

function clock(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

function renderPanel(state) {
  for (const button of el.list.children) {
    button.setAttribute('aria-pressed', String(button.dataset.id === state.id));
  }
  for (const button of el.speeds.children) {
    button.setAttribute('aria-pressed', String(Number(button.dataset.speed) === state.speed));
  }
  el.play.textContent = state.paused ? 'Play' : 'Pause';
  el.play.setAttribute('aria-pressed', String(state.paused));
  el.name.textContent = state.name ?? '—';
  el.note.textContent = state.note || 'Pick a scenario to play it.';
}

function updateProgress() {
  const state = player.state();
  el.bar.style.width = `${(state.progress * 100).toFixed(1)}%`;
  el.time.textContent = `${clock(state.elapsed)} / ${clock(state.duration)}`;
}

// --- Debug overlays and URL parameters -------------------------------------

function sendAll(places) {
  for (const [id, station] of Object.entries(places)) {
    if (byId.has(id)) loco.goTo(id, station);
  }
}

const markers = office.getObjectByName('stationMarkers');
let labelsVisible = true;
let badgesVisible = true;

// ?anchors=1 shows the station rings on load — handy when checking that the
// anchors the later layers depend on actually land on chairs and doorways.
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

// ?badges=0 hides the activity badges.
if (params.get('badges') === '0') {
  badgesVisible = false;
  setBadgesVisible(agents, false);
  document.getElementById('hint-badges').classList.add('off');
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

// ?speed=n and ?paused=1 set the transport up before anything starts, which is
// what a still capture of a particular moment needs.
if (params.get('speed')) player.setSpeed(Number(params.get('speed')));

// ?scenario=id plays one on load. ?demo= is accepted as well, because that is
// what Layer 4 called it before the library existed.
const wanted = params.get('scenario') || params.get('demo');
if (wanted) player.load(wanted);

if (params.get('paused') === '1') player.setPaused(true);

// ?goto=cto-1:board_present;engineering-2:coffee drives locomotion directly,
// bypassing the behaviour model. Kept for checking routing in isolation.
const goto = params.get('goto');
if (goto === 'home') {
  sendAll(HOME);
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

renderPanel(player.state());
updateProgress();

window.addEventListener('keydown', (e) => {
  if (e.repeat) return;
  // Space and Enter belong to whichever button has focus, not to the office.
  if (e.target instanceof HTMLButtonElement && (e.key === ' ' || e.key === 'Enter')) return;

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
  } else if (key === 'b') {
    badgesVisible = !badgesVisible;
    setBadgesVisible(agents, badgesVisible);
    document.getElementById('hint-badges').classList.toggle('off', !badgesVisible);
  } else if (e.key === ' ') {
    e.preventDefault();
    player.togglePaused();
  } else if (e.key === 'Enter') {
    player.restart();
  } else if (e.key === '-') {
    player.nudgeSpeed(-1);
  } else if (e.key === '=' || e.key === '+') {
    player.nudgeSpeed(1);
  } else if (key >= '1' && key <= '9') {
    const scenario = SCENARIOS[Number(key) - 1];
    if (scenario) player.load(scenario.id);
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

      const diag = document.createElement('pre');
      diag.id = 'diag';
      diag.style.display = 'none'; // read via --dump-dom, never shown
      const ticket = bhv.ticket;
      const p = player.state();
      diag.textContent = JSON.stringify({
        sceneChildren: stage.scene.children.length,
        agents: agents.length,
        calls: stage.renderer.info.render.calls,
        tris: stage.renderer.info.render.triangles,
        canvas: [stage.renderer.domElement.width, stage.renderer.domElement.height],
        cam: stage.camera.position.toArray().map((n) => +n.toFixed(2)),
        zoom: stage.camera.zoom,
        t,
        navCells: [nav.nx, nav.nz],
        navBlocked: nav.blockedCount,
        navFootprints: nav.footprints.length,
        who: agents.map((a) => ({
          id: a.id,
          at: [+a.root.position.x.toFixed(2), +a.root.position.z.toFixed(2)],
          pose: a.pose,
          mode: loco.modeOf(a.id),
          station: loco.stationOf(a.id),
          activity: bhv.stateOf(a.id)?.activity ?? null,
          badge: a.badgeText,
          carrying: a.isCarrying,
          timer: a.timerBar.visible ? +a.timerBar.userData.drawn.toFixed(2) : null,
        })),
        ticket: ticket && {
          id: ticket.id,
          status: ticket.status,
          assignee: ticket.assignee,
          retry: `${ticket.retryCount}/${ticket.retryCap}`,
          escalations: `${ticket.escalationCount}/${ticket.escalationCap}`,
        },
        dropped: bhv.droppedCount(),
        player: {
          scenario: p.id,
          elapsed: +p.elapsed.toFixed(1),
          duration: p.duration,
          paused: p.paused,
          speed: p.speed,
          finished: p.finished,
          note: p.note,
        },
      });
      document.body.appendChild(diag);
    })
  );
} else {
  stage.start();
}

// Handy for poking at the model from the devtools console.
window.SIM = { stage, office, agents, byId, nav, loco, bhv, player };
