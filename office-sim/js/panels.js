// Resizable HUD panels.
//
// The right-hand rail holds two panels that compete for the same column: the
// scenario list and the event log. How much each one deserves depends entirely
// on what you are doing — picking a scenario wants the list, watching a failure
// unfold wants the log — so rather than guess a split, it is draggable.
//
// Two handles: one between the panels for the split, one down the left edge of
// the rail for its width. Double-clicking either resets that dimension.
//
// The chosen size is remembered in localStorage, which can throw outright in a
// private window or with site data blocked, so every access is guarded and the
// page falls back to the default layout rather than failing to start.

const MIN_PANEL = 110; // px: below this a panel is not worth showing
const MIN_RAIL = 280;
const RAIL_EDGE_GAP = 27; // keeps the rail off the middle of the screen

function readStore(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || '{}');
  } catch {
    return {};
  }
}

function writeStore(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore: a remembered panel size is not worth breaking the page over */
  }
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export function createResizableRail({
  rail,
  topPanel,
  splitter,
  widthHandle,
  storeKey = 'office-sim.rail',
}) {
  const stored = readStore(storeKey);
  let width = Number(stored.width) || null;
  let topHeight = Number(stored.topHeight) || null;

  function maxRail() {
    return Math.max(MIN_RAIL, window.innerWidth / 2 - RAIL_EDGE_GAP);
  }

  function maxTop() {
    // Whatever is left once the log keeps its minimum and the splitter its row.
    return Math.max(MIN_PANEL, rail.clientHeight - MIN_PANEL - splitter.offsetHeight);
  }

  function apply() {
    rail.style.width = width ? `${clamp(width, MIN_RAIL, maxRail())}px` : '';
    topPanel.style.height = topHeight ? `${clamp(topHeight, MIN_PANEL, maxTop())}px` : '';
  }

  function persist() {
    writeStore(storeKey, { width, topHeight });
  }

  // One drag implementation for both handles: capture the pointer so the drag
  // survives leaving the 8px handle, and report the new value from the pointer
  // position rather than from accumulated deltas, which drift.
  function draggable(handle, onMove, onReset) {
    let dragging = false;

    handle.addEventListener('pointerdown', (e) => {
      dragging = true;
      handle.setPointerCapture(e.pointerId);
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });

    handle.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      onMove(e);
      apply();
    });

    const end = (e) => {
      if (!dragging) return;
      dragging = false;
      if (handle.hasPointerCapture?.(e.pointerId)) handle.releasePointerCapture(e.pointerId);
      document.body.style.userSelect = '';
      persist();
    };

    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);

    handle.addEventListener('dblclick', () => {
      onReset();
      apply();
      persist();
    });
  }

  draggable(
    splitter,
    (e) => {
      topHeight = clamp(e.clientY - topPanel.getBoundingClientRect().top, MIN_PANEL, maxTop());
    },
    () => {
      topHeight = null;
    }
  );

  // The rail is anchored to the right, so dragging its left edge leftwards
  // widens it: the width is the distance from the pointer to that fixed edge.
  draggable(
    widthHandle,
    (e) => {
      width = clamp(rail.getBoundingClientRect().right - e.clientX, MIN_RAIL, maxRail());
    },
    () => {
      width = null;
    }
  );

  // A window that got narrower must not leave the rail spanning the screen.
  window.addEventListener('resize', apply);

  apply();

  return { apply };
}
