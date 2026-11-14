// Phòng điều khiển 3D (Three.js / WebGL): Master Agent trên bục giữa, 3 robot agent ngồi bàn, quả địa cầu hologram,
// dây sáng nối về Master. Hiệu ứng: phát sáng (bloom), phản chiếu môi trường, hạt bụi sáng, chùm đèn trần.
// Dữ liệu tiến độ do app.js đưa vào qua update(); chữ (nhãn, danh sách việc) là HTML đặt theo vị trí 3D chiếu ra màn hình.
import * as THREE from './three.module.min.js';
import {EffectComposer} from './addons/EffectComposer.js';
import {RenderPass} from './addons/RenderPass.js';
import {UnrealBloomPass} from './addons/UnrealBloomPass.js';
import {OutputPass} from './addons/OutputPass.js';
import {RoomEnvironment} from './addons/RoomEnvironment.js';
import {RoundedBoxGeometry} from './addons/RoundedBoxGeometry.js';

const AGENTS = {
  conductor: {color: 0x5ab8ff, pos: [0, 0, -1.7], master: true},
  analyst: {color: 0xffc14d, pos: [-4.4, 0, 1.3], rot: 0.58},
  writer: {color: 0xb28cff, pos: [0, 0, 2.7], rot: 0},
  studio: {color: 0x3fe0a0, pos: [4.4, 0, 1.3], rot: -0.58},
};
const HOME = {theta: 0, phi: 1.13, radius: 13.6};

let renderer, composer, bloom, scene, camera, canvas, host, labels = {}, raf = 0, clock, running = false;
const view = {...HOME, target: new THREE.Vector3(0, 1.2, .4), auto: true, drag: null, t: 0, intro: 0};
const parts = {robots: {}, screens: {}, links: {}, rings: {}, lights: {}, globe: null, dust: null, flowTexture: null, scan: []};
const perf = {frames: 0, time: 0, low: false};
let state = {};
const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
let hovered = null;

// ---------- tiện ích ----------
function canvasTexture(width, height, draw, repeat) {
  const c = document.createElement('canvas');
  c.width = width; c.height = height;
  draw(c.getContext('2d'), width, height);
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  if (repeat) { texture.wrapS = texture.wrapT = THREE.RepeatWrapping; texture.repeat.set(...repeat); }
  return texture;
}
const hexOf = color => '#' + new THREE.Color(color).getHexString();
function glow(color, opacity = 1) {
  return new THREE.MeshBasicMaterial({color, transparent: opacity < 1, opacity, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false});
}
function softDot() {
  return canvasTexture(64, 64, (g, w, h) => {
    const r = g.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
    r.addColorStop(0, 'rgba(255,255,255,1)'); r.addColorStop(.35, 'rgba(180,220,255,.5)'); r.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = r; g.fillRect(0, 0, w, h);
  });
}

// ---------- căn phòng ----------
function windowTexture(seed) {
  return canvasTexture(256, 384, (g, w, h) => {
    const sky = g.createLinearGradient(0, 0, 0, h);
    sky.addColorStop(0, '#0b1838'); sky.addColorStop(.3, '#2f5596'); sky.addColorStop(.55, '#d98f6a');
    sky.addColorStop(.68, '#ffcf9a'); sky.addColorStop(.69, '#3b5684'); sky.addColorStop(1, '#101c38');
    g.fillStyle = sky; g.fillRect(0, 0, w, h);
    const sun = g.createRadialGradient(w * .6, h * .66, 0, w * .6, h * .66, 60);
    sun.addColorStop(0, 'rgba(255,230,180,.9)'); sun.addColorStop(1, 'rgba(255,200,140,0)');
    g.fillStyle = sun; g.fillRect(0, 0, w, h);
    const base = h * .69;
    g.fillStyle = '#131d38';
    for (let i = 0; i < 3; i++) {
      const x = 20 + ((seed * 67 + i * 83) % 200);
      g.fillRect(x, base - 130, 6, 130); g.fillRect(x - 36, base - 130, 80, 6); g.fillRect(x + 34, base - 124, 3, 50);
      g.fillRect(x - 8, base - 60, 22, 4);
    }
    for (let i = 0; i < 2; i++) {
      const x = (seed * 41 + i * 120) % 190;
      g.fillRect(x, base - 16, 80, 16); g.fillRect(x + 8, base - 32, 34, 16); g.fillRect(x + 50, base - 24, 20, 8);
    }
    for (let i = 0; i < 18; i++) { g.fillStyle = `rgba(255,${190 + i % 50},120,${.25 + (i % 4) * .15})`; g.fillRect((seed * 31 + i * 37) % w, base - 4 - (i % 5) * 18, 3, 3); }
    g.fillStyle = 'rgba(255,210,160,.18)';
    for (let y = base + 8; y < h; y += 12) g.fillRect(0, y, w, 2);
  });
}

function floorTexture() {
  return canvasTexture(1024, 1024, (g, w, h) => {
    g.fillStyle = '#0a1634'; g.fillRect(0, 0, w, h);
    g.strokeStyle = 'rgba(120,190,255,0.10)'; g.lineWidth = 2;
    for (let i = 0; i <= w; i += 64) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, h); g.stroke(); g.beginPath(); g.moveTo(0, i); g.lineTo(w, i); g.stroke(); }
    g.fillStyle = 'rgba(140,200,255,0.06)';
    for (let i = 0; i < 60; i++) g.fillRect((i * 173) % w, (i * 389) % h, 64, 64);
  }, [6, 6]);
}

function buildRoom() {
  const floor = new THREE.Mesh(new THREE.CircleGeometry(16, 128),
    new THREE.MeshStandardMaterial({map: floorTexture(), color: 0xc6d6ff, metalness: .82, roughness: .24, envMapIntensity: .35}));
  floor.rotation.x = -Math.PI / 2; scene.add(floor);
  for (const [r, w, c, o] of [[2.25, .045, 0x7fd6ff, .95], [3.35, .03, 0x4fb7ff, .55], [6.4, .045, 0x7fd6ff, .6], [9.6, .05, 0x9a7bff, .45], [12.6, .04, 0x4fb7ff, .3]]) {
    const ring = new THREE.Mesh(new THREE.RingGeometry(r - w, r + w, 160), glow(c, o));
    ring.rotation.x = -Math.PI / 2; ring.position.y = .012; scene.add(ring);
  }
  // vòng quét chạy chậm trên sàn
  const scan = new THREE.Mesh(new THREE.RingGeometry(0, 1, 128, 1, 0, Math.PI / 6), glow(0x4fb7ff, .12));
  scan.rotation.x = -Math.PI / 2; scan.position.y = .015; scan.scale.setScalar(9.5); scene.add(scan); parts.scan.push(scan);
  // tường cong, khung cửa và cửa sổ
  const wall = new THREE.Mesh(new THREE.CylinderGeometry(15, 15, 9, 128, 1, true),
    new THREE.MeshStandardMaterial({color: 0x0b1838, metalness: .55, roughness: .5, side: THREE.BackSide}));
  wall.position.y = 4.5; scene.add(wall);
  const frameMat = new THREE.MeshStandardMaterial({color: 0x24365e, metalness: .9, roughness: .25});
  for (let i = 0; i < 11; i++) {
    const a = Math.PI * (.63 + i * .074), x = Math.sin(a) * 14.85, z = Math.cos(a) * 14.85;
    const win = new THREE.Mesh(new THREE.PlaneGeometry(2.25, 4.2), new THREE.MeshBasicMaterial({map: windowTexture(i + 1), toneMapped: false}));
    win.position.set(x, 3.3, z); win.lookAt(0, 3.3, 0); scene.add(win);
    const fa = a + .037, frame = new THREE.Mesh(new THREE.BoxGeometry(.16, 4.8, .3), frameMat);
    frame.position.set(Math.sin(fa) * 14.75, 3.3, Math.cos(fa) * 14.75); frame.lookAt(0, 3.3, 0); scene.add(frame);
  }
  const strip = new THREE.Mesh(new THREE.TorusGeometry(14.6, .03, 8, 200), glow(0x4fb7ff, .9));
  strip.rotation.x = Math.PI / 2; strip.position.y = .9; scene.add(strip);
  // trần: vòng đèn + chùm sáng
  const ceiling = new THREE.Mesh(new THREE.CircleGeometry(16, 64), new THREE.MeshStandardMaterial({color: 0x040a1c, roughness: .8}));
  ceiling.rotation.x = Math.PI / 2; ceiling.position.y = 9; scene.add(ceiling);
  for (const [r, y, c] of [[3.2, 7.4, 0x7fd6ff], [5.6, 7.8, 0x7fd6ff], [8.6, 8.2, 0x5ab8ff], [12, 8.6, 0xffb36b]]) {
    const torus = new THREE.Mesh(new THREE.TorusGeometry(r, .06, 12, 200), glow(c, 1));
    torus.rotation.x = Math.PI / 2; torus.position.y = y; scene.add(torus);
  }
  const coneTex = canvasTexture(8, 256, (g, w, h) => {
    const grad = g.createLinearGradient(0, 0, 0, h); grad.addColorStop(0, 'rgba(255,255,255,0.0)'); grad.addColorStop(.15, 'rgba(255,255,255,.55)'); grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grad; g.fillRect(0, 0, w, h);
  });
  for (const [id, a] of Object.entries(AGENTS)) {
    const cone = new THREE.Mesh(new THREE.CylinderGeometry(.25, 1.9, 7.4, 48, 1, true),
      new THREE.MeshBasicMaterial({color: a.color, map: coneTex, transparent: true, opacity: id === 'conductor' ? .09 : .055, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false}));
    cone.position.set(a.pos[0], 3.7, a.pos[2]); scene.add(cone);
  }
  // cây trang trí
  const potMat = new THREE.MeshPhysicalMaterial({color: 0xd5deec, roughness: .35, clearcoat: .6, envMapIntensity: .3});
  const leafMat = new THREE.MeshStandardMaterial({color: 0x2c9a5c, roughness: .65, flatShading: true});
  for (const [x, z] of [[-8.8, -4.6], [8.8, -4.6], [-11.5, 2.5], [11.5, 2.5]]) {
    const pot = new THREE.Mesh(new THREE.CylinderGeometry(.5, .38, .9, 32), potMat); pot.position.set(x, .45, z); scene.add(pot);
    for (let i = 0; i < 3; i++) {
      const leaves = new THREE.Mesh(new THREE.IcosahedronGeometry(.55 - i * .1, 1), leafMat);
      leaves.position.set(x + (i - 1) * .18, 1.25 + i * .45, z); leaves.scale.set(1, 1.25, 1); scene.add(leaves);
    }
  }
}

// ---------- quả địa cầu hologram ----------
function buildGlobe() {
  const group = new THREE.Group();
  group.position.set(0, 4.2, -4.6);
  group.add(new THREE.Mesh(new THREE.SphereGeometry(2.25, 64, 48), new THREE.MeshBasicMaterial({color: 0x1550c0, transparent: true, opacity: .22, blending: THREE.AdditiveBlending, depthWrite: false})));
  group.add(new THREE.Mesh(new THREE.SphereGeometry(.9, 32, 24), glow(0x3aa5ff, .35)));
  const wire = new THREE.Mesh(new THREE.SphereGeometry(2.3, 28, 18), new THREE.MeshBasicMaterial({color: 0x8fdcff, wireframe: true, transparent: true, opacity: .28, blending: THREE.AdditiveBlending, depthWrite: false}));
  group.add(wire);
  const points = [];
  for (let i = 0; i < 900; i++) {  // dải điểm như lục địa
    const lat = (Math.sin(i * 12.9898) * .5) * Math.PI * .8, lon = (i * 2.399963) % (Math.PI * 2);
    if (Math.sin(lon * 3 + lat * 4) + Math.cos(lon * 2 - lat * 3) < .2) continue;
    const r = 2.33;
    points.push(Math.cos(lat) * Math.cos(lon) * r, Math.sin(lat) * r, Math.cos(lat) * Math.sin(lon) * r);
  }
  const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  group.add(new THREE.Points(geo, new THREE.PointsMaterial({color: 0xbdefff, size: .09, map: softDot(), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false})));
  for (const [r, tilt, c] of [[2.85, 1.25, 0x7fd6ff], [3.2, 1.75, 0xb28cff]]) {
    const orbit = new THREE.Mesh(new THREE.TorusGeometry(r, .012, 8, 200), glow(c, .8)); orbit.rotation.x = tilt; group.add(orbit);
    const sat = new THREE.Mesh(new THREE.SphereGeometry(.07, 12, 8), glow(c, 1)); orbit.add(sat); sat.position.x = r;
  }
  const base = new THREE.Mesh(new THREE.CylinderGeometry(.6, .9, .25, 48), new THREE.MeshStandardMaterial({color: 0x173b82, metalness: .9, roughness: .25}));
  base.position.set(0, -3.95, 0); group.add(base);
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(.08, .55, 1.6, 32, 1, true), glow(0x4fb7ff, .25)); beam.position.y = -3.0; group.add(beam);
  scene.add(group);
  parts.globe = {group, wire};
}

// ---------- robot ----------
function faceTexture(color, mood) {
  return canvasTexture(256, 160, (g, w, h) => {
    g.fillStyle = '#050b1c'; g.fillRect(0, 0, w, h);
    const c = mood === 'done' ? '#4fe3a8' : hexOf(color);
    g.strokeStyle = c; g.fillStyle = c; g.lineCap = 'round'; g.shadowColor = c; g.shadowBlur = 18;
    const eye = (x) => {
      if (mood === 'done') { g.lineWidth = 12; g.beginPath(); g.arc(x, 92, 26, Math.PI * 1.15, Math.PI * 1.85); g.stroke(); }  // mắt cười ^ ^
      else if (mood === 'waiting') { g.lineWidth = 10; g.beginPath(); g.moveTo(x - 24, 82); g.lineTo(x + 24, 82); g.stroke(); }  // mắt lim dim
      else { g.beginPath(); g.ellipse(x, 76, 20, 28, 0, 0, Math.PI * 2); g.fill(); g.fillStyle = '#ffffffcc'; g.beginPath(); g.arc(x + 7, 64, 6, 0, Math.PI * 2); g.fill(); g.fillStyle = c; }
    };
    eye(84); eye(172);
    if (mood === 'done') { g.lineWidth = 7; g.beginPath(); g.arc(128, 112, 18, Math.PI * .15, Math.PI * .85); g.stroke(); }
    else if (mood === 'working') { g.lineWidth = 6; g.beginPath(); g.moveTo(112, 128); g.lineTo(144, 128); g.stroke(); }
  });
}

function robot(color, master) {
  const shell = new THREE.MeshPhysicalMaterial({color: 0xf4f7fc, metalness: .05, roughness: .22, clearcoat: 1, clearcoatRoughness: .08, envMapIntensity: .35});
  const joint = new THREE.MeshPhysicalMaterial({color: 0xb7c4dc, metalness: .6, roughness: .3, clearcoat: .6, envMapIntensity: .4});
  const glass = new THREE.MeshPhysicalMaterial({color: 0x050b1c, metalness: .2, roughness: .05, clearcoat: 1, envMapIntensity: .8});
  const light = new THREE.MeshBasicMaterial({color, toneMapped: false});
  const root = new THREE.Group();
  const body = new THREE.Mesh(new RoundedBoxGeometry(.82, .74, .58, 6, .26), shell); body.position.y = 1.0; root.add(body);
  const chestRing = new THREE.Mesh(new THREE.TorusGeometry(.13, .025, 12, 40), light); chestRing.position.set(0, 1.04, .295); root.add(chestRing);
  const chestCore = new THREE.Mesh(new THREE.CircleGeometry(.075, 32), light); chestCore.position.set(0, 1.04, .296); root.add(chestCore);
  const neck = new THREE.Mesh(new THREE.CylinderGeometry(.12, .15, .14, 24), joint); neck.position.y = 1.44; root.add(neck);
  const head = new THREE.Group(); head.position.y = 1.86; root.add(head);
  head.add(new THREE.Mesh(new RoundedBoxGeometry(1.0, .8, .78, 8, .34), shell));
  const screen = new THREE.Mesh(new RoundedBoxGeometry(.76, .5, .06, 4, .14), glass); screen.position.z = .37; head.add(screen);
  const face = new THREE.Mesh(new THREE.PlaneGeometry(.68, .42), new THREE.MeshBasicMaterial({map: faceTexture(color, 'waiting'), toneMapped: false}));
  face.position.z = .405; head.add(face);
  for (const x of [-.53, .53]) {
    const ear = new THREE.Mesh(new THREE.CylinderGeometry(.17, .17, .1, 32), joint); ear.rotation.z = Math.PI / 2; ear.position.x = x; head.add(ear);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(.1, .022, 10, 32), light); ring.rotation.y = Math.PI / 2; ring.position.x = x * 1.11; head.add(ring);
  }
  const antenna = new THREE.Mesh(new THREE.CylinderGeometry(.018, .018, .26, 8), joint); antenna.position.y = .52; head.add(antenna);
  const tip = new THREE.Mesh(new THREE.SphereGeometry(.055, 16, 12), light); tip.position.y = .68; head.add(tip);
  const arms = [];
  for (const side of [-1, 1]) {
    const pivot = new THREE.Group(); pivot.position.set(side * .5, 1.24, 0); root.add(pivot);
    pivot.add(new THREE.Mesh(new THREE.SphereGeometry(.13, 20, 16), joint));
    const arm = new THREE.Mesh(new THREE.CapsuleGeometry(.1, .34, 8, 16), shell); arm.position.set(0, -.27, 0); pivot.add(arm);
    const hand = new THREE.Mesh(new THREE.SphereGeometry(.11, 20, 16), joint); hand.position.set(0, -.52, 0); pivot.add(hand);
    pivot.rotation.x = -1.0; pivot.rotation.z = side * .12;
    arms.push(pivot);
  }
  let crown = null;
  if (master) {
    const gold = new THREE.MeshPhysicalMaterial({color: 0xffc83d, metalness: 1, roughness: .18, clearcoat: 1, emissive: 0x4a2a00});
    crown = new THREE.Group(); crown.position.y = .46; head.add(crown);
    crown.add(new THREE.Mesh(new THREE.CylinderGeometry(.3, .32, .14, 32, 1, true), gold));
    for (let i = 0; i < 6; i++) {
      const a = i / 6 * Math.PI * 2, spike = new THREE.Mesh(new THREE.ConeGeometry(.065, .24, 12), gold);
      spike.position.set(Math.sin(a) * .3, .18, Math.cos(a) * .3); crown.add(spike);
      const ball = new THREE.Mesh(new THREE.SphereGeometry(.03, 10, 8), gold); ball.position.set(Math.sin(a) * .3, .31, Math.cos(a) * .3); crown.add(ball);
      const gem = new THREE.Mesh(new THREE.SphereGeometry(.04, 12, 10), new THREE.MeshBasicMaterial({color: i % 2 ? 0xff4f6d : 0x5ab8ff, toneMapped: false}));
      gem.position.set(Math.sin(a) * .325, 0, Math.cos(a) * .325); crown.add(gem);
    }
  }
  root.userData = {head, arms, light, face, tip, crown, mood: 'waiting', color, baseColor: new THREE.Color(color)};
  root.traverse(o => { if (o.isMesh) o.userData.robot = root; });
  return root;
}

function chair(color) {
  const group = new THREE.Group();
  const dark = new THREE.MeshPhysicalMaterial({color: 0x1a2440, metalness: .4, roughness: .35, clearcoat: .8});
  const metal = new THREE.MeshStandardMaterial({color: 0xb9c6dc, metalness: .9, roughness: .2});
  const seat = new THREE.Mesh(new RoundedBoxGeometry(.9, .14, .8, 4, .06), dark); seat.position.y = .52; group.add(seat);
  const back = new THREE.Mesh(new RoundedBoxGeometry(.86, .95, .12, 4, .06), dark); back.position.set(0, 1.05, -.42); back.rotation.x = -.1; group.add(back);
  const trim = new THREE.Mesh(new THREE.BoxGeometry(.7, .03, .02), glow(color, .9)); trim.position.set(0, 1.42, -.36); group.add(trim);
  const stem = new THREE.Mesh(new THREE.CylinderGeometry(.05, .05, .45, 12), metal); stem.position.y = .27; group.add(stem);
  const base = new THREE.Mesh(new THREE.CylinderGeometry(.38, .42, .05, 32), metal); base.position.y = .03; group.add(base);
  return group;
}

function arcShape(rIn, rOut, half) {
  const shape = new THREE.Shape();
  shape.absarc(0, 0, rOut, Math.PI / 2 - half, Math.PI / 2 + half, false);
  shape.absarc(0, 0, rIn, Math.PI / 2 + half, Math.PI / 2 - half, true);
  return shape;
}

function desk(color) {
  const group = new THREE.Group();
  const top = new THREE.MeshPhysicalMaterial({color: 0xc9d4e6, metalness: .35, roughness: .3, clearcoat: .6, envMapIntensity: .3});
  const side = new THREE.MeshPhysicalMaterial({color: 0x17213d, metalness: .7, roughness: .3, clearcoat: .6, envMapIntensity: .5});
  const half = .62;
  const plate = new THREE.Mesh(new THREE.ExtrudeGeometry(arcShape(.78, 1.42, half), {depth: .08, bevelEnabled: true, bevelThickness: .02, bevelSize: .02, bevelSegments: 2, curveSegments: 48}), top);
  plate.rotation.x = Math.PI / 2; plate.position.y = 1.1; group.add(plate);   // mặt bàn cong ôm phía trước robot (+z)
  const front = new THREE.Mesh(new THREE.CylinderGeometry(1.4, 1.4, .95, 64, 1, true, -half, half * 2), side);
  front.position.y = .55; group.add(front);
  const trim = new THREE.Mesh(new THREE.TorusGeometry(1.43, .016, 8, 64, half * 2), glow(color, 1));
  trim.rotation.x = -Math.PI / 2; trim.rotation.z = Math.PI / 2 - half; trim.position.y = .92; group.add(trim);
  const trim2 = trim.clone(); trim2.position.y = .14; group.add(trim2);
  const keys = new THREE.Mesh(new THREE.PlaneGeometry(.62, .2), new THREE.MeshBasicMaterial({map: canvasTexture(128, 40, (g, w, h) => {
    g.fillStyle = '#0a1430'; g.fillRect(0, 0, w, h); g.fillStyle = hexOf(color);
    for (let y = 4; y < h - 4; y += 9) for (let x = 4; x < w - 4; x += 10) g.fillRect(x, y, 7, 6);
  }), toneMapped: false}));
  keys.rotation.x = -Math.PI / 2; keys.position.set(0, 1.12, .98); group.add(keys);
  const lapBase = new THREE.Mesh(new RoundedBoxGeometry(.7, .03, .44, 2, .015), side); lapBase.position.set(.62, 1.13, 1.0); lapBase.rotation.y = -.5; group.add(lapBase);
  const lapScreen = new THREE.Mesh(new THREE.PlaneGeometry(.66, .4), new THREE.MeshBasicMaterial({color, transparent: true, opacity: .75, side: THREE.DoubleSide, toneMapped: false}));
  lapScreen.position.set(.52, 1.34, .82); lapScreen.rotation.set(-.25, Math.PI - .5, 0); group.add(lapScreen);
  group.userData.laptop = lapScreen;
  return group;
}

// ---------- màn hình hologram ----------
function screenTexture(color, title, lines, kind = 'list') {
  return canvasTexture(640, 380, (g, w, h) => {
    const c = hexOf(color);
    g.clearRect(0, 0, w, h);
    const bg = g.createLinearGradient(0, 0, 0, h); bg.addColorStop(0, 'rgba(40,110,230,0.38)'); bg.addColorStop(1, 'rgba(10,40,120,0.25)');
    g.fillStyle = bg; g.fillRect(0, 0, w, h);
    g.fillStyle = 'rgba(255,255,255,0.05)'; for (let y = 0; y < h; y += 4) g.fillRect(0, y, w, 1);  // vạch quét
    g.strokeStyle = c; g.lineWidth = 5; g.strokeRect(4, 4, w - 8, h - 8);
    g.lineWidth = 10;
    for (const [x, y, dx, dy] of [[4, 4, 1, 1], [w - 4, 4, -1, 1], [4, h - 4, 1, -1], [w - 4, h - 4, -1, -1]]) {
      g.beginPath(); g.moveTo(x, y + dy * 36); g.lineTo(x, y); g.lineTo(x + dx * 36, y); g.stroke();
    }
    g.fillStyle = c; g.fillRect(20, 20, w - 40, 44); g.fillStyle = '#04112e'; g.font = 'bold 28px Arial'; g.fillText(title, 34, 52);
    g.beginPath(); g.arc(w - 46, 42, 9, 0, Math.PI * 2); g.fill();
    if (kind === 'chart') {
      g.globalAlpha = .8;
      for (let i = 0; i < 9; i++) { const bh = 60 + ((i * 53) % 140); g.fillStyle = i % 3 ? c : '#ffffff'; g.fillRect(40 + i * 62, h - 40 - bh, 38, bh); }
      g.globalAlpha = 1; return;
    }
    if (kind === 'ring') {
      g.lineWidth = 26; g.strokeStyle = 'rgba(255,255,255,.15)'; g.beginPath(); g.arc(w / 2, 220, 100, 0, Math.PI * 2); g.stroke();
      g.strokeStyle = c; g.beginPath(); g.arc(w / 2, 220, 100, -Math.PI / 2, Math.PI * 1.1); g.stroke(); return;
    }
    if (!lines.length) {
      g.fillStyle = '#cfe0ff'; g.font = '32px Arial'; g.fillText('Đang chờ việc…', 34, 140);
      g.strokeStyle = c; g.lineWidth = 4; g.globalAlpha = .7; g.beginPath();
      for (let x = 34; x < w - 34; x += 8) g.lineTo(x, 260 + Math.sin(x / 24) * 22 + Math.sin(x / 7) * 6);
      g.stroke(); g.globalAlpha = 1; return;
    }
    lines.slice(0, 4).forEach((l, i) => {
      const y = 118 + i * 64;
      g.fillStyle = '#ffffff'; g.font = 'bold 28px Arial'; g.fillText(l.name.slice(0, 16), 34, y);
      g.fillStyle = 'rgba(255,255,255,0.15)'; g.fillRect(330, y - 22, 200, 20);
      g.fillStyle = c; g.fillRect(330, y - 22, 2 * l.pct, 20);
      g.fillStyle = '#ffffff'; g.font = '26px Arial'; g.fillText(l.pct + '%', 548, y);
    });
  });
}

function holoPanel(width, height, color, title, kind) {
  const material = new THREE.MeshBasicMaterial({transparent: true, opacity: .95, side: THREE.DoubleSide, depthWrite: false,
    blending: THREE.AdditiveBlending, map: screenTexture(color, title, [], kind), toneMapped: false});
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(width, height), material);
  const projector = new THREE.Mesh(new THREE.CylinderGeometry(.03, width * .32, height * .9, 24, 1, true), glow(color, .08));
  projector.position.y = -height * .95; mesh.add(projector);
  mesh.userData.title = title;
  return mesh;
}

// ---------- các agent ----------
function buildAgents() {
  for (const [id, a] of Object.entries(AGENTS)) {
    const group = new THREE.Group();
    group.position.set(...a.pos); group.rotation.y = a.rot || 0; scene.add(group);
    const lamp = new THREE.PointLight(a.color, 2, 6, 1.8); lamp.position.set(0, 2.6, 1.0); group.add(lamp); parts.lights[id] = lamp;
    const ring = new THREE.Mesh(new THREE.RingGeometry(1.55, 1.66, 96), glow(a.color, .8)); ring.rotation.x = -Math.PI / 2; ring.position.y = .02; group.add(ring);
    const disc = new THREE.Mesh(new THREE.CircleGeometry(1.55, 96), glow(a.color, .06)); disc.rotation.x = -Math.PI / 2; disc.position.y = .018; group.add(disc);
    parts.rings[id] = ring;
    const bot = robot(a.color, a.master); group.add(bot); parts.robots[id] = bot;
    if (a.master) {
      const metal = new THREE.MeshPhysicalMaterial({color: 0x183c86, metalness: .85, roughness: .22, clearcoat: 1});
      const d1 = new THREE.Mesh(new THREE.CylinderGeometry(1.8, 2.0, .36, 96), metal); d1.position.y = .18; group.add(d1);
      const d2 = new THREE.Mesh(new THREE.CylinderGeometry(1.25, 1.42, .32, 96), metal); d2.position.y = .52; group.add(d2);
      for (const [r, y] of [[2.0, .37], [1.42, .69], [1.0, .7]]) {
        const rim = new THREE.Mesh(new THREE.TorusGeometry(r, .028, 10, 128), glow(0x7fd6ff, 1)); rim.rotation.x = Math.PI / 2; rim.position.y = y; group.add(rim);
      }
      bot.userData.floatBase = .7; bot.position.y = .7; bot.scale.setScalar(1.18);
      for (const side of [-1, 1]) {
        const panel = holoPanel(1.4, .84, a.color, side < 0 ? 'KẾ HOẠCH' : 'TIẾN ĐỘ', side < 0 ? 'chart' : 'list');
        panel.position.set(side * 2.05, 2.75, .35); panel.rotation.y = -side * .42; group.add(panel); parts.screens[id + side] = panel;
      }
      continue;
    }
    const seat = chair(a.color); seat.position.z = -.3; group.add(seat);
    bot.position.set(0, .05, -.15);
    const table = desk(a.color); group.add(table); bot.userData.laptop = table.userData.laptop;
    const main = holoPanel(2.0, 1.18, a.color, 'LIVE · ' + ({analyst: 'PHÂN TÍCH', writer: 'VIẾT PROMPT', studio: 'STUDIO MUSE'}[id]), 'list');
    main.position.set(0, 3.2, -.95); group.add(main); parts.screens[id] = main;
    for (const side of [-1, 1]) {
      const p = holoPanel(1.0, .72, a.color, side < 0 ? 'DATA' : 'STATUS', side < 0 ? 'chart' : 'ring');
      p.position.set(side * 1.6, 2.9, -.7); p.rotation.y = -side * .55; group.add(p);
    }
  }
  parts.flowTexture = canvasTexture(256, 8, (g, w, h) => {
    const grad = g.createLinearGradient(0, 0, w, 0);
    grad.addColorStop(0, 'rgba(255,255,255,0)'); grad.addColorStop(.7, 'rgba(255,255,255,.9)'); grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = 'rgba(255,255,255,.18)'; g.fillRect(0, 0, w, h); g.fillStyle = grad; g.fillRect(0, 0, w, h);
  }, [6, 1]);
  for (const id of ['analyst', 'writer', 'studio']) {
    const from = new THREE.Vector3(...AGENTS[id].pos), to = new THREE.Vector3(...AGENTS.conductor.pos);
    from.y = to.y = .06;
    const mid = from.clone().lerp(to, .5); mid.y = .06;
    const curve = new THREE.CatmullRomCurve3([from, from.clone().lerp(mid, .5), mid, mid.clone().lerp(to, .5), to]);
    const tex = parts.flowTexture.clone(); tex.needsUpdate = true;
    const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 80, .045, 10, false),
      new THREE.MeshBasicMaterial({color: 0x3d6fb7, map: tex, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false}));
    scene.add(tube);
    const packets = [];
    for (let i = 0; i < 5; i++) {
      const p = new THREE.Mesh(new THREE.SphereGeometry(.085, 14, 10), glow(AGENTS[id].color, 1)); p.visible = false; scene.add(p); packets.push(p);
    }
    parts.links[id] = {curve, tube, tex, packets};
  }
}

function buildDust() {
  const count = 700, pos = new Float32Array(count * 3), speed = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const r = Math.sqrt(Math.random()) * 13, a = Math.random() * Math.PI * 2;
    pos[i * 3] = Math.cos(a) * r; pos[i * 3 + 1] = Math.random() * 8; pos[i * 3 + 2] = Math.sin(a) * r; speed[i] = .08 + Math.random() * .25;
  }
  const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const dust = new THREE.Points(geo, new THREE.PointsMaterial({color: 0x9fd8ff, size: .07, map: softDot(), transparent: true, opacity: .75, blending: THREE.AdditiveBlending, depthWrite: false}));
  dust.userData.speed = speed; scene.add(dust); parts.dust = dust;
}

// ---------- camera, khung hình ----------
function applyCamera() {
  const ease = 1 - Math.pow(1 - Math.min(view.intro, 1), 3);  // camera bay vào khi mở trang
  const r = view.radius + (1 - ease) * 10, p = view.phi - (1 - ease) * .45, t = view.theta + (1 - ease) * .9;
  camera.position.set(view.target.x + r * Math.sin(p) * Math.sin(t), view.target.y + r * Math.cos(p), view.target.z + r * Math.sin(p) * Math.cos(t));
  camera.lookAt(view.target);
}

function resize() {
  const w = host.clientWidth, h = host.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  composer?.setSize(w, h);
  bloom?.resolution.set(w / 2, h / 2);
  camera.aspect = w / h; camera.updateProjectionMatrix();
}

function placeLabels() {
  const w = host.clientWidth, h = host.clientHeight, v = new THREE.Vector3(), up = new THREE.Vector3(0, 1, 0);
  for (const [id, el] of Object.entries(labels)) {
    const a = AGENTS[id], g = new THREE.Vector3(...a.pos);
    const off = new THREE.Vector3(0, 0, a.master ? .5 : 1.5).applyAxisAngle(up, a.rot || 0);
    v.set(g.x + off.x, a.master ? 4.25 : .5, g.z + off.z).project(camera);
    el.style.transform = `translate(-50%, 0) translate(${(v.x * .5 + .5) * w}px, ${(-v.y * .5 + .5) * h}px)`;
    el.style.opacity = v.z < 1 ? '1' : '0';
    el.style.zIndex = String(Math.round((1 - v.z) * 10000));
    el.classList.toggle('compact', Math.abs(Math.atan2(Math.sin(view.theta), Math.cos(view.theta))) > .5 || view.radius > 16);
    el.classList.toggle('hover', hovered === id);
  }
}

function setMood(bot, mood) {
  const u = bot.userData;
  if (u.mood === mood) return;
  u.mood = mood;
  u.face.material.map.dispose();
  u.face.material.map = faceTexture(u.color, mood);
  u.face.material.needsUpdate = true;
}

function frame() {
  raf = 0;
  if (!running) return;
  const dt = Math.min(clock.getDelta(), .05);
  view.t += dt; view.intro += dt / 2.6;
  if (view.auto && !view.drag) view.theta += (Math.sin(view.t * .1) * .5 - view.theta) * .01;
  applyCamera();
  if (parts.globe) { parts.globe.group.rotation.y += dt * .18; parts.globe.wire.rotation.y -= dt * .05; }
  for (const scan of parts.scan) scan.rotation.z -= dt * .35;
  if (parts.dust) {
    const p = parts.dust.geometry.attributes.position, sp = parts.dust.userData.speed;
    for (let i = 0; i < sp.length; i++) { let y = p.array[i * 3 + 1] + sp[i] * dt; if (y > 8) y = 0; p.array[i * 3 + 1] = y; }
    p.needsUpdate = true;
  }
  for (const [id, bot] of Object.entries(parts.robots)) {
    const st = state[id] || 'waiting', u = bot.userData, time = view.t + id.length * 1.7, working = st === 'working';
    setMood(bot, st === 'error' ? 'waiting' : st);
    u.head.rotation.z = working ? Math.sin(time * 3.6) * .06 : Math.sin(time * .7) * .03;
    u.head.rotation.x = working ? .12 + Math.sin(time * 1.3) * .04 : 0;   // cúi nhìn bàn phím khi làm việc
    u.arms.forEach((arm, i) => { arm.rotation.x = -1.0 + (working ? Math.sin(time * 16 + i * Math.PI) * .14 : 0); });
    u.face.scale.y = (time % 4.6) < .1 && st !== 'done' ? .15 : 1;   // chớp mắt
    u.light.color.set(st === 'done' ? 0x4fe3a8 : st === 'waiting' ? u.baseColor.clone().multiplyScalar(.5) : u.baseColor);
    u.tip.visible = !working || Math.sin(time * 10) > -.2;
    if (u.crown) u.crown.rotation.y = time * .4;
    if (u.floatBase !== undefined) bot.position.y = u.floatBase + Math.sin(time * 1.4) * .05;
    if (u.laptop) u.laptop.material.opacity = working ? .65 + Math.sin(time * 9) * .2 : .35;
    const ring = parts.rings[id];
    ring.scale.setScalar(working ? 1 + Math.sin(time * 3) * .05 : 1);
    ring.material.opacity = working ? 1 : st === 'done' ? .65 : .3;
    parts.lights[id].intensity = working ? 3.2 + Math.sin(time * 3) * .8 : st === 'done' ? 1.8 : .8;
    bot.scale.setScalar((u.floatBase !== undefined ? 1.18 : 1) * (hovered === id ? 1.06 : 1));
  }
  for (const [id, link] of Object.entries(parts.links)) {
    const working = state[id] === 'working';
    link.tube.material.color.set(working ? AGENTS[id].color : state[id] === 'done' ? 0x4fe3a8 : 0x2e5aa0);
    link.tex.offset.x -= dt * (working ? 1.2 : .25);
    link.packets.forEach((p, i) => { p.visible = working; if (working) p.position.copy(link.curve.getPointAt((view.t * .3 + i / link.packets.length) % 1)); });
  }
  for (const panel of Object.values(parts.screens)) panel.material.opacity = .86 + Math.sin(view.t * 7 + panel.id) * .04;
  if (composer && !perf.low) composer.render(); else renderer.render(scene, camera);
  placeLabels();
  // máy yếu: tắt hiệu ứng phát sáng, hạ độ phân giải để giữ mượt
  perf.frames++; perf.time += dt;
  if (perf.time > 3) {
    if (!perf.low && perf.frames / perf.time < 28 && view.intro > 1.2) { perf.low = true; renderer.setPixelRatio(1); resize(); }
    perf.frames = 0; perf.time = 0;
  }
  raf = requestAnimationFrame(frame);
}

function pick(e) {
  const r = canvas.getBoundingClientRect();
  pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects(Object.values(parts.robots), true)[0];
  const bot = hit?.object.userData.robot;
  hovered = bot ? Object.keys(parts.robots).find(id => parts.robots[id] === bot) : null;
  canvas.style.cursor = hovered ? 'pointer' : 'grab';
}

export function mount(container, canvasElement, labelElements) {
  host = container; canvas = canvasElement; labels = labelElements;
  try {
    renderer = new THREE.WebGLRenderer({canvas, antialias: true, powerPreference: 'high-performance'});
  } catch (error) {
    return false;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = .9;
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x030817);
  scene.fog = new THREE.FogExp2(0x061230, .024);
  try {
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(renderer), .04).texture;
  } catch (error) { /* không có phản chiếu môi trường vẫn chạy */ }
  camera = new THREE.PerspectiveCamera(40, 16 / 9, .1, 120);
  scene.add(new THREE.HemisphereLight(0x8cc8ff, 0x0a1030, .35));
  const sun = new THREE.DirectionalLight(0xffbe86, .9); sun.position.set(-7, 9, -11); scene.add(sun);
  const fill = new THREE.DirectionalLight(0x8fbfff, .45); fill.position.set(6, 8, 10); scene.add(fill);
  const center = new THREE.PointLight(0x5ab8ff, 5, 14, 1.6); center.position.set(0, 6.5, -2.5); scene.add(center);
  buildRoom(); buildGlobe(); buildAgents(); buildDust();
  try {
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    bloom = new UnrealBloomPass(new THREE.Vector2(640, 360), .7, .45, .88);
    composer.addPass(bloom);
    composer.addPass(new OutputPass());
  } catch (error) {
    composer = null;
  }
  clock = new THREE.Clock();
  canvas.addEventListener('pointerdown', e => { view.drag = {x: e.clientX, y: e.clientY}; canvas.setPointerCapture(e.pointerId); view.intro = Math.max(view.intro, 1); });
  canvas.addEventListener('pointermove', e => {
    if (!view.drag) { pick(e); return; }
    view.theta -= (e.clientX - view.drag.x) * .006;
    view.phi = Math.max(.6, Math.min(1.42, view.phi - (e.clientY - view.drag.y) * .004));
    view.drag = {x: e.clientX, y: e.clientY};
  });
  const end = () => { view.drag = null; };
  canvas.addEventListener('pointerup', end); canvas.addEventListener('pointercancel', end);
  canvas.addEventListener('pointerleave', () => { hovered = null; });
  canvas.addEventListener('wheel', e => { e.preventDefault(); view.radius = Math.max(6.5, Math.min(20, view.radius * (e.deltaY < 0 ? .92 : 1.08))); }, {passive: false});
  new ResizeObserver(resize).observe(host);
  resize();
  return true;
}

export function start() { if (!renderer || running) return; running = true; clock.getDelta(); raf = requestAnimationFrame(frame); }
export function stop() { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }
export function setAuto(on) { view.auto = on; }
export function resetView() { Object.assign(view, HOME); }

// states: {conductor|analyst|writer|studio: waiting/working/done/error}; screens: {id: [{name, pct}]}; masterPct: số %
export function update(states, screens, masterPct) {
  state = states || {};
  if (!renderer) return;
  const refresh = (panel, key, make) => {
    if (!panel || panel.userData.key === key) return;
    panel.userData.key = key;
    panel.material.map?.dispose();
    panel.material.map = make();
    panel.material.needsUpdate = true;
  };
  for (const id of ['analyst', 'writer', 'studio']) {
    const panel = parts.screens[id], lines = screens[id] || [];
    refresh(panel, JSON.stringify(lines), () => screenTexture(AGENTS[id].color, panel.userData.title, lines));
  }
  refresh(parts.screens.conductor1, 'pct' + masterPct, () => canvasTexture(640, 380, (g, w, h) => {
    const bg = g.createLinearGradient(0, 0, 0, h); bg.addColorStop(0, 'rgba(40,110,230,0.38)'); bg.addColorStop(1, 'rgba(10,40,120,0.25)');
    g.fillStyle = bg; g.fillRect(0, 0, w, h); g.fillStyle = 'rgba(255,255,255,0.05)'; for (let y = 0; y < h; y += 4) g.fillRect(0, y, w, 1);
    g.strokeStyle = '#5ab8ff'; g.lineWidth = 5; g.strokeRect(4, 4, w - 8, h - 8);
    g.fillStyle = '#5ab8ff'; g.fillRect(20, 20, w - 40, 44); g.fillStyle = '#04112e'; g.font = 'bold 28px Arial'; g.fillText('TIẾN ĐỘ', 34, 52);
    g.fillStyle = '#bfe8ff'; g.font = 'bold 150px Arial'; g.textAlign = 'center'; g.fillText(masterPct == null ? '—' : masterPct + '%', w / 2, 270);
  }));
}
