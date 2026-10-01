// Renderer, camera, lighting and camera controls.
//
// The camera is orthographic and held at a fixed elevation band, which is what
// gives the Sims-style "look down into the building" read: a literal 90-degree
// top-down view of 3D people would show nothing but the tops of their heads, so
// the view is tilted about 55 degrees off vertical instead, and the walls are
// kept low enough that nothing is ever hidden behind them.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PALETTE } from './palette.js';
import { LAYOUT } from './layout.js';

const VIEW = {
  size: 23.5, // world-metres visible vertically at zoom 1
  polar: 0.62, // radians off vertical (~35 deg from straight down)
  azimuth: 0.26, // radians; camera sits just south-east of the office
  radius: 80, // orthographic, so this only affects clipping and shadows
  target: new THREE.Vector3(0, 0.6, 0),
};

function placeCamera(camera, controls) {
  const { radius: r, polar: p, azimuth: a, target } = VIEW;
  camera.position.set(
    target.x + r * Math.sin(p) * Math.sin(a),
    target.y + r * Math.cos(p),
    target.z + r * Math.sin(p) * Math.cos(a)
  );
  controls.target.copy(target);
  camera.zoom = 1;
  camera.updateProjectionMatrix();
  controls.update();
}

export function createStage(canvas, opts = {}) {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    // Only needed for still captures: without it a one-shot render leaves the
    // drawing buffer undefined by the time a screenshot composites, which looks
    // exactly like a scene that failed to build.
    preserveDrawingBuffer: !!opts.preserveDrawingBuffer,
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.04;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(PALETTE.bg);
  scene.fog = new THREE.Fog(PALETTE.bg, 90, 190);

  const aspect = window.innerWidth / window.innerHeight;
  const camera = new THREE.OrthographicCamera(
    (-VIEW.size * aspect) / 2,
    (VIEW.size * aspect) / 2,
    VIEW.size / 2,
    -VIEW.size / 2,
    0.1,
    400
  );

  // --- Lighting -------------------------------------------------------------
  scene.add(new THREE.HemisphereLight(0xffffff, 0xb7bfb4, 0.62));

  const sun = new THREE.DirectionalLight(0xfff5e6, 1.3);
  sun.position.set(-26, 34, 20);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.left = -26;
  sun.shadow.camera.right = 26;
  sun.shadow.camera.top = 26;
  sun.shadow.camera.bottom = -26;
  sun.shadow.camera.near = 1;
  sun.shadow.camera.far = 110;
  sun.shadow.bias = -0.0005;
  sun.shadow.normalBias = 0.022;
  scene.add(sun);

  // Cool fill from the opposite side so shadowed faces keep their detail.
  const fill = new THREE.DirectionalLight(0xdce8f2, 0.34);
  fill.position.set(24, 16, -22);
  scene.add(fill);

  // --- Controls -------------------------------------------------------------
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.075;
  controls.screenSpacePanning = false;
  controls.minPolarAngle = 0.12; // never quite straight down
  controls.maxPolarAngle = 1.02; // never down to eye level
  controls.minZoom = 0.55;
  controls.maxZoom = 4.5;
  controls.mouseButtons = {
    LEFT: THREE.MOUSE.PAN,
    MIDDLE: THREE.MOUSE.DOLLY,
    RIGHT: THREE.MOUSE.ROTATE,
  };
  controls.touches = { ONE: THREE.TOUCH.PAN, TWO: THREE.TOUCH.DOLLY_ROTATE };
  placeCamera(camera, controls);

  // Keep the pan target over the building, so the office can't be lost offscreen.
  const panLimit = {
    minX: LAYOUT.bounds.minX - 4,
    maxX: LAYOUT.bounds.maxX + 4,
    minZ: LAYOUT.bounds.minZ - 4,
    maxZ: LAYOUT.bounds.maxZ + 4,
  };
  function clampTarget() {
    const t = controls.target;
    const before = t.clone();
    t.x = THREE.MathUtils.clamp(t.x, panLimit.minX, panLimit.maxX);
    t.z = THREE.MathUtils.clamp(t.z, panLimit.minZ, panLimit.maxZ);
    if (!before.equals(t)) camera.position.add(t.clone().sub(before));
  }

  function resize() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const a = w / h;
    camera.left = (-VIEW.size * a) / 2;
    camera.right = (VIEW.size * a) / 2;
    camera.top = VIEW.size / 2;
    camera.bottom = -VIEW.size / 2;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
  }
  window.addEventListener('resize', resize);
  resize();

  const resetView = () => placeCamera(camera, controls);

  // Point the camera at a spot on the floor at a given zoom. Used by the
  // ?focus=x,z&zoom=n params to inspect one part of the office closely, which
  // is the only practical way to check poses and choreography headlessly.
  function focusOn(x, z, zoom) {
    const dx = x - controls.target.x;
    const dz = z - controls.target.z;
    controls.target.x = x;
    controls.target.z = z;
    camera.position.x += dx;
    camera.position.z += dz;
    if (zoom) camera.zoom = THREE.MathUtils.clamp(zoom, controls.minZoom, controls.maxZoom);
    camera.updateProjectionMatrix();
    controls.update();
  }

  // --- Frame loop -----------------------------------------------------------
  const updaters = [];
  const clock = new THREE.Clock();

  function onFrame(fn) {
    updaters.push(fn);
  }

  function start() {
    renderer.setAnimationLoop(() => {
      const dt = Math.min(clock.getDelta(), 0.1); // clamp after a tab switch
      for (const fn of updaters) fn(dt, clock.elapsedTime);
      clampTarget();
      controls.update();
      renderer.render(scene, camera);
    });
  }

  // Draw exactly one frame and stop. A continuous animation loop never lets a
  // headless browser's page go idle, so `?still=1` screenshots would hang once
  // the scene got heavy enough that software-rendered frames were slow. This
  // keeps headless verification viable for every later layer.
  function renderOnce(advance = 0) {
    for (const fn of updaters) fn(advance, advance);
    clampTarget();
    controls.update();
    renderer.render(scene, camera);
  }

  return { renderer, scene, camera, controls, onFrame, start, renderOnce, resetView, focusOn };
}
