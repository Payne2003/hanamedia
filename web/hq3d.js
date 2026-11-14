// Phòng điều khiển 3D (Three.js / WebGL): Master Agent trên bục giữa, 3 robot agent ngồi bàn, quả địa cầu hologram,
// dây sáng nối về Master. Dữ liệu tiến độ do app.js đưa vào qua update(); chữ (nhãn, danh sách việc) là HTML đặt theo
// vị trí 3D chiếu ra màn hình để luôn sắc nét.
import * as THREE from './three.module.min.js';

const AGENTS = {
  conductor: {color: 0x5ab8ff, pos: [0, 0, -1.6], master: true},
  analyst: {color: 0xffd166, pos: [-4.3, 0, 1.4], rot: 0.55},
  writer: {color: 0xb28cff, pos: [0, 0, 2.7], rot: 0},
  studio: {color: 0x4fe3a8, pos: [4.3, 0, 1.4], rot: -0.55},
};

let renderer, scene, camera, canvas, host, labels = {}, raf = 0, clock, running = false;
const view = {theta: 0, phi: 1.12, radius: 13.5, target: new THREE.Vector3(0, 1.15, .4), auto: true, drag: null, t: 0};
const parts = {robots: {}, screens: {}, links: {}, rings: {}, lights: {}, globe: null};
let state = {};

function canvasTexture(width, height, draw) {
  const c = document.createElement('canvas');
  c.width = width; c.height = height;
  draw(c.getContext('2d'), width, height);
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

function windowTexture(seed) {
  return canvasTexture(256, 384, (g, w, h) => {
    const sky = g.createLinearGradient(0, 0, 0, h);
    sky.addColorStop(0, '#0c1a3f'); sky.addColorStop(.35, '#3c62a6'); sky.addColorStop(.62, '#f0a774');
    sky.addColorStop(.7, '#f7c99c'); sky.addColorStop(.71, '#36507e'); sky.addColorStop(1, '#14223f');
    g.fillStyle = sky; g.fillRect(0, 0, w, h);
    g.fillStyle = '#16213d';
    const base = h * .705;
    for (let i = 0; i < 3; i++) {  // cần cẩu cảng (bóng tối)
      const x = 30 + ((seed * 67 + i * 83) % 190);
      g.fillRect(x, base - 120, 6, 120); g.fillRect(x - 30, base - 120, 70, 6); g.fillRect(x + 30, base - 112, 4, 40);
    }
    for (let i = 0; i < 2; i++) {  // tàu
      const x = (seed * 41 + i * 120) % 200;
      g.fillRect(x, base - 14, 70, 14); g.fillRect(x + 8, base - 30, 30, 16);
    }
    g.fillStyle = '#ffffff22';
    for (let y = base + 10; y < h; y += 14) g.fillRect(0, y, w, 2);
  });
}

function glowMaterial(color, opacity = 1) {
  return new THREE.MeshBasicMaterial({color, transparent: opacity < 1, opacity, blending: THREE.AdditiveBlending, depthWrite: false});
}

function buildRoom() {
  const floor = new THREE.Mesh(new THREE.CircleGeometry(16, 96),
    new THREE.MeshStandardMaterial({color: 0x0b1a3d, metalness: .7, roughness: .32}));
  floor.rotation.x = -Math.PI / 2; scene.add(floor);
  for (const [r, w, c, o] of [[2.2, .05, 0x7fd6ff, .9], [3.3, .04, 0x4fb7ff, .6], [6.3, .05, 0x7fd6ff, .55], [9.5, .06, 0x9a7bff, .4], [12.5, .05, 0x4fb7ff, .3]]) {
    const ring = new THREE.Mesh(new THREE.RingGeometry(r - w, r + w, 128), glowMaterial(c, o));
    ring.rotation.x = -Math.PI / 2; ring.position.y = .01; scene.add(ring);
  }
  // tường cong + cửa sổ nhìn ra cảng lúc hoàng hôn
  const wall = new THREE.Mesh(new THREE.CylinderGeometry(15, 15, 8, 96, 1, true, Math.PI * .62, Math.PI * .76),
    new THREE.MeshStandardMaterial({color: 0x0a1736, metalness: .5, roughness: .55, side: THREE.BackSide}));
  wall.position.y = 4; scene.add(wall);
  for (let i = 0; i < 9; i++) {
    const a = Math.PI * (.66 + i * .085), x = Math.sin(a) * 14.8, z = Math.cos(a) * 14.8;
    const win = new THREE.Mesh(new THREE.PlaneGeometry(2.6, 3.9), new THREE.MeshBasicMaterial({map: windowTexture(i + 1)}));
    win.position.set(x, 3.2, z); win.lookAt(0, 3.2, 0); scene.add(win);
    const frame = new THREE.Mesh(new THREE.BoxGeometry(.18, 4.4, .25), new THREE.MeshStandardMaterial({color: 0x1a2a4d, metalness: .8, roughness: .3}));
    const fa = a + .0425; frame.position.set(Math.sin(fa) * 14.7, 3.2, Math.cos(fa) * 14.7); frame.lookAt(0, 3.2, 0); scene.add(frame);
  }
  // trần: vòng đèn
  for (const [r, y, c] of [[4.5, 6.6, 0x7fd6ff], [7.5, 7.1, 0x7fd6ff], [11, 7.6, 0xffb36b]]) {
    const torus = new THREE.Mesh(new THREE.TorusGeometry(r, .07, 12, 160), glowMaterial(c, .95));
    torus.rotation.x = Math.PI / 2; torus.position.y = y; scene.add(torus);
  }
  const ceiling = new THREE.Mesh(new THREE.CircleGeometry(16, 64), new THREE.MeshStandardMaterial({color: 0x050b1f, side: THREE.DoubleSide}));
  ceiling.rotation.x = Math.PI / 2; ceiling.position.y = 8; scene.add(ceiling);
  // chậu cây
  for (const x of [-8.5, 8.5]) {
    const pot = new THREE.Mesh(new THREE.CylinderGeometry(.45, .35, .8, 24), new THREE.MeshStandardMaterial({color: 0xdfe6f2, roughness: .4}));
    pot.position.set(x, .4, -4.5); scene.add(pot);
    const leaves = new THREE.Mesh(new THREE.IcosahedronGeometry(.75, 1), new THREE.MeshStandardMaterial({color: 0x2f9e5f, roughness: .8, flatShading: true}));
    leaves.position.set(x, 1.35, -4.5); leaves.scale.set(1, 1.3, 1); scene.add(leaves);
  }
}

function buildGlobe() {
  const group = new THREE.Group();
  group.position.set(0, 3.9, -4.3);
  group.add(new THREE.Mesh(new THREE.SphereGeometry(2.3, 48, 32),
    new THREE.MeshBasicMaterial({color: 0x1d5fd0, transparent: true, opacity: .16, blending: THREE.AdditiveBlending, depthWrite: false})));
  const wire = new THREE.Mesh(new THREE.SphereGeometry(2.32, 24, 16),
    new THREE.MeshBasicMaterial({color: 0x7fd6ff, wireframe: true, transparent: true, opacity: .32, blending: THREE.AdditiveBlending, depthWrite: false}));
  group.add(wire);
  const points = [];
  for (let i = 0; i < 420; i++) {
    const u = Math.random() * 2 - 1, a = Math.random() * Math.PI * 2, r = 2.34;
    points.push(Math.sqrt(1 - u * u) * Math.cos(a) * r, u * r, Math.sqrt(1 - u * u) * Math.sin(a) * r);
  }
  const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  group.add(new THREE.Points(geo, new THREE.PointsMaterial({color: 0xc8f0ff, size: .05, transparent: true, opacity: .9, blending: THREE.AdditiveBlending, depthWrite: false})));
  const orbit = new THREE.Mesh(new THREE.TorusGeometry(2.9, .015, 8, 160), glowMaterial(0x7fd6ff, .7));
  orbit.rotation.x = Math.PI / 2.3; group.add(orbit);
  scene.add(group);
  parts.globe = group;
}

function robot(color, master) {
  const white = new THREE.MeshStandardMaterial({color: 0xf2f6fd, metalness: .15, roughness: .28});
  const grey = new THREE.MeshStandardMaterial({color: 0xcdd8ea, metalness: .3, roughness: .35});
  const dark = new THREE.MeshStandardMaterial({color: 0x081126, metalness: .6, roughness: .15});
  const glow = new THREE.MeshBasicMaterial({color});
  const root = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(.36, .42, 8, 24), white);
  body.position.y = .95; root.add(body);
  const chest = new THREE.Mesh(new THREE.CircleGeometry(.12, 24), glow);
  chest.position.set(0, 1.0, .355); root.add(chest);
  const head = new THREE.Group(); head.position.y = 1.72; root.add(head);
  const skull = new THREE.Mesh(new THREE.SphereGeometry(.42, 32, 24), white); skull.scale.set(1.12, .98, 1); head.add(skull);
  // kính mặt: một dải cầu ở phía trước (phi = π/2 là hướng +z)
  const visor = new THREE.Mesh(new THREE.SphereGeometry(.44, 32, 16, Math.PI * .16, Math.PI * .68, Math.PI * .32, Math.PI * .34), dark);
  visor.scale.set(1.1, .98, 1.02); head.add(visor);
  const eyes = new THREE.Group(); head.add(eyes);
  for (const x of [-.13, .13]) {
    const eye = new THREE.Mesh(new THREE.SphereGeometry(.065, 16, 12), glow);
    eye.scale.set(1, 1.25, .5); eye.position.set(x, .03, .455); eyes.add(eye);
  }
  for (const x of [-.47, .47]) {
    const ear = new THREE.Mesh(new THREE.CylinderGeometry(.16, .16, .1, 24), grey);
    ear.rotation.z = Math.PI / 2; ear.position.x = x; head.add(ear);
    const core = new THREE.Mesh(new THREE.CylinderGeometry(.08, .08, .11, 20), glow);
    core.rotation.z = Math.PI / 2; core.position.x = x * 1.02; head.add(core);
  }
  const band = new THREE.Mesh(new THREE.TorusGeometry(.47, .035, 8, 32, Math.PI), grey);
  band.position.y = .02; head.add(band);
  const arms = [];
  for (const side of [-1, 1]) {
    const pivot = new THREE.Group(); pivot.position.set(side * .45, 1.22, 0); root.add(pivot);
    const arm = new THREE.Mesh(new THREE.CapsuleGeometry(.1, .38, 6, 12), grey);
    arm.position.set(0, -.25, .05); pivot.add(arm);
    pivot.rotation.x = -.9;  // tay đặt lên bàn phím
    arms.push(pivot);
  }
  if (master) {
    const gold = new THREE.MeshStandardMaterial({color: 0xffcf3f, metalness: .9, roughness: .25, emissive: 0x553300});
    const crown = new THREE.Group(); crown.position.y = .42; head.add(crown);
    crown.add(new THREE.Mesh(new THREE.CylinderGeometry(.26, .28, .12, 24, 1, true), gold));
    for (let i = 0; i < 5; i++) {
      const a = i / 5 * Math.PI * 2, spike = new THREE.Mesh(new THREE.ConeGeometry(.06, .2, 8), gold);
      spike.position.set(Math.sin(a) * .26, .15, Math.cos(a) * .26); crown.add(spike);
      const gem = new THREE.Mesh(new THREE.SphereGeometry(.035, 10, 8), new THREE.MeshBasicMaterial({color: i % 2 ? 0xff5d73 : 0x5ab8ff}));
      gem.position.set(Math.sin(a) * .28, .02, Math.cos(a) * .28); crown.add(gem);
    }
    root.userData.crown = crown;
  }
  root.userData = {...root.userData, head, eyes, arms, glow, chest, baseColor: new THREE.Color(color)};
  return root;
}

function screenTexture(color, title, lines) {
  return canvasTexture(512, 300, (g, w, h) => {
    g.clearRect(0, 0, w, h);
    const hex = '#' + new THREE.Color(color).getHexString();
    g.fillStyle = 'rgba(25,80,200,0.30)'; g.fillRect(0, 0, w, h);
    g.strokeStyle = hex; g.lineWidth = 6; g.strokeRect(3, 3, w - 6, h - 6);
    g.fillStyle = '#9fd8ff'; g.font = 'bold 26px Arial'; g.fillText(title, 22, 42);
    g.fillStyle = hex; g.fillRect(22, 54, 120, 4);
    if (!lines.length) {
      g.fillStyle = '#b8c9ea'; g.font = '28px Arial'; g.fillText('Đang chờ việc', 22, 150);
      for (let i = 0; i < 6; i++) { g.fillStyle = 'rgba(160,200,255,0.25)'; g.fillRect(22 + i * 76, 200 + (i % 3) * 10, 56, 60 - (i % 3) * 10); }
      return;
    }
    lines.slice(0, 4).forEach((l, i) => {
      const y = 100 + i * 52;
      g.fillStyle = '#ffffff'; g.font = '26px Arial'; g.fillText(l.name.slice(0, 18), 22, y);
      g.fillStyle = 'rgba(255,255,255,0.15)'; g.fillRect(270, y - 20, 160, 18);
      g.fillStyle = hex; g.fillRect(270, y - 20, 1.6 * l.pct, 18);
      g.fillText(l.pct + '%', 440, y);
    });
  });
}

function holoPanel(width, height, color) {
  const material = new THREE.MeshBasicMaterial({transparent: true, opacity: .92, side: THREE.DoubleSide, depthWrite: false,
    blending: THREE.AdditiveBlending, map: screenTexture(color, 'LIVE', [])});
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(width, height), material);
  const edge = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry), new THREE.LineBasicMaterial({color, transparent: true, opacity: .9}));
  mesh.add(edge);
  return mesh;
}

function buildAgents() {
  for (const [id, a] of Object.entries(AGENTS)) {
    const group = new THREE.Group();
    group.position.set(...a.pos); group.rotation.y = a.rot || 0; scene.add(group);
    const light = new THREE.PointLight(a.color, 6, 6, 1.6); light.position.set(0, 2.4, .8); group.add(light);
    parts.lights[id] = light;
    const ring = new THREE.Mesh(new THREE.RingGeometry(1.25, 1.36, 64), glowMaterial(a.color, .7));
    ring.rotation.x = -Math.PI / 2; ring.position.y = .02; group.add(ring); parts.rings[id] = ring;
    const bot = robot(a.color, a.master); group.add(bot); parts.robots[id] = bot;
    if (a.master) {
      const metal = new THREE.MeshStandardMaterial({color: 0x173b82, metalness: .8, roughness: .3});
      const dais1 = new THREE.Mesh(new THREE.CylinderGeometry(1.7, 1.9, .35, 64), metal); dais1.position.y = .18; group.add(dais1);
      const dais2 = new THREE.Mesh(new THREE.CylinderGeometry(1.2, 1.35, .3, 64), metal); dais2.position.y = .5; group.add(dais2);
      for (const [r, y] of [[1.9, .36], [1.36, .66]]) {
        const rim = new THREE.Mesh(new THREE.TorusGeometry(r, .03, 8, 96), glowMaterial(0x7fd6ff, 1));
        rim.rotation.x = Math.PI / 2; rim.position.y = y; group.add(rim);
      }
      bot.position.y = .65; bot.scale.setScalar(1.15);
      for (const side of [-1, 1]) {  // 2 màn hình nhỏ hai bên Master
        const panel = holoPanel(1.3, .8, a.color); panel.position.set(side * 1.9, 2.4, .3); panel.rotation.y = -side * .45;
        group.add(panel); parts.screens[id + side] = panel;
      }
      continue;
    }
    // màn hình hologram phía sau robot
    const main = holoPanel(1.9, 1.1, a.color); main.position.set(0, 2.75, -.75); group.add(main); parts.screens[id] = main;
    for (const side of [-1, 1]) {
      const sidePanel = holoPanel(.95, .7, a.color); sidePanel.position.set(side * 1.5, 2.55, -.55); sidePanel.rotation.y = -side * .55;
      sidePanel.material.map = screenTexture(a.color, side < 0 ? 'DATA' : 'STATUS', []); group.add(sidePanel);
    }
    // ghế, bàn cong, laptop
    const metal = new THREE.MeshStandardMaterial({color: 0xd7e0ee, metalness: .55, roughness: .25});
    const darkMetal = new THREE.MeshStandardMaterial({color: 0x1c2846, metalness: .7, roughness: .35});
    const top = new THREE.Mesh(new THREE.BoxGeometry(2.5, .1, .9), metal); top.position.set(0, .95, .7); group.add(top);
    const front = new THREE.Mesh(new THREE.BoxGeometry(2.5, .85, .12), darkMetal); front.position.set(0, .5, 1.12); group.add(front);
    const strip = new THREE.Mesh(new THREE.BoxGeometry(2.4, .04, .02), new THREE.MeshBasicMaterial({color: a.color})); strip.position.set(0, .82, 1.19); group.add(strip);
    const laptopBase = new THREE.Mesh(new THREE.BoxGeometry(.6, .03, .4), darkMetal); laptopBase.position.set(0, 1.02, .62); group.add(laptopBase);
    const laptopScreen = new THREE.Mesh(new THREE.PlaneGeometry(.58, .38), new THREE.MeshBasicMaterial({color: a.color, transparent: true, opacity: .8, side: THREE.DoubleSide}));
    laptopScreen.position.set(0, 1.2, .43); laptopScreen.rotation.x = -.25; laptopScreen.rotation.y = Math.PI; group.add(laptopScreen);
    bot.position.z = 0; bot.userData.laptop = laptopScreen;
  }
  // dây sáng nối các bàn về Master + "gói dữ liệu" chạy trên dây
  for (const id of ['analyst', 'writer', 'studio']) {
    const from = new THREE.Vector3(...AGENTS[id].pos).add(new THREE.Vector3(0, .05, 0));
    const to = new THREE.Vector3(...AGENTS.conductor.pos).add(new THREE.Vector3(0, .05, 0));
    const mid = from.clone().lerp(to, .5); mid.y = .05;
    const curve = new THREE.CatmullRomCurve3([from, from.clone().lerp(mid, .5).setY(.05), mid, mid.clone().lerp(to, .5).setY(.05), to]);
    const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 64, .035, 8, false), glowMaterial(0x3d6fb7, .7));
    scene.add(tube);
    const packets = [];
    for (let i = 0; i < 4; i++) {
      const p = new THREE.Mesh(new THREE.SphereGeometry(.09, 12, 8), glowMaterial(AGENTS[id].color, 1));
      p.visible = false; scene.add(p); packets.push(p);
    }
    parts.links[id] = {curve, tube, packets};
  }
}

function applyCamera() {
  const r = view.radius, p = view.phi, t = view.theta;
  camera.position.set(view.target.x + r * Math.sin(p) * Math.sin(t), view.target.y + r * Math.cos(p), view.target.z + r * Math.sin(p) * Math.cos(t));
  camera.lookAt(view.target);
}

function resize() {
  const w = host.clientWidth, h = host.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.updateProjectionMatrix();
}

function placeLabels() {
  const w = host.clientWidth, h = host.clientHeight, v = new THREE.Vector3();
  for (const [id, el] of Object.entries(labels)) {
    const a = AGENTS[id];
    const g = new THREE.Vector3(...a.pos);
    const off = new THREE.Vector3(0, 0, a.master ? .4 : 1.25).applyAxisAngle(new THREE.Vector3(0, 1, 0), a.rot || 0);
    v.set(g.x + off.x, a.master ? 3.75 : .55, g.z + off.z).project(camera);
    const visible = v.z < 1;
    el.style.transform = `translate(-50%, 0) translate(${(v.x * .5 + .5) * w}px, ${(-v.y * .5 + .5) * h}px)`;
    el.style.opacity = visible ? '1' : '0';
    el.style.zIndex = String(Math.round((1 - v.z) * 10000));
    el.classList.toggle('compact', Math.abs(Math.atan2(Math.sin(view.theta), Math.cos(view.theta))) > .5 || view.radius > 16);
  }
}

function frame() {
  raf = 0;
  if (!running) return;
  const dt = Math.min(clock.getDelta(), .05);
  view.t += dt;
  if (view.auto && !view.drag) view.theta = Math.sin(view.t * .12) * .55;
  applyCamera();
  if (parts.globe) parts.globe.rotation.y += dt * .25;
  for (const [id, bot] of Object.entries(parts.robots)) {
    const st = state[id] || 'waiting', u = bot.userData, time = view.t + id.length;
    const working = st === 'working';
    u.head.rotation.z = working ? Math.sin(time * 4) * .06 : Math.sin(time * .8) * .03;
    u.head.position.y = 1.72 + (working ? Math.abs(Math.sin(time * 4)) * .04 : 0);
    u.arms.forEach((arm, i) => { arm.rotation.x = -.9 + (working ? Math.sin(time * 18 + i * Math.PI) * .15 : 0); });
    const blink = (time % 4.2) < .12 ? .1 : 1;
    u.eyes.scale.y = blink;
    const color = st === 'done' ? new THREE.Color(0x4fe3a8) : st === 'waiting' ? u.baseColor.clone().multiplyScalar(.45) : u.baseColor;
    u.glow.color.copy(color);
    if (u.crown) u.crown.position.y = .42 + (working ? Math.sin(time * 2.4) * .04 : 0);
    if (u.laptop) u.laptop.material.opacity = working ? .6 + Math.sin(time * 9) * .25 : .35;
    const ring = parts.rings[id];
    ring.scale.setScalar(working ? 1 + Math.sin(time * 3) * .06 : 1);
    ring.material.opacity = working ? .95 : st === 'done' ? .6 : .3;
    parts.lights[id].intensity = working ? 9 + Math.sin(time * 3) * 2 : st === 'done' ? 5 : 2;
  }
  for (const [id, link] of Object.entries(parts.links)) {
    const working = state[id] === 'working';
    link.tube.material.color.set(working ? AGENTS[id].color : state[id] === 'done' ? 0x4fe3a8 : 0x3d6fb7);
    link.packets.forEach((p, i) => {
      p.visible = working;
      if (working) p.position.copy(link.curve.getPointAt(((view.t * .35 + i / link.packets.length) % 1)));
    });
  }
  renderer.render(scene, camera);
  placeLabels();
  raf = requestAnimationFrame(frame);
}

export function mount(container, canvasElement, labelElements) {
  host = container; canvas = canvasElement; labels = labelElements;
  try {
    renderer = new THREE.WebGLRenderer({canvas, antialias: true, alpha: false, powerPreference: 'high-performance'});
  } catch (error) {
    return false;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x040a1e);
  scene.fog = new THREE.FogExp2(0x061230, .028);
  camera = new THREE.PerspectiveCamera(42, 16 / 9, .1, 100);
  scene.add(new THREE.HemisphereLight(0x8cc8ff, 0x0a1030, 1.1));
  const sun = new THREE.DirectionalLight(0xffc28a, 1.6); sun.position.set(-6, 9, -10); scene.add(sun);
  const fill = new THREE.DirectionalLight(0x7fb8ff, .9); fill.position.set(5, 7, 9); scene.add(fill);
  const center = new THREE.PointLight(0x5ab8ff, 14, 14, 1.4); center.position.set(0, 6, -2); scene.add(center);
  buildRoom(); buildGlobe(); buildAgents();
  clock = new THREE.Clock();
  // kéo để xoay quanh phòng, cuộn để phóng to
  canvas.addEventListener('pointerdown', e => { view.drag = {x: e.clientX, y: e.clientY}; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener('pointermove', e => {
    if (!view.drag) return;
    view.theta -= (e.clientX - view.drag.x) * .006;
    view.phi = Math.max(.55, Math.min(1.42, view.phi - (e.clientY - view.drag.y) * .004));
    view.drag = {x: e.clientX, y: e.clientY};
  });
  const end = () => { view.drag = null; };
  canvas.addEventListener('pointerup', end); canvas.addEventListener('pointercancel', end);
  canvas.addEventListener('wheel', e => { e.preventDefault(); view.radius = Math.max(6, Math.min(20, view.radius * (e.deltaY < 0 ? .92 : 1.08))); }, {passive: false});
  new ResizeObserver(resize).observe(host);
  resize();
  return true;
}

export function start() { if (!renderer || running) return; running = true; clock.getDelta(); raf = requestAnimationFrame(frame); }
export function stop() { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }
export function setAuto(on) { view.auto = on; }
export function resetView() { Object.assign(view, {theta: 0, phi: 1.12, radius: 13.5}); }

// states: {conductor|analyst|writer|studio: waiting/working/done/error}; screens: {id: [{name, pct}]}; masterPct: số %
export function update(states, screens, masterPct) {
  state = states || {};
  if (!renderer) return;
  for (const id of ['analyst', 'writer', 'studio']) {
    const panel = parts.screens[id];
    if (!panel) continue;
    const key = JSON.stringify(screens[id] || []);
    if (panel.userData.key === key) continue;
    panel.userData.key = key;
    panel.material.map?.dispose();
    panel.material.map = screenTexture(AGENTS[id].color, 'LIVE', screens[id] || []);
    panel.material.needsUpdate = true;
  }
  const left = parts.screens['conductor-1'], right = parts.screens['conductor1'];
  const key = String(masterPct);
  if (right && right.userData.key !== key) {
    right.userData.key = key;
    right.material.map?.dispose();
    right.material.map = canvasTexture(512, 300, (g, w, h) => {
      g.fillStyle = 'rgba(25,80,200,0.30)'; g.fillRect(0, 0, w, h); g.strokeStyle = '#5ab8ff'; g.lineWidth = 6; g.strokeRect(3, 3, w - 6, h - 6);
      g.fillStyle = '#9fd8ff'; g.font = 'bold 30px Arial'; g.fillText('TIẾN ĐỘ', 24, 48);
      g.fillStyle = '#7fd6ff'; g.font = 'bold 120px Arial'; g.fillText(masterPct == null ? '—' : masterPct + '%', 40, 200);
    });
    right.material.needsUpdate = true;
  }
  if (left && !left.userData.key) {
    left.userData.key = 'plan';
    left.material.map = canvasTexture(512, 300, (g, w, h) => {
      g.fillStyle = 'rgba(25,80,200,0.30)'; g.fillRect(0, 0, w, h); g.strokeStyle = '#5ab8ff'; g.lineWidth = 6; g.strokeRect(3, 3, w - 6, h - 6);
      g.fillStyle = '#9fd8ff'; g.font = 'bold 30px Arial'; g.fillText('KẾ HOẠCH', 24, 48);
      for (let i = 0; i < 4; i++) { g.fillStyle = 'rgba(127,214,255,' + (0.8 - i * .15) + ')'; g.fillRect(24, 90 + i * 45, 420 - i * 70, 16); }
    });
    left.material.needsUpdate = true;
  }
}
