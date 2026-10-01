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

export function applyPose(agent, poseName) {
  const pose = POSES[poseName] ?? POSES.standing;
  const j = agent.joints;
  j.hips.position.y = pose.hipY;
  j.thighL.rotation.x = pose.thigh;
  j.thighR.rotation.x = pose.thigh;
  j.shinL.rotation.x = pose.shin;
  j.shinR.rotation.x = pose.shin;
  j.torso.rotation.x = pose.torso;
  j.armUpperL.rotation.set(pose.armUpper, 0, pose.armSpread);
  j.armUpperR.rotation.set(pose.armUpper, 0, -pose.armSpread);
  j.armForeL.rotation.x = pose.armFore;
  j.armForeR.rotation.x = pose.armFore;
  agent.pose = poseName;
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

  if (spec.heightScale) root.scale.setScalar(spec.heightScale);

  const agent = { id: spec.id, spec, root, joints, label, pose: 'standing' };

  // Everything downstream identifies an agent by this, including Layer 6's
  // click-to-inspect, which will raycast and walk up to the nearest root.
  root.userData.agent = agent;

  applyPose(agent, 'standing');
  return agent;
}

export { P as PROPORTIONS };
