// Resizable HUD panels.
//
// The scenario list and the event log each get their own edges to drag: a left
// edge for width, one horizontal edge for height, and the corner between them
// for both at once. They are sized independently rather than sharing a split,
// so making the log taller does not mean giving up the scenario list.
//
// Each panel lives in a wrapper that is anchored to one corner — scenarios to
// the top right, the log to the bottom right — and the grips sit on the
// wrapper's edges rather than inside the panel. That is deliberate: the panels
// clip their own overflow so their contents can scroll, and a grip placed
// inside one would be clipped away at exactly the edge it needs to sit on.
//
// The two are clamped against each other so they can never overlap, which means
// dragging one eventually runs out of room rather than burying the other.
//
// Sizes are remembered per browser. localStorage throws outright in a private
// window or with site data blocked, so every access is guarded and the page
// falls back to the default layout rather than failing to start.

const GAP = 18; // breathing room between the panels
// Wide enough that each panel's height grip gets its own band: at 12px the
// two of them landed on top of each other and only the lower one in the DOM
// could be grabbed at all.

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
    /* a remembered panel size is not worth breaking the page over */
  }
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(Math.max(lo, hi), v));

/**
 * Makes one wrapper resizable.
 *
 * `anchor` says which edge is pinned, and therefore which way a height drag
 * grows the panel. `limits` is supplied by the caller because the ceiling on
 * one panel depends on where the other one currently ends.
 */
function createResizablePanel({
  wrap,
  gripX,
  gripY,
  gripCorner,
  anchor, // 'top' | 'bottom'
  minWidth = 260,
  minHeight = 110,
  limits,
  storeKey,
  onChange = () => {},
}) {
  const stored = readStore(storeKey);
  let width = Number(stored.width) || null;
  let height = Number(stored.height) || null;

  function apply() {
    const { maxWidth, maxHeight } = limits();
    wrap.style.width = width ? `${clamp(width, minWidth, maxWidth)}px` : '';
    wrap.style.height = height ? `${clamp(height, minHeight, maxHeight)}px` : '';
    onChange();
  }

  function persist() {
    writeStore(storeKey, { width, height });
  }

  function widthFrom(clientX) {
    // The wrapper is pinned to the right, so its width is the distance from the
    // pointer to that fixed edge.
    return wrap.getBoundingClientRect().right - clientX;
  }

  function heightFrom(clientY) {
    const rect = wrap.getBoundingClientRect();
    return anchor === 'top' ? clientY - rect.top : rect.bottom - clientY;
  }

  // One drag implementation for all three grips. The pointer is captured so a
  // drag survives leaving the 10px grip, and each move recomputes from the
  // pointer position rather than accumulating deltas, which drift.
  function draggable(handle, onMove, onReset) {
    if (!handle) return;
    let dragging = false;

    handle.addEventListener('pointerdown', (e) => {
      dragging = true;
      handle.setPointerCapture(e.pointerId);
      document.body.style.userSelect = 'none';
      e.preventDefault();
      e.stopPropagation();
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

    handle.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      onReset();
      apply();
      persist();
    });
  }

  const setWidth = (e) => {
    width = widthFrom(e.clientX);
  };
  const setHeight = (e) => {
    height = heightFrom(e.clientY);
  };

  draggable(gripX, setWidth, () => {
    width = null;
  });

  draggable(gripY, setHeight, () => {
    height = null;
  });

  draggable(
    gripCorner,
    (e) => {
      setWidth(e);
      setHeight(e);
    },
    () => {
      width = null;
      height = null;
    }
  );

  return {
    apply,
    // The measured outer edge, which the other panel clamps itself against.
    rect: () => wrap.getBoundingClientRect(),
    hasHeight: () => height !== null,
  };
}

/**
 * Wires the two right-hand panels together.
 *
 * The scenario panel keeps its natural height until dragged. The log then takes
 * whatever is left, so the default layout fills the column without either panel
 * having to know a number the other one owns.
 */
export function createPanelLayout({ scenarios, eventlog, margin = 18 }) {
  const maxWidth = () => Math.max(260, window.innerWidth / 2 - 27);
  let top;
  let bottom;

  top = createResizablePanel({
    ...scenarios,
    anchor: 'top',
    limits: () => ({
      maxWidth: maxWidth(),
      // Stop short of the log's top edge.
      maxHeight: Math.max(
        110,
        (bottom ? bottom.rect().top : window.innerHeight - margin) - margin - GAP
      ),
    }),
    onChange: () => sizeLog(),
  });

  bottom = createResizablePanel({
    ...eventlog,
    anchor: 'bottom',
    limits: () => ({
      maxWidth: maxWidth(),
      maxHeight: Math.max(110, window.innerHeight - margin - top.rect().bottom - GAP),
    }),
  });

  // With no dragged height of its own, the log fills the gap under the scenario
  // panel. Set explicitly rather than with CSS, because the scenario panel's
  // height is its content's and only the browser knows it.
  function sizeLog() {
    if (bottom.hasHeight()) return;
    const available = window.innerHeight - margin - top.rect().bottom - GAP;
    eventlog.wrap.style.height = `${Math.max(110, available)}px`;
  }

  function apply() {
    top.apply();
    bottom.apply();
    sizeLog();
  }

  window.addEventListener('resize', apply);
  apply();

  return { apply };
}
