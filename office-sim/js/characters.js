// The agent figures.
//
// Each agent is a small jointed rig rather than a single welded mesh. That is
// deliberate: every home station in the floor plan is a seated one, so a figure
// that cannot bend at the hip and knee would stand inside its own chair. The
// same joints are what Layer 3's walk cycle will drive, so the rig is built
// once here and posed from outside.
//
// Build convention matches the furniture: a figure at yaw 0 faces north (-Z).
// Limbs are modelled pointing straight down from their joint, so a rotation of
// zero is the standing pose and every pose is expressed as joint rotations.

import * as THREE from 'three';
import { PALETTE } from './palette.js';

// Body measurements in metres, for a 1.0-scale figure roughly 1.72m tall.
const P = {
  footY: 0.08, // ankle height
  shin: 0.42,
  thigh: 0.42,
  pelvisH: 0.14,
  torsoH: 0.42,
  torsoW: 0.36,
  torsoD: 0.22,
  shoulderDrop: 0.06, // below the top of the torso
  upperArm: 0.26,
  foreArm: 0.24,
  armR: 0.055,
  legR: 0.07,
  headR: 0.12,
  neck: 0.06,
};

P.hipY = P.footY + P.shin + P.thigh; // 0.92 standing
P.torsoBase = P.hipY + P.pelvisH * 0.5;
P.shoulderY = P.torsoH - P.shoulderDrop;

const mat = (color, opts = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness: 0.82, metalness: 0.02, ...opts });

// A limb segment: a capsule hanging downward from a pivot at the origin, so
// setting rotation on the pivot swings it the way a real joint would.
function segment(radius, length, material) {
  const pivot = new THREE.Group();
  const mesh = new THREE.Mesh(new THREE.CapsuleGeometry(radius, length - radius * 2, 4, 12), material);
  mesh.position.y = -length / 2;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  pivot.add(mesh);
  return { pivot, mesh };
}

function box(w, h, d, material, x = 0, y = 0, z = 0) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

// Name tag. Drawn to a canvas and shown as a sprite so it always faces the
// camera no matter how the view is rotated.
function makeLabel(text, tierColor) {
  const dpr = 2;
  const padX = 18 * dpr;
  const padY = 11 * dpr;
  const dot = 9 * dpr;
  const gap = 10 * dpr;
  const fontPx = 26 * dpr;

  const measure = document.createElement('canvas').getContext('2d');
  measure.font = `600 ${fontPx}px "JetBrains Mono", ui-monospace, monospace`;
  const textW = measure.measureText(text).width;

  const canvas = document.createElement('canvas');
  canvas.width = Math.ceil(padX * 2 + dot + gap + textW);
  canvas.height = Math.ceil(padY * 2 + fontPx);
  const ctx = canvas.getContext('2d');

  // Pill background
  const r = canvas.height / 2;
  ctx.beginPath();
  ctx.moveTo(r, 0);
  ctx.arcTo(canvas.width, 0, canvas.width, canvas.height, r);
  ctx.arcTo(canvas.width, canvas.height, 0, canvas.height, r);
  ctx.arcTo(0, canvas.height, 0, 0, r);
  ctx.arcTo(0, 0, canvas.width, 0, r);
  ctx.closePath();
  ctx.fillStyle = 'rgba(16, 19, 16, 0.86)';
  ctx.fill();

  // Tier dot, the same colour coding the 2D dashboard uses for role tiers
  ctx.beginPath();
  ctx.arc(padX + dot / 2, canvas.height / 2, dot / 2, 0, Math.PI * 2);
  ctx.fillStyle = `#${tierColor.toString(16).padStart(6, '0')}`;
  ctx.fill();

  ctx.font = measure.font;
  ctx.fillStyle = '#eef1ec';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, padX + dot + gap, canvas.height / 2 + 1);

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;

  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false })
  );
  const h = 0.26;
  sprite.scale.set((canvas.width / canvas.height) * h, h, 1);
  return sprite;
}

// A smaller pill than the name tag, used for the activity badge. Rebuilt from
// scratch whenever the text changes, which is on a state change rather than on
// a frame, so the cost does not matter.
function makeBadgeSprite(text, accentHex) {
  const dpr = 2;
  const fontPx = 19 * dpr;
  const padX = 13 * dpr;
  const padY = 8 * dpr;
  const bar = 5 * dpr;
  const gap = 8 * dpr;

  const measure = document.createElement('canvas').getContext('2d');
  measure.font = `700 ${fontPx}px "JetBrains Mono", ui-monospace, monospace`;
  const textW = measure.measureText(text).width;

  const canvas = document.createElement('canvas');
  canvas.width = Math.ceil(padX * 2 + bar + gap + textW);
  canvas.height = Math.ceil(padY * 2 + fontPx);
  const ctx = canvas.getContext('2d');

  const r = 7 * dpr;
  ctx.beginPath();
  ctx.moveTo(r, 0);
  ctx.arcTo(canvas.width, 0, canvas.width, canvas.height, r);
  ctx.arcTo(canvas.width, canvas.height, 0, canvas.height, r);
  ctx.arcTo(0, canvas.height, 0, 0, r);
  ctx.arcTo(0, 0, canvas.width, 0, r);
  ctx.closePath();
  ctx.fillStyle = 'rgba(12, 14, 12, 0.9)';
  ctx.fill();

  const accent = `#${accentHex.toString(16).padStart(6, '0')}`;
  ctx.fillStyle = accent;
  ctx.fillRect(padX, padY, bar, canvas.height - padY * 2);

  ctx.font = measure.font;
  ctx.fillStyle = '#eef1ec';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, padX + bar + gap, canvas.height / 2 + 1);

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;

  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false })
  );
  const h = 0.2;
  sprite.scale.set((canvas.width / canvas.height) * h, h, 1);
  return sprite;
}

/**
 * Sets the activity badge above an agent's name tag. Passing a null label
 * hides it, which is how an agent with nothing worth saying stays uncluttered.
 */
export function setAgentBadge(agent, text, accentHex) {
  const holder = agent.badgeHolder;
  if (agent.badge) {
    holder.remove(agent.badge);
    agent.badge.material.map.dispose();
    agent.badge.material.dispose();
    agent.badge = null;
  }
  agent.badgeText = text ?? null;
  if (!text) return;
  const sprite = makeBadgeSprite(text, accentHex);
  holder.add(sprite);
  agent.badge = sprite;
}

const hex = (n) => `#${n.toString(16).padStart(6, '0')}`;

function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

// A progress bar for the one thing a badge cannot show: how long an agent has
// been stuck.
//
// Drawn into a canvas and shown as a textured sprite, which is the same shape
// as the name tag because that is what demonstrably renders here. An earlier
// attempt built it from two plain untextured sprites and nothing appeared on
// screen at all, even with the fill width updating correctly underneath.
function makeTimerBar() {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 32;

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;

  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false })
  );
  const h = 0.075;
  sprite.scale.set((canvas.width / canvas.height) * h, h, 1);
  sprite.visible = false;
  sprite.userData = { canvas, tex, drawn: -1 };
  return sprite;
}

/**
 * Shows the stuck timer at `progress` (0..1), or hides it when passed null.
 * The bar turns from amber to red past three quarters, because a timer about
 * to expire should not read the same as one that has just started.
 */
export function setAgentTimer(agent, progress) {
  const sprite = agent.timerBar;
  if (progress == null) {
    sprite.visible = false;
    return;
  }

  const t = Math.max(0, Math.min(1, progress));
  sprite.visible = true;

  // Redrawn only when the value actually moves, rather than every frame.
  const ud = sprite.userData;
  if (Math.abs(t - ud.drawn) < 0.01) return;
  ud.drawn = t;

  const { canvas } = ud;
  const W = canvas.width;
  const H = canvas.height;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);

  roundRect(ctx, 0, 0, W, H, H / 2);
  ctx.fillStyle = 'rgba(12, 14, 12, 0.9)';
  ctx.fill();

  const pad = 5;
  const fw = (W - pad * 2) * t;
  if (fw > 1) {
    roundRect(ctx, pad, pad, fw, H - pad * 2, (H - pad * 2) / 2);
    ctx.fillStyle = hex(t > 0.75 ? PALETTE.status.alert : PALETTE.status.waiting);
    ctx.fill();
  }

  ud.tex.needsUpdate = true;
}

// The ticket as a physical object. Carrying it is what makes "Product dropped
// the spec at an engineer's desk" and "the CTO walked it back to the backlog"
// readable as events rather than as two people walking about.
function makeCarriedTicket() {
  const g = new THREE.Group();
  g.visible = false;
  const paper = new THREE.Mesh(
    new THREE.BoxGeometry(0.2, 0.012, 0.28),
    new THREE.MeshStandardMaterial({ color: PALETTE.ticketPaper, roughness: 0.9 })
  );
  paper.castShadow = true;
  g.add(paper);
  const edge = new THREE.Mesh(
    new THREE.BoxGeometry(0.205, 0.004, 0.285),
    new THREE.MeshStandardMaterial({ color: PALETTE.ticketEdge, roughness: 0.9 })
  );
  edge.position.y = -0.008;
  g.add(edge);
  return g;
}

export function setAgentCarrying(agent, carrying) {
  agent.carried.visible = !!carrying;
  agent.isCarrying = !!carrying;
}

export function setBadgesVisible(agents, visible) {
  for (const agent of agents) agent.badgeHolder.visible = visible;
}

// Poses are plain joint-rotation sets. Adding a new one (walking, presenting)
// means adding rotations here, not new geometry.
//
// Sign convention, which is easy to get backwards: a limb is modelled pointing
// straight down, so rotating it about X by a POSITIVE angle swings it toward
// -Z, which is the direction the figure faces. Forward is positive.
export const POSES = {
  standing: {
    hipY: P.hipY,
    thigh: 0,
    shin: 0,
    torso: 0,
    armUpper: 0.04,
    armFore: 0.12,
    armSpread: 0.07,
  },
  // Seated: hips drop to chair height, thighs swing forward to horizontal, and
  // the shins counter-rotate back to vertical so the feet reach the floor.
  seated: {
    hipY: 0.54,
    thigh: Math.PI / 2,
    shin: -Math.PI / 2 + 0.08,
    torso: 0.05,
    armUpper: 0.5,
    armFore: 0.5,
    armSpread: 0.1,
  },
};

// The poses above are symmetric, which a walk cycle is not: it needs the left
// and right limbs at opposite points of the same stride. So a pose is expanded
// into a flat per-joint set before being applied, and walking is that same set
// with a stride added to it rather than a separate code path.
function expand(pose) {
  return {
    hipY: pose.hipY,
    torso: pose.torso,
    armSpread: pose.armSpread,
    thighL: pose.thigh,
    thighR: pose.thigh,
    shinL: pose.shin,
    shinR: pose.shin,
    armUpperL: pose.armUpper,
    armUpperR: pose.armUpper,
    armForeL: pose.armFore,
    armForeR: pose.armFore,
  };
}

function setJoints(agent, f) {
  const j = agent.joints;
  j.hips.position.y = f.hipY;
  j.thighL.rotation.x = f.thighL;
  j.thighR.rotation.x = f.thighR;
  j.shinL.rotation.x = f.shinL;
  j.shinR.rotation.x = f.shinR;
  j.torso.rotation.x = f.torso;
  j.armUpperL.rotation.set(f.armUpperL, 0, f.armSpread);
  j.armUpperR.rotation.set(f.armUpperR, 0, -f.armSpread);
  j.armForeL.rotation.x = f.armForeL;
  j.armForeR.rotation.x = f.armForeR;
}

export function applyPose(agent, poseName) {
  setJoints(agent, expand(POSES[poseName] ?? POSES.standing));
  agent.pose = poseName;
}

/**
 * Blends between two named poses. Standing up and sitting down are the whole
 * reason the rig exists, and snapping between them reads as a glitch, so both
 * ends of a walk are a short interpolation rather than an assignment.
 */
export function applyPoseBlend(agent, fromName, toName, t) {
  const a = expand(POSES[fromName] ?? POSES.standing);
  const b = expand(POSES[toName] ?? POSES.standing);
  const k = Math.max(0, Math.min(1, t));
  // Eased, so the figure settles into the chair instead of arriving at
  // constant speed and stopping dead.
  const e = k * k * (3 - 2 * k);
  const out = {};
  for (const key of Object.keys(a)) out[key] = a[key] + (b[key] - a[key]) * e;
  setJoints(agent, out);
  agent.pose = e < 0.5 ? fromName : toName;
}

// Stride shape. `phase` advances with distance travelled rather than with time,
// so the feet cannot skate when the walking speed changes.
// The hip swing and the hip dip are not independent. A leg swung forward by
// `thigh` radians is geometrically shorter in Y than a vertical one, so unless
// the hips drop by the same amount at that moment the planted foot hangs in the
// air and the figure appears to skim the floor. For a leg of length `legY`:
//
//   dip = legY * (1 - cos(thigh))
//
// Changing one of these without the other is what makes a walk cycle look
// wrong in a way that is hard to name, so they are derived here rather than
// both being guessed.
const WALK_THIGH = 0.38; // peak hip swing
const LEG_Y = P.hipY; // hip to sole, standing

const WALK = {
  thigh: WALK_THIGH,
  knee: 0.62, // peak knee flex during the swing-through
  kneeBase: 0.08, // legs are never quite locked straight
  arm: 0.34,
  foreArm: 0.18,
  bob: LEG_Y * (1 - Math.cos(WALK_THIGH)),
  lean: 0.07,
};

// Metres of ground covered per half cycle, from the same geometry: the two feet
// end up 2 * legY * sin(thigh) apart at full stride. locomotion.js advances the
// phase by distance over this, which is what keeps the feet from skating.
export const WALK_STRIDE = 2 * LEG_Y * Math.sin(WALK_THIGH);

/**
 * Poses a figure mid-stride. `amount` fades the whole cycle in and out, which
 * is what stops the legs still swinging while an agent pivots on the spot or
 * comes to a halt; at 0 this is exactly the standing pose.
 */
export function applyWalkPose(agent, phase, amount = 1) {
  const base = expand(POSES.standing);
  const a = Math.max(0, Math.min(1, amount));
  const sL = Math.sin(phase);
  const sR = Math.sin(phase + Math.PI);

  const f = { ...base };
  f.thighL = base.thighL + a * WALK.thigh * sL;
  f.thighR = base.thighR + a * WALK.thigh * sR;

  // A knee only bends backwards, and it bends most just after the foot leaves
  // the floor, which is when that thigh is at its rearmost. Hence the negative
  // sign (see the convention note above) and the phase lead.
  const flex = (p) => WALK.kneeBase + WALK.knee * Math.max(0, -Math.sin(p - 0.5));
  f.shinL = base.shinL - a * flex(phase);
  f.shinR = base.shinR - a * flex(phase + Math.PI);

  // Arms counter-swing: the left arm comes forward as the left leg goes back.
  f.armUpperL = base.armUpperL - a * WALK.arm * sL;
  f.armUpperR = base.armUpperR - a * WALK.arm * sR;
  f.armForeL = base.armForeL + a * WALK.foreArm * Math.max(0, -sL);
  f.armForeR = base.armForeR + a * WALK.foreArm * Math.max(0, -sR);

  f.torso = base.torso + a * WALK.lean;
  // Hips dip twice per cycle, lowest when the legs are furthest apart.
  f.hipY = base.hipY - a * WALK.bob * (0.5 - 0.5 * Math.cos(2 * phase));

  setJoints(agent, f);
  agent.pose = a > 0.02 ? 'walking' : 'standing';
}

/**
 * Builds one agent figure.
 *
 * `spec` carries both identity (id, display name, tier) and appearance. The
 * shirt is always the role-tier colour, since that is the part that actually
 * carries meaning; everything else varies only so five figures do not read as
 * five copies of one person.
 */
export function makeAgent(spec) {
  const shirt = mat(spec.tierColor);
  const skin = mat(spec.skin, { roughness: 0.88 });
  const hair = mat(spec.hair, { roughness: 0.95 });
  const trousers = mat(spec.trousers, { roughness: 0.9 });
  const shoes = mat(0x2e3238, { roughness: 0.7 });

  const root = new THREE.Group();
  root.name = `agent:${spec.id}`;

  const hips = new THREE.Group();
  hips.position.y = P.hipY;
  root.add(hips);

  hips.add(box(P.torsoW * 0.92, P.pelvisH, P.torsoD, trousers, 0, P.pelvisH * 0.1, 0));

  // --- Torso, head ---
  const torso = new THREE.Group();
  torso.position.y = P.pelvisH * 0.5;
  hips.add(torso);

  torso.add(box(P.torsoW, P.torsoH, P.torsoD, shirt, 0, P.torsoH / 2, 0));
  // A slightly narrower block at the top reads as shoulders without a mesh.
  torso.add(box(P.torsoW * 1.04, 0.1, P.torsoD * 1.02, shirt, 0, P.torsoH - 0.05, 0));
  torso.add(box(0.1, P.neck + 0.04, 0.1, skin, 0, P.torsoH + P.neck / 2, 0));

  const head = new THREE.Group();
  head.position.y = P.torsoH + P.neck + P.headR * 0.86;
  torso.add(head);

  const skull = new THREE.Mesh(new THREE.SphereGeometry(P.headR, 20, 14), skin);
  skull.scale.set(1, 1.06, 0.95);
  skull.castShadow = true;
  head.add(skull);

  if (spec.hairStyle !== 'none') {
    const cap = new THREE.Mesh(
      new THREE.SphereGeometry(P.headR * 1.04, 18, 12, 0, Math.PI * 2, 0, Math.PI * 0.56),
      hair
    );
    cap.scale.set(1, 1.08, 0.97);
    cap.position.y = 0.006;
    cap.castShadow = true;
    head.add(cap);

    if (spec.hairStyle === 'bun') {
      const bun = new THREE.Mesh(new THREE.SphereGeometry(P.headR * 0.46, 12, 10), hair);
      bun.position.set(0, P.headR * 0.42, P.headR * 0.88);
      bun.castShadow = true;
      head.add(bun);
    }
  }

  // --- Arms, hung from the shoulders ---
  const joints = { hips, torso, head };
  for (const side of ['L', 'R']) {
    const dir = side === 'L' ? -1 : 1;
    const upper = segment(P.armR, P.upperArm, shirt);
    upper.pivot.position.set(dir * (P.torsoW / 2 + P.armR * 0.5), P.shoulderY, 0);
    torso.add(upper.pivot);

    const fore = segment(P.armR * 0.92, P.foreArm, skin);
    fore.pivot.position.y = -P.upperArm;
    upper.pivot.add(fore.pivot);

    joints[`armUpper${side}`] = upper.pivot;
    joints[`armFore${side}`] = fore.pivot;
  }

  // --- Legs, hung from the hips ---
  for (const side of ['L', 'R']) {
    const dir = side === 'L' ? -1 : 1;
    const thigh = segment(P.legR, P.thigh, trousers);
    thigh.pivot.position.set(dir * P.torsoW * 0.22, 0, 0);
    hips.add(thigh.pivot);

    const shin = segment(P.legR * 0.86, P.shin, trousers);
    shin.pivot.position.y = -P.thigh;
    thigh.pivot.add(shin.pivot);

    const foot = box(P.legR * 2, 0.07, 0.2, shoes, 0, -P.shin - 0.02, -0.05);
    shin.pivot.add(foot);

    joints[`thigh${side}`] = thigh.pivot;
    joints[`shin${side}`] = shin.pivot;
  }

  // Parented to the head, not the root: a fixed height off the floor would
  // leave the tag stranded above a seated figure, and would drift during the
  // walk cycle later.
  const label = makeLabel(spec.label, spec.tierColor);
  label.position.y = P.headR + 0.3;
  head.add(label);

  // Layer 4's readouts stack upward from the name tag: what the agent is doing,
  // and above that the stuck timer when one is running. Both hang off the head
  // for the same reason the name tag does — a fixed height off the floor would
  // strand them above a seated figure.
  // The name tag is 0.26 tall and the badge 0.2, so their half-heights alone
  // come to 0.23: at a 0.24 gap they touched and the badge clipped the name.
  const badgeHolder = new THREE.Group();
  badgeHolder.position.y = P.headR + 0.62;
  head.add(badgeHolder);

  const timerBar = makeTimerBar();
  timerBar.position.y = P.headR + 0.8;
  head.add(timerBar);

  // Carried in the right hand, at the end of the forearm, so it follows the
  // arm through the walk cycle instead of floating alongside the body.
  const carried = makeCarriedTicket();
  carried.position.set(0, -P.foreArm - 0.03, -0.07);
  carried.rotation.x = 0.25;
  joints.armForeR.add(carried);

  if (spec.heightScale) root.scale.setScalar(spec.heightScale);

  const agent = {
    id: spec.id,
    spec,
    root,
    joints,
    label,
    badgeHolder,
    badge: null,
    badgeText: null,
    timerBar,
    carried,
    isCarrying: false,
    pose: 'standing',
  };

  // Everything downstream identifies an agent by this, including Layer 6's
  // click-to-inspect, which will raycast and walk up to the nearest root.
  root.userData.agent = agent;

  applyPose(agent, 'standing');
  return agent;
}
