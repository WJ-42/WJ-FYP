// The event log.
//
// The office already emits everything worth recording — behaviour.js fires an
// event on every ticket transition, timeout, document drop and reset — so this
// is a reader rather than a second source of truth. Layer 7 replaces the
// scripted driver with the live orchestrator stream and this panel keeps
// working, because it is watching the same events either way.
//
// Newest first, so the most recent thing is always in view without the panel
// having to scroll itself while someone is reading it.

import { PALETTE } from './palette.js';

const MAX_ENTRIES = 80;

const KIND_COLOUR = {
  transition: PALETTE.status.active,
  override: PALETTE.status.alert,
  timeout: PALETTE.status.waiting,
  drop: PALETTE.status.halted,
  note: PALETTE.status.idle,
  reset: PALETTE.status.idle,
  activity: PALETTE.status.rest,
};

// Activity changes fire constantly as agents settle in and out of their desks.
// Only the ones that say something a viewer could not already guess from
// watching are worth a line in the log.
const NOTABLE_ACTIVITIES = new Set([
  'blocked',
  'escalating',
  'escalated',
  'parking',
  'halted',
  'delivering',
  'onBreak',
]);

const hex = (n) => `#${n.toString(16).padStart(6, '0')}`;

function clock(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function createEventLog({ el, labelFor = (id) => id, timeFn = () => 0 }) {
  const entries = [];

  // Seeking replays the scenario, which re-fires every event in it. Writing all
  // of that to the page as it goes is wasted work nobody sees, so a replay
  // mutes the log and rebuilds it once at the end.
  let muted = false;

  function rowFor(entry) {
    const row = document.createElement('div');
    row.className = 'log-row';

    const time = document.createElement('span');
    time.className = 'log-time';
    time.textContent = clock(entry.at);

    const body = document.createElement('span');
    body.className = 'log-text';
    body.textContent = entry.text;
    body.style.borderLeftColor = hex(KIND_COLOUR[entry.kind] ?? PALETTE.status.idle);

    row.append(time, body);
    return row;
  }

  function redraw() {
    el.replaceChildren(...entries.map(rowFor));
  }

  function push(kind, text) {
    entries.unshift({ kind, text, at: timeFn() });
    if (entries.length > MAX_ENTRIES) entries.length = MAX_ENTRIES;
    if (muted) return;

    el.prepend(rowFor(entries[0]));
    while (el.childElementCount > MAX_ENTRIES) el.lastElementChild.remove();
  }

  /** Suspends drawing while a batch of events is replayed. */
  function mute(value) {
    const was = muted;
    muted = !!value;
    if (was && !muted) redraw();
  }

  function clear() {
    entries.length = 0;
    if (!muted) el.replaceChildren();
  }

  /** Turns one behaviour event into a line, or ignores it. */
  function record(event) {
    switch (event.type) {
      case 'transition': {
        // An override is the orchestrator refusing what was asked for, which is
        // the single most interesting thing that happens in a failing run, so
        // it is called out rather than logged as an ordinary transition.
        if (event.overrodeFrom) {
          push(
            'override',
            `${event.ticket}: ${event.from} → ${event.to} — system overrode "${event.overrodeFrom}" with "${event.trigger}"`
          );
        } else {
          push('transition', `${event.ticket}: ${event.from} → ${event.to} (${event.trigger})`);
        }
        return;
      }
      case 'timeout':
        push('timeout', `${labelFor(event.agent)} gave up waiting → ${event.trigger}`);
        return;
      case 'dropped':
        push('drop', `A document was left at ${event.at} (${event.count} there now)`);
        return;
      case 'reset':
        clear();
        push('reset', 'Office reset.');
        return;
      case 'activity':
        if (!NOTABLE_ACTIVITIES.has(event.activity)) return;
        push(
          'activity',
          `${labelFor(event.agent)} → ${event.detail ? `${event.activity} (${event.detail})` : event.activity}`
        );
        return;
      default:
        return;
    }
  }

  function note(text) {
    push('note', text);
  }

  return { record, note, clear, push, mute, count: () => entries.length };
}
