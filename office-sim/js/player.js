// Scenario playback: which scenario is loaded, where it has got to, and the
// transport controls over it.
//
// Speed and pause are deliberately not the player's own private business. The
// player publishes a time scale and the frame loop multiplies every update by
// it, so slowing the office down slows the walking and the walk cycle too
// rather than only the rate at which beats fire. Pause is the same mechanism at
// zero, which is why a paused office freezes mid-stride instead of continuing
// to walk about with the script stopped.

import { SCENARIOS_BY_ID, scenarioDuration } from './scenarios.js';

export const SPEEDS = [0.5, 1, 2, 4];

export function createPlayer({ bhv, loco, makeTicket, step, onChange = () => {} }) {
  let scenario = null;
  let duration = 0;
  let clock = 0;
  let nextBeat = 0;
  let paused = false;
  let speed = 1;
  let note = '';
  let finished = false;

  const ACTIONS = {
    summon: () => bhv.summonToBoard(),
    disperse: () => bhv.disperse(),
    fire: (trigger) => bhv.fire(trigger),
    goto: (id, station) => loco.goTo(id, station),
    deliver: (id, station) => bhv.deliver(id, station),
    block: (id, seconds, trigger) => bhv.block(id, seconds, trigger),
    break: (id, station, seconds) => bhv.takeBreak(id, station, seconds),
  };

  function publish() {
    onChange(state());
  }

  function state() {
    return {
      id: scenario?.id ?? null,
      name: scenario?.name ?? null,
      summary: scenario?.summary ?? null,
      note,
      elapsed: clock,
      duration,
      progress: duration ? Math.min(1, clock / duration) : 0,
      paused,
      speed,
      finished,
    };
  }

  /** Loads a scenario and starts it from a clean office. */
  function load(id) {
    const next = SCENARIOS_BY_ID.get(id);
    if (!next) {
      console.warn(`player: no scenario "${id}"`);
      return false;
    }
    scenario = next;
    duration = scenarioDuration(scenario);
    bhv.resetOffice(makeTicket());
    clock = 0;
    nextBeat = 0;
    note = scenario.summary;
    paused = false;
    finished = false;
    publish();
    return true;
  }

  function restart() {
    if (!scenario) return;
    load(scenario.id);
  }

  // The fixed step a seek replays at. Matching the frame loop's own cadence
  // keeps a seeked office in the same state a played-through one would reach.
  const SEEK_STEP = 1 / 60;

  /**
   * Jumps to a point in the scenario.
   *
   * A simulation cannot be rewound: an agent half way across the office cannot
   * be un-walked, and a merged ticket cannot be un-merged. So seeking replays —
   * the office is reset and the scenario re-run at a fixed step up to the
   * target. That is only honest because the randomness is seeded; against
   * Math.random the same seek would land somewhere slightly different each
   * time, and scrubbing back and forth would quietly change the run.
   *
   * Paused stays paused across a seek, so you can scrub a frozen office.
   */
  function seek(seconds) {
    if (!scenario || typeof step !== 'function') return;

    const target = Math.max(0, Math.min(duration, seconds));
    const wasPaused = paused;

    load(scenario.id);

    for (let t = 0; t < target; t += SEEK_STEP) {
      step(Math.min(SEEK_STEP, target - t));
    }

    paused = wasPaused;
    publish();
  }

  function setPaused(value) {
    paused = !!value;
    publish();
  }

  function togglePaused() {
    setPaused(!paused);
  }

  function setSpeed(value) {
    if (!SPEEDS.includes(value)) return;
    speed = value;
    publish();
  }

  function nudgeSpeed(delta) {
    const i = SPEEDS.indexOf(speed);
    setSpeed(SPEEDS[Math.max(0, Math.min(SPEEDS.length - 1, i + delta))]);
  }

  // The scale every other per-frame update is multiplied by. Pause is zero,
  // which stops the agents as well as the script.
  function timeScale() {
    return paused ? 0 : speed;
  }

  /** `dt` has already been scaled by timeScale() in the frame loop. */
  function update(dt) {
    if (!scenario || dt <= 0) return;

    clock += dt;

    let changed = false;
    while (nextBeat < scenario.beats.length && scenario.beats[nextBeat].at <= clock) {
      const beat = scenario.beats[nextBeat];
      nextBeat += 1;

      if (beat.do) {
        const fn = ACTIONS[beat.do];
        if (!fn) console.warn(`player: unknown action "${beat.do}"`);
        else fn(...(beat.args ?? []));
      }
      if (beat.note) {
        note = beat.note;
        changed = true;
      }
    }

    // Past the end the office is left running rather than frozen: the idle
    // behaviour taking over is a reasonable thing to be looking at, and a
    // scenario that ends by slamming to a halt reads like a crash.
    if (!finished && clock >= duration) {
      finished = true;
      changed = true;
    }

    if (changed) publish();
  }

  return {
    load,
    restart,
    seek,
    setPaused,
    togglePaused,
    setSpeed,
    nudgeSpeed,
    timeScale,
    update,
    state,
    publish,
  };
}
