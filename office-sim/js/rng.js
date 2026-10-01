// A small seeded random number generator.
//
// The office needs randomness — which break an idle agent wanders off to, how
// long it stays, how a dropped document lands — or five people behave like one
// person. But `Math.random` makes a run unrepeatable, and this project checks
// almost everything by rendering a frame at a chosen moment and reading it. Two
// captures of the same scenario have to be the same picture, or a screenshot
// proves nothing.
//
// So the randomness is seeded and resettable: the same seed replays the same
// run, and restarting a scenario rewinds the stream rather than carrying on
// from wherever the last run left it.
//
// mulberry32: small, fast, and good enough for choosing a coffee machine.

export function createRng(seed = 1) {
  const initial = seed >>> 0;
  let state = initial;

  const next = () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };

  next.reset = () => {
    state = initial;
  };

  return next;
}
