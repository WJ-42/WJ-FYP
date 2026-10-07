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

// A fold toggle for one panel. `after` runs once the panel has changed size, for
// layouts where the neighbouring panel has to take up the slack.
function wireToggle(button, panel, after = () => {}) {
  if (!button) return;
  const label = button.closest('.hud')?.querySelector('h2')?.textContent?.toLowerCase() ?? 'panel';
  const sync = () => {
    const folded = panel.isCollapsed();
    button.textContent = folded ? '+' : '–';
    button.setAttribute('aria-expanded', String(!folded));
    button.title = `${folded ? 'Show' : 'Hide'} ${label}`;
  };
  button.addEventListener('click', () => {
    panel.toggleCollapsed();
    sync();
    after();
  });
  sync();
}

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
  side = 'right', // which side edge is pinned: the width grip sits on the other one
  minWidth = 260,
  minHeight = 110,
  limits,
  storeKey,
  onChange = () => {},
}) {
  const stored = readStore(storeKey);
  let width = Number(stored.width) || null;
  let height = Number(stored.height) || null;
  // A collapsed panel shows only its header. Its remembered height is kept, so
  // expanding it again puts it back the size it was.
  let collapsed = Boolean(stored.collapsed);

  function apply() {
    const { maxWidth, maxHeight } = limits();
    wrap.style.width = width ? `${clamp(width, minWidth, maxWidth)}px` : '';
    wrap.style.height =
      height && !collapsed ? `${clamp(height, minHeight, maxHeight)}px` : '';
    wrap.classList.toggle('collapsed', collapsed);
    // CSS caps a panel that has no height of its own, so it cannot grow over
    // its neighbours; once it is sized by hand the clamp above does that job.
    wrap.classList.toggle('auto-size', !height && !collapsed);
    onChange();
  }

  function persist() {
    writeStore(storeKey, { width, height, collapsed });
  }

  function widthFrom(clientX) {
    // The wrapper is pinned to one side, so its width is the distance from the
    // pointer to that fixed edge.
    const rect = wrap.getBoundingClientRect();
    return side === 'right' ? rect.right - clientX : clientX - rect.left;
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
    toggleCollapsed() {
      collapsed = !collapsed;
      apply();
      persist();
      return collapsed;
    },
    isCollapsed: () => collapsed,
  };
}

/**
 * The key-hints box, bottom left.
 *
 * Same grips as the right-hand panels, mirrored: width from the right edge,
 * height from the top edge. It also folds down to just its header, for when the
 * window is tiled small and the hints are covering the scene. It is limited to
 * the room under the left rail, so it cannot grow up over the title panel.
 */
export function createControlsPanel({ wrap, toggle, gripX, gripY, gripCorner, storeKey, rail, margin = 18 }) {
  const panel = createResizablePanel({
    wrap,
    gripX,
    gripY,
    gripCorner,
    storeKey,
    anchor: 'bottom',
    side: 'left',
    minWidth: 200,
    minHeight: 70,
    limits: () => ({
      maxWidth: Math.max(200, window.innerWidth / 2 - 27),
      maxHeight: Math.max(
        70,
        window.innerHeight - margin - (rail ? rail.getBoundingClientRect().bottom : margin) - GAP
      ),
    }),
  });

  wireToggle(toggle, panel);

  window.addEventListener('resize', () => panel.apply());
  panel.apply();
  return panel;
}

/**
 * Wires the two right-hand panels together.
 *
 * The scenario panel keeps its natural height until dragged. The log then takes
 * whatever is left, so the default layout fills the column without either panel
 * having to know a number the other one owns.
 */
export function createPanelLayout({ scenarios, eventlog, margin = 18 }) {
  const { toggle: scenariosToggle, ...scenariosCfg } = scenarios;
  const { toggle: eventlogToggle, ...eventlogCfg } = eventlog;
  const maxWidth = () => Math.max(260, window.innerWidth / 2 - 27);
  let top;
  let bottom;

  top = createResizablePanel({
    ...scenariosCfg,
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
    ...eventlogCfg,
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
    // A folded log is just its header; its own apply() has already cleared the
    // height, so leave it alone.
    if (bottom.isCollapsed()) {
      eventlog.wrap.style.height = '';
      return;
    }
    if (bottom.hasHeight()) return;
    const available = window.innerHeight - margin - top.rect().bottom - GAP;
    eventlog.wrap.style.height = `${Math.max(110, available)}px`;
  }

  function apply() {
    top.apply();
    bottom.apply();
    sizeLog();
  }

  wireToggle(scenariosToggle, top, apply);
  wireToggle(eventlogToggle, bottom, apply);

  window.addEventListener('resize', apply);
  apply();

  return { apply };
}
