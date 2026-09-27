// Mussel viewer: a Streamlit custom component rendering the mussel with three.js.
//
// The mesh is uploaded to the GPU once. Rotating the mussel or changing its
// burial depth only changes the mussel node's quaternion/position, so every
// interaction is a single cheap redraw with no Python round trip. The pose is
// sent back to Streamlit only when an interaction ends (pointer up / slider
// release), so dragging never triggers a Streamlit rerun.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

// ---------------------------------------------------------------------------
// Streamlit component protocol (streamlit-component-lib, minus React).
// ---------------------------------------------------------------------------
const Streamlit = {
  send(type, data = {}) {
    window.parent.postMessage({ isStreamlitMessage: true, type, ...data }, "*");
  },
  ready() { this.send("streamlit:componentReady", { apiVersion: 1 }); },
  setFrameHeight(height) { this.send("streamlit:setFrameHeight", { height }); },
  setComponentValue(value) { this.send("streamlit:setComponentValue", { value, dataType: "json" }); },
};

// Camera distance, in multiples of the mussel's bounding radius, for
// camera.distance_scale = 1.
const DEFAULT_DISTANCE_RADII = 6.8;
const CAMERA_FOV_DEG = 35;
// Degrees of rotation per pixel of drag, relative to the canvas height.
const DRAG_DEGREES_PER_HEIGHT = 180;
const GIMBAL_WARN_DEG = 85;
const SEE_THROUGH_OPACITY = 0.45;

const DEG = Math.PI / 180;
const $ = (id) => document.getElementById(id);

const state = {
  config: null,
  initialized: false,
  quaternion: new THREE.Quaternion(),
  height: 0, // 0 = resting on the substrate, -1 = fully buried
  hull: null, // Float32Array of xyz, mussel frame
  zRange: 0,
};

// ---------------------------------------------------------------------------
// Scene
// ---------------------------------------------------------------------------
const canvas = $("canvas");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.5;

// Z-up world, matching the conversion in mussel_loader.py.
const camera = new THREE.PerspectiveCamera(CAMERA_FOV_DEG, 1, 0.01, 100);
camera.up.set(0, 0, 1);

scene.add(new THREE.HemisphereLight(0xffffff, 0x8a7a55, 1.0));
const sun = new THREE.DirectionalLight(0xffffff, 1.8);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0005;
scene.add(sun, sun.target);

// Substrate: fixed forever at z = 0. Only the mussel moves.
const substrateMaterial = new THREE.MeshStandardMaterial({ roughness: 1, metalness: 0 });
const substrate = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), substrateMaterial);
substrate.receiveShadow = true;
scene.add(substrate);

// The mussel node: rotation about its own rest-pose center (the GLB origin),
// translated only along z to rest on / sink into the substrate.
const mussel = new THREE.Group();
scene.add(mussel);
// Body-frame axes (x red, y green, z blue), drawn on top of the shell.
const axes = new THREE.AxesHelper(1);
axes.material.depthTest = false;
axes.renderOrder = 1;
axes.visible = false;
mussel.add(axes);

let renderQueued = false;
function requestRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    renderer.render(scene, camera);
  });
}

function resize() {
  const { clientWidth: w, clientHeight: h } = canvas.parentElement;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  requestRender();
}
new ResizeObserver(resize).observe($("view"));

// ---------------------------------------------------------------------------
// Pose
// ---------------------------------------------------------------------------
const _v = new THREE.Vector3();

/** Settle the mussel so its lowest point sits at z = 0, then sink it by
 *  |height| of its current vertical extent. Same semantics as the old
 *  geometry.rotate_and_rest + plane_z_from_height, but moving the mussel. */
function applyPose() {
  mussel.quaternion.copy(state.quaternion);
  if (state.hull) {
    // Only the z row of the rotation matters: z' = r20 x + r21 y + r22 z.
    const { x, y, z, w } = state.quaternion;
    const a = 2 * (x * z - w * y);
    const b = 2 * (y * z + w * x);
    const c = 1 - 2 * (x * x + y * y);
    const h = state.hull;
    let zmin = Infinity;
    let zmax = -Infinity;
    for (let i = 0; i < h.length; i += 3) {
      const z = a * h[i] + b * h[i + 1] + c * h[i + 2];
      if (z < zmin) zmin = z;
      if (z > zmax) zmax = z;
    }
    state.zRange = zmax - zmin;
    mussel.position.set(0, 0, -zmin + state.height * state.zRange);
  }
  updateReadout();
  requestRender();
}

function eulerDeg() {
  // three.js order "ZYX" gives R = Rz(yaw) · Ry(pitch) · Rx(roll), the same as
  // scipy Rotation.from_euler("ZYX", [yaw, pitch, roll]).
  const e = new THREE.Euler().setFromQuaternion(state.quaternion, "ZYX");
  return { yaw: e.z / DEG, pitch: e.y / DEG, roll: e.x / DEG };
}

function setFromEuler(yaw, pitch, roll) {
  state.quaternion.setFromEuler(new THREE.Euler(roll * DEG, pitch * DEG, yaw * DEG, "ZYX"));
  applyPose();
}

function currentPose() {
  const { yaw, pitch, roll } = eulerDeg();
  const q = state.quaternion;
  const round = (x, n) => Number(x.toFixed(n));
  return {
    yaw: round(yaw, 3),
    pitch: round(pitch, 3),
    roll: round(roll, 3),
    quaternion: { w: round(q.w, 6), x: round(q.x, 6), y: round(q.y, 6), z: round(q.z, 6) },
    height: round(state.height, 4),
  };
}

function commitPose() {
  Streamlit.setComponentValue(currentPose());
}

// ---------------------------------------------------------------------------
// Controls panel
// ---------------------------------------------------------------------------
const controls = {};
for (const name of ["yaw", "pitch", "roll", "height"]) {
  const range = $(`${name}-range`);
  const num = $(`${name}-num`);
  controls[name] = { range, num };

  const onInput = (src) => {
    const value = Number(src.value);
    if (!Number.isFinite(value)) return;
    const clamped = Math.min(Number(range.max), Math.max(Number(range.min), value));
    range.value = clamped;
    if (src === range) num.value = clamped;
    if (name === "height") {
      state.height = clamped;
      applyPose();
    } else {
      setFromEuler(Number(controls.yaw.range.value), Number(controls.pitch.range.value), Number(controls.roll.range.value));
    }
  };
  range.addEventListener("input", () => onInput(range));
  num.addEventListener("input", () => onInput(num));
  range.addEventListener("change", commitPose);
  num.addEventListener("change", () => { num.value = range.value; commitPose(); });
}

function syncSliders() {
  const angles = eulerDeg();
  for (const name of ["yaw", "pitch", "roll"]) {
    const v = Math.round(angles[name] * 2) / 2;
    controls[name].range.value = v;
    if (document.activeElement !== controls[name].num) controls[name].num.value = v.toFixed(1);
  }
  controls.height.range.value = state.height;
  controls.height.num.value = state.height.toFixed(2);
}

function updateReadout() {
  const { yaw, pitch, roll } = eulerDeg();
  const q = state.quaternion;
  $("r-yaw").textContent = `${yaw.toFixed(1)}°`;
  $("r-pitch").textContent = `${pitch.toFixed(1)}°`;
  $("r-roll").textContent = `${roll.toFixed(1)}°`;
  const f = (v) => (v < 0 ? "" : " ") + v.toFixed(3);
  $("r-quat").textContent = `w ${f(q.w)}  x ${f(q.x)}\ny ${f(q.y)}  z ${f(q.z)}`;
  $("r-height").textContent = state.height.toFixed(2);
  $("r-burial").textContent = `${Math.round(Math.abs(state.height) * 100)}%`;
  $("gimbal").hidden = Math.abs(pitch) < GIMBAL_WARN_DEG;
}

$("reset").addEventListener("click", () => {
  state.quaternion.identity();
  state.height = 0;
  applyPose();
  syncSliders();
  commitPose();
});

$("opt-seethrough").addEventListener("change", (ev) => {
  const on = ev.target.checked;
  substrateMaterial.transparent = on;
  substrateMaterial.opacity = on ? SEE_THROUGH_OPACITY : 1;
  substrateMaterial.depthWrite = !on;
  substrateMaterial.needsUpdate = true;
  requestRender();
});

$("opt-axes").addEventListener("change", (ev) => {
  axes.visible = ev.target.checked;
  requestRender();
});

// ---------------------------------------------------------------------------
// Trackball drag: rotates the mussel (never the camera) about the view-space
// axis perpendicular to the drag direction.
// ---------------------------------------------------------------------------
let drag = null;
canvas.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 0) return;
  drag = { x: ev.clientX, y: ev.clientY, moved: false };
  canvas.setPointerCapture(ev.pointerId);
  canvas.classList.add("dragging");
  $("hint").hidden = true;
});

canvas.addEventListener("pointermove", (ev) => {
  if (!drag) return;
  const dx = ev.clientX - drag.x;
  const dy = ev.clientY - drag.y;
  drag.x = ev.clientX;
  drag.y = ev.clientY;
  const len = Math.hypot(dx, dy);
  if (!len) return;
  drag.moved = true;

  const angle = (len / canvas.clientHeight) * DRAG_DEGREES_PER_HEIGHT * DEG;
  // Screen y points down: dragging right spins about the camera's up axis,
  // dragging down tips the top toward the viewer (about the camera's right axis).
  const axis = _v.set(dy / len, dx / len, 0).applyQuaternion(camera.quaternion);
  state.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(axis, angle)).normalize();
  applyPose();
  syncSliders();
});

function endDrag(ev) {
  if (!drag) return;
  const moved = drag.moved;
  drag = null;
  canvas.classList.remove("dragging");
  if (canvas.hasPointerCapture(ev.pointerId)) canvas.releasePointerCapture(ev.pointerId);
  if (moved) commitPose();
}
canvas.addEventListener("pointerup", endDrag);
canvas.addEventListener("pointercancel", endDrag);

// ---------------------------------------------------------------------------
// Config from Python (render_config.json) and model loading
// ---------------------------------------------------------------------------
function applyConfig(config, radius) {
  const cam = config.camera || {};
  const elevation = (cam.elevation_deg ?? 25) * DEG;
  const azimuth = (cam.azimuth_deg ?? 45) * DEG;
  const distance = DEFAULT_DISTANCE_RADII * radius * (cam.distance_scale ?? 1);
  const target = new THREE.Vector3(...(cam.target || [0, 0, 0]));
  camera.position.set(
    target.x + distance * Math.cos(elevation) * Math.cos(azimuth),
    target.y + distance * Math.cos(elevation) * Math.sin(azimuth),
    target.z + distance * Math.sin(elevation),
  );
  camera.near = distance / 100;
  camera.far = distance * 10;
  camera.lookAt(target);
  camera.updateProjectionMatrix();

  const size = 2 * (config.substrate_size_scale ?? 2.2) * radius;
  substrate.scale.set(size, size, 1);
  substrateMaterial.color.set(config.substrate_color || "#C2B280");
  const background = config.background_color || "#FFFFFF";
  scene.background = new THREE.Color(background);
  document.documentElement.style.setProperty("--bg", background);

  // Light from high above, slightly toward the camera, so the shadow is visible.
  sun.position.set(radius * 1.5 * Math.cos(azimuth + 0.6), radius * 1.5 * Math.sin(azimuth + 0.6), radius * 4);
  const sc = sun.shadow.camera;
  sc.left = sc.bottom = -radius * 1.6;
  sc.right = sc.top = radius * 1.6;
  sc.near = radius * 0.5;
  sc.far = radius * 10;
  sc.updateProjectionMatrix();

  axes.scale.setScalar(radius * 0.8);
  requestRender();
}

async function init(args) {
  const version = encodeURIComponent(args.asset_version ?? "");
  const status = $("status");
  try {
    const meta = await (await fetch(`assets/mussel_meta.json?v=${version}`)).json();
    state.hull = new Float32Array(meta.hull.flat());
    state.radius = meta.bounding_radius;
    applyConfig(state.config, state.radius);

    const gltf = await new GLTFLoader().loadAsync(`assets/mussel.glb?v=${version}`, (ev) => {
      if (ev.total) status.textContent = `Loading model… ${Math.round((100 * ev.loaded) / ev.total)}%`;
    });
    gltf.scene.traverse((obj) => {
      if (obj.isMesh) obj.castShadow = true;
    });
    mussel.add(gltf.scene);

    const pose = args.initial_pose || {};
    state.height = Math.min(0, Math.max(-1, pose.height ?? 0));
    const q = pose.quaternion;
    if (q) state.quaternion.set(q.x, q.y, q.z, q.w).normalize();
    else setFromEuler(pose.yaw ?? 0, pose.pitch ?? 0, pose.roll ?? 0);
    applyPose();
    syncSliders();
    status.hidden = true;
  } catch (err) {
    console.error(err);
    status.textContent = `Could not load the mussel model: ${err.message || err}`;
    status.classList.add("error");
  }
}

window.addEventListener("message", (event) => {
  if (event.data?.type !== "streamlit:render") return;
  const args = event.data.args || {};
  document.documentElement.style.setProperty("--viewer-height", `${args.height ?? 640}px`);
  state.config = args.config || {};
  if (!state.initialized) {
    state.initialized = true;
    init(args);
  } else if (state.radius) {
    applyConfig(state.config, state.radius);
  }
  Streamlit.setFrameHeight(document.body.scrollHeight);
});

new ResizeObserver(() => Streamlit.setFrameHeight(document.body.scrollHeight)).observe(document.body);
Streamlit.ready();
