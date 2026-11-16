// Phòng điều khiển 3D (Three.js / WebGL) — tầng thượng toà nhà nhìn ra thành phố lúc hoàng hôn.
// Master Agent đội vương miện trên bục giữa, 3 robot agent ngồi bàn với màn hình hologram, quả địa cầu chấm sáng,
// xe bay ngoài vách kính, sàn bóng phản chiếu. Mọi thứ đều chuyển động; bảng số liệu vẽ lại theo dữ liệu thật.
// Dữ liệu tiến độ do app.js đưa vào qua update(); chữ (nhãn, danh sách việc) là HTML đặt theo vị trí 3D chiếu ra màn hình.
import * as THREE from './three.module.min.js';
import {EffectComposer} from './addons/EffectComposer.js';
import {RenderPass} from './addons/RenderPass.js';
import {UnrealBloomPass} from './addons/UnrealBloomPass.js';
import {OutputPass} from './addons/OutputPass.js';
import {RoomEnvironment} from './addons/RoomEnvironment.js';
import {RoundedBoxGeometry} from './addons/RoundedBoxGeometry.js';
import {Reflector} from './addons/Reflector.js';

const AGENTS = {
  conductor: {color: 0x5ab8ff, trim: 0xffc83d, pos: [0, 0, -1.9], master: true},
  analyst: {color: 0xffb938, pos: [-4.6, 0, 1.35], rot: 0.6, title: 'LIVE · PHÂN TÍCH', desk: 'thumbs'},
  writer: {color: 0xb07cff, pos: [0, 0, 2.75], rot: 0, title: 'LIVE · VIẾT PROMPT', desk: 'code'},
  studio: {color: 0x34e0a8, pos: [4.6, 0, 1.35], rot: -0.6, title: 'LIVE · STUDIO MUSE', desk: 'video'},
};
const HOME = {theta: 0, phi: 1.2, radius: 12.8, tx: 0, ty: 2.75, tz: .7};
const ROOM_R = 19;

let renderer, composer, bloom, scene, camera, canvas, host, labels = {}, raf = 0, clock, running = false, mirror = null;
const view = {...HOME, target: new THREE.Vector3(HOME.tx, HOME.ty, HOME.tz), goal: null, auto: true, drag: null, t: 0, intro: 0};
const parts = {robots: {}, links: {}, rings: {}, lights: {}, globe: null, dust: null, scan: [], vehicles: [], beacons: [],
  panels: [], pulses: []};
const perf = {frames: 0, time: 0, low: false};
let state = {}, screens = {}, masterPct = null;
const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
let hovered = null;

// ---------- tiện ích ----------
function canvasTexture(width, height, draw, repeat) {
  const c = document.createElement('canvas');
  c.width = width; c.height = height;
  if (draw) draw(c.getContext('2d'), width, height);
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  if (repeat) { texture.wrapS = texture.wrapT = THREE.RepeatWrapping; texture.repeat.set(...repeat); }
  return texture;
}
const hexOf = color => '#' + new THREE.Color(color).getHexString();
const rgba = (color, a) => { const c = new THREE.Color(color); return `rgba(${c.r * 255 | 0},${c.g * 255 | 0},${c.b * 255 | 0},${a})`; };
function glow(color, opacity = 1) {
  return new THREE.MeshBasicMaterial({color, transparent: opacity < 1, opacity, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false});
}
function softDot() {
  return canvasTexture(64, 64, (g, w, h) => {
    const r = g.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
    r.addColorStop(0, 'rgba(255,255,255,1)'); r.addColorStop(.35, 'rgba(200,230,255,.55)'); r.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = r; g.fillRect(0, 0, w, h);
  });
}
const rand = (() => { let s = 7; return () => { s = (s * 16807) % 2147483647; return (s - 1) / 2147483646; }; })();

// ---------- bầu trời + thành phố bên ngoài ----------
function buildSky() {
  const sky = new THREE.Mesh(new THREE.SphereGeometry(90, 48, 24), new THREE.MeshBasicMaterial({side: THREE.BackSide, fog: false, toneMapped: false,
    map: canvasTexture(16, 512, (g, w, h) => {
      const grad = g.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, '#050a24'); grad.addColorStop(.3, '#141a4d'); grad.addColorStop(.42, '#3a2f78');
      grad.addColorStop(.48, '#8a4f9a'); grad.addColorStop(.52, '#f08a6a'); grad.addColorStop(.56, '#2a2a5a'); grad.addColorStop(1, '#070a1a');
      g.fillStyle = grad; g.fillRect(0, 0, w, h);
    })}));
  scene.add(sky);
  parts.sky = {mesh: sky, maps: {default: sky.material.map}};
  // sao mờ
  const stars = [];
  for (let i = 0; i < 400; i++) { const a = rand() * Math.PI * 2, y = 20 + rand() * 60, r = 85; stars.push(Math.cos(a) * r, y, Math.sin(a) * r); }
  const sg = new THREE.BufferGeometry(); sg.setAttribute('position', new THREE.Float32BufferAttribute(stars, 3));
  scene.add(new THREE.Points(sg, new THREE.PointsMaterial({color: 0xbfd4ff, size: .35, map: softDot(), transparent: true, opacity: .7, fog: false, depthWrite: false})));
}

function towerTexture(seed, warm) {
  return canvasTexture(128, 256, (g, w, h) => {
    g.fillStyle = '#0b1230'; g.fillRect(0, 0, w, h);
    for (let y = 6; y < h - 4; y += 9) for (let x = 5; x < w - 4; x += 8) {
      const v = Math.sin(x * 12.9 + y * 78.2 + seed * 37.7) * 43758.5; const lit = v - Math.floor(v);
      if (lit > .45) { g.fillStyle = lit > .9 ? '#fff4d6' : warm ? `rgba(255,${180 + (lit * 60 | 0)},120,${.5 + lit * .5})` : `rgba(140,${190 + (lit * 50 | 0)},255,${.45 + lit * .5})`; g.fillRect(x, y, 5, 5); }
    }
    g.fillStyle = 'rgba(120,180,255,.25)'; g.fillRect(0, 0, 3, h); g.fillRect(w - 3, 0, 3, h);
  });
}

function buildCity() {
  const textures = Array.from({length: 6}, (_, i) => towerTexture(i + 1, i % 3 === 0));
  for (let i = 0; i < 120; i++) {
    const a = rand() * Math.PI * 2, r = 34 + rand() * 52;
    const w = 3 + rand() * 5, d = 3 + rand() * 5, h = 14 + rand() * (r > 60 ? 30 : 22) + (rand() > .9 ? 16 : 0);
    const tex = textures[i % textures.length].clone(); tex.needsUpdate = true; tex.repeat.set(Math.max(1, w / 3), Math.max(1, h / 10));
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    const mat = new THREE.MeshBasicMaterial({map: tex, color: new THREE.Color().setHSL(.62, .3, .55 + rand() * .25), fog: true, toneMapped: false});
    const tower = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
    tower.position.set(Math.cos(a) * r, h / 2 - 30, Math.sin(a) * r); tower.rotation.y = rand() * Math.PI; scene.add(tower);
    if (rand() > .55) {  // đèn đỏ nhấp nháy trên nóc
      const b = new THREE.Mesh(new THREE.SphereGeometry(.18, 8, 6), glow(0xff3344, 1));
      b.position.set(tower.position.x, h - 30 + .4, tower.position.z); b.scale.setScalar(2); scene.add(b); parts.beacons.push({mesh: b, phase: rand() * 6});
    }
    if (rand() > .8) {  // ăng-ten
      const ant = new THREE.Mesh(new THREE.CylinderGeometry(.05, .08, 3, 6), new THREE.MeshBasicMaterial({color: 0x1a2240}));
      ant.position.set(tower.position.x, h - 30 + 1.5, tower.position.z); scene.add(ant);
    }
  }
  // lớp sương thành phố + mặt đất xa
  const haze = new THREE.Mesh(new THREE.CylinderGeometry(70, 70, 10, 64, 1, true), new THREE.MeshBasicMaterial({side: THREE.BackSide, transparent: true, fog: false, depthWrite: false,
    map: canvasTexture(8, 128, (g, w, h) => { const grad = g.createLinearGradient(0, 0, 0, h); grad.addColorStop(0, 'rgba(255,140,120,0)'); grad.addColorStop(.7, 'rgba(255,140,120,.25)'); grad.addColorStop(1, 'rgba(60,40,120,.6)'); g.fillStyle = grad; g.fillRect(0, 0, w, h); })}));
  haze.position.y = -18; haze.scale.set(1.3, 3, 1.3); scene.add(haze);
  const ground = new THREE.Mesh(new THREE.CircleGeometry(90, 48), new THREE.MeshBasicMaterial({color: 0x070b1e, fog: false}));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -30; scene.add(ground);
}

function vehicle(color) {
  const group = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(.32, 1.1, 6, 16), new THREE.MeshStandardMaterial({color: 0xd9e2f2, metalness: .7, roughness: .25}));
  body.rotation.z = Math.PI / 2; group.add(body);
  const canopy = new THREE.Mesh(new THREE.SphereGeometry(.3, 16, 12, 0, Math.PI * 2, 0, Math.PI / 2), new THREE.MeshBasicMaterial({color: 0x7fd6ff, toneMapped: false}));
  canopy.position.set(.15, .18, 0); group.add(canopy);
  const under = new THREE.Mesh(new THREE.CircleGeometry(.55, 24), glow(color, .35)); under.rotation.x = Math.PI / 2; under.position.y = -.33; group.add(under);
  const tail = new THREE.Mesh(new THREE.SphereGeometry(.09, 8, 6), glow(0xff3355, 1)); tail.position.x = -.85; group.add(tail);
  const head = new THREE.Mesh(new THREE.SphereGeometry(.06, 8, 6), glow(0xfff2c8, .8)); head.position.x = .85; group.add(head);
  const trail = new THREE.Mesh(new THREE.ConeGeometry(.16, 2.4, 10, 1, true), glow(color, .25)); trail.rotation.z = Math.PI / 2; trail.position.x = -2; group.add(trail);
  group.userData = {tail};
  return group;
}

function buildVehicles() {
  const colors = [0x7fd6ff, 0xb28cff, 0xffb36b, 0x34e0a8];
  for (let i = 0; i < 14; i++) {
    const v = vehicle(colors[i % colors.length]);
    const r = 24 + rand() * 24, y = -6 + rand() * 16, speed = (.025 + rand() * .05) * (rand() > .5 ? 1 : -1);
    v.userData = {...v.userData, r, y, speed, phase: rand() * Math.PI * 2, wobble: rand() * 2, scale: .7 + rand() * .6};
    v.scale.setScalar(v.userData.scale);
    scene.add(v); parts.vehicles.push(v);
  }
}

// ---------- phòng: vách kính, trần, sàn bóng, chậu cây ----------
function buildRoom() {
  // sàn bóng phản chiếu (tắt khi máy yếu)
  mirror = new Reflector(new THREE.CircleGeometry(ROOM_R + .5, 96), {clipBias: .003, textureWidth: 1024, textureHeight: 1024, color: 0x8a96bb});
  mirror.rotation.x = -Math.PI / 2; scene.add(mirror);
  const tint = new THREE.Mesh(new THREE.CircleGeometry(ROOM_R + .5, 96), new THREE.MeshPhysicalMaterial({color: 0x0b1636, metalness: .5, roughness: .32, clearcoat: .8, clearcoatRoughness: .28, envMapIntensity: .8, transparent: true, opacity: .5,
    map: canvasTexture(1024, 1024, (g, w, h) => {
      g.fillStyle = '#ffffff'; g.fillRect(0, 0, w, h);
      g.strokeStyle = 'rgba(30,60,120,.35)'; g.lineWidth = 3;
      for (let i = 0; i < 24; i++) { g.beginPath(); g.moveTo(w / 2, h / 2); g.lineTo(w / 2 + Math.cos(i / 24 * Math.PI * 2) * w, h / 2 + Math.sin(i / 24 * Math.PI * 2) * h); g.stroke(); }
      for (let r = 60; r < w / 2; r += 70) { g.beginPath(); g.arc(w / 2, h / 2, r, 0, Math.PI * 2); g.stroke(); }
    })}));
  tint.rotation.x = -Math.PI / 2; tint.position.y = .005; scene.add(tint);
  parts.tint = tint;
  for (const [r, w, c, o] of [[2.6, .05, 0x7fd6ff, .95], [3.7, .035, 0x9a7bff, .7], [6.6, .055, 0x7fd6ff, .7], [9.8, .06, 0xb28cff, .55], [12.8, .05, 0x4fb7ff, .45], [16.2, .06, 0x9a7bff, .4]]) {
    const ring = new THREE.Mesh(new THREE.RingGeometry(r - w, r + w, 180), glow(c, o)); ring.rotation.x = -Math.PI / 2; ring.position.y = .015; scene.add(ring);
  }
  const scan = new THREE.Mesh(new THREE.RingGeometry(2.7, 12.7, 128, 1, 0, Math.PI / 7), glow(0x4fb7ff, .1));
  scan.rotation.x = -Math.PI / 2; scan.position.y = .018; scene.add(scan); parts.scan.push(scan);
  // vách kính: khung đứng + gờ, kính mờ rất nhẹ
  const frameMat = new THREE.MeshStandardMaterial({color: 0x3a4a72, metalness: .9, roughness: .25});
  for (let i = 0; i < 32; i++) {
    const a = i / 32 * Math.PI * 2, mull = new THREE.Mesh(new THREE.BoxGeometry(.16, 9, .25), frameMat);
    mull.position.set(Math.sin(a) * ROOM_R, 4.5, Math.cos(a) * ROOM_R); mull.lookAt(0, 4.5, 0); scene.add(mull);
  }
  for (const y of [.05, 1.0, 8.6]) {
    const rail = new THREE.Mesh(new THREE.TorusGeometry(ROOM_R, y > 8 ? .35 : .12, 10, 220), frameMat); rail.rotation.x = Math.PI / 2; rail.position.y = y; scene.add(rail);
  }
  const strip = new THREE.Mesh(new THREE.TorusGeometry(ROOM_R - .3, .04, 8, 220), glow(0x4fb7ff, 1)); strip.rotation.x = Math.PI / 2; strip.position.y = .25; scene.add(strip);
  const glass = new THREE.Mesh(new THREE.CylinderGeometry(ROOM_R - .05, ROOM_R - .05, 8.6, 128, 1, true), new THREE.MeshPhysicalMaterial({color: 0x8fb4ff, transparent: true, opacity: .06, roughness: .05, metalness: 0, side: THREE.DoubleSide, depthWrite: false}));
  glass.position.y = 4.3; scene.add(glass);
  // trần: đĩa trần tối + vòng đèn + đèn âm trần
  const ceiling = new THREE.Mesh(new THREE.RingGeometry(3.6, ROOM_R + 1, 96), new THREE.MeshStandardMaterial({color: 0x081129, metalness: .5, roughness: .5, side: THREE.FrontSide}));  // chỉ thấy từ dưới lên: camera kéo xa vẫn nhìn xuyên được
  ceiling.rotation.x = Math.PI / 2; ceiling.position.y = 9.4; scene.add(ceiling);
  for (const [r, y, c, t] of [[3.6, 9.3, 0x7fd6ff, .09], [6.2, 9.2, 0xffffff, .06], [9.4, 9.25, 0x7fd6ff, .09], [13, 9.15, 0xb28cff, .08], [16.5, 9.1, 0xffb36b, .07]]) {
    const torus = new THREE.Mesh(new THREE.TorusGeometry(r, t, 12, 220), glow(c, 1)); torus.rotation.x = Math.PI / 2; torus.position.y = y; scene.add(torus);
  }
  for (let i = 0; i < 24; i++) {
    const a = i / 24 * Math.PI * 2, spot = new THREE.Mesh(new THREE.CircleGeometry(.14, 16), glow(0xfff1d6, 1));
    spot.rotation.x = Math.PI / 2; spot.position.set(Math.sin(a) * 11, 9.32, Math.cos(a) * 11); scene.add(spot);
  }
  // chậu cây cảnh lớn
  const pot = new THREE.MeshPhysicalMaterial({color: 0x8e9ab2, metalness: .75, roughness: .32, clearcoat: .5, envMapIntensity: .22});
  const leafTex = canvasTexture(64, 256, (g, w, h) => {
    const grad = g.createLinearGradient(0, 0, w, 0); grad.addColorStop(0, '#1d6b3c'); grad.addColorStop(.5, '#3fbf6a'); grad.addColorStop(1, '#1d6b3c');
    g.fillStyle = grad; g.beginPath(); g.moveTo(w / 2, 0); g.quadraticCurveTo(w, h * .4, w / 2, h); g.quadraticCurveTo(0, h * .4, w / 2, 0); g.fill();
    g.strokeStyle = 'rgba(20,70,40,.8)'; g.lineWidth = 2; g.beginPath(); g.moveTo(w / 2, 4); g.lineTo(w / 2, h - 4); g.stroke();
  });
  const leafMat = new THREE.MeshStandardMaterial({map: leafTex, transparent: true, alphaTest: .4, side: THREE.DoubleSide, roughness: .7});
  for (const [x, z] of [[-9.6, -3.4], [9.6, -3.4], [-12.6, 4.2], [12.6, 4.2], [-15.5, -6], [15.5, -6]]) {
    const bowl = new THREE.Mesh(new THREE.LatheGeometry([0, .6, .9, 1.0, .95].map((r, i) => new THREE.Vector2(r, i * .3)), 32), pot);
    bowl.position.set(x, 0, z); scene.add(bowl);
    const soil = new THREE.Mesh(new THREE.CircleGeometry(.92, 24), new THREE.MeshStandardMaterial({color: 0x120c08, roughness: 1})); soil.rotation.x = -Math.PI / 2; soil.position.set(x, 1.15, z); scene.add(soil);
    const plant = new THREE.Group(); plant.position.set(x, 1.15, z); scene.add(plant);
    for (let k = 0; k < 22; k++) {
      const leaf = new THREE.Mesh(new THREE.PlaneGeometry(.5, 2.3), leafMat);
      leaf.geometry.translate(0, .95, 0);
      leaf.rotation.y = k / 14 * Math.PI * 2 + rand(); leaf.rotation.x = .35 + rand() * .55; leaf.scale.setScalar(.8 + rand() * .5);
      plant.add(leaf);
    }
    plant.userData.sway = rand() * 6; parts.pulses.push({plant});
  }
}

// ---------- quả địa cầu ----------
function buildGlobe() {
  const group = new THREE.Group();
  group.position.set(0, 6.55, -3.4); group.scale.setScalar(.95);
  group.add(new THREE.Mesh(new THREE.SphereGeometry(2.55, 64, 48), new THREE.MeshPhysicalMaterial({color: 0x0f3a9a, transparent: true, opacity: .55, roughness: .1, metalness: .1, clearcoat: 1, emissive: 0x0a2a7a, emissiveIntensity: .8, depthWrite: false})));
  group.add(new THREE.Mesh(new THREE.SphereGeometry(2.62, 48, 32), glow(0x3aa5ff, .12)));
  const wire = new THREE.Mesh(new THREE.SphereGeometry(2.6, 36, 24), new THREE.MeshBasicMaterial({color: 0x8fdcff, wireframe: true, transparent: true, opacity: .14, blending: THREE.AdditiveBlending, depthWrite: false}));
  group.add(wire);
  // lục địa dạng chấm (vùng gần đúng: Á-Âu, Phi, Bắc/Nam Mỹ, Úc)
  const blobs = [[50, 80, 32, 60], [10, 20, 22, 18], [5, 105, 12, 25], [45, -100, 22, 30], [-15, -60, 22, 15], [-25, 135, 10, 15], [60, 40, 12, 40]];
  const pts = [];
  for (let lat = -80; lat <= 80; lat += 2.4) for (let lon = -180; lon < 180; lon += 2.4) {
    let inside = false;
    for (const [la, lo, rla, rlo] of blobs) { const dl = (lat - la) / rla, dn = ((lon - lo + 540) % 360 - 180) / rlo; if (dl * dl + dn * dn < 1 + Math.sin(lat * .3 + lon * .2) * .25) inside = true; }
    if (!inside) continue;
    const phi = (90 - lat) * Math.PI / 180, th = (lon + 180) * Math.PI / 180, r = 2.63;
    pts.push(-r * Math.sin(phi) * Math.cos(th), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(th));
  }
  const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
  group.add(new THREE.Points(geo, new THREE.PointsMaterial({color: 0xc9f2ff, size: .07, map: softDot(), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false})));
  const orbits = [];
  for (const [r, tx, tz, c, sp] of [[3.3, 1.3, .25, 0xb28cff, .5], [3.7, 1.75, -.3, 0x7fd6ff, -.35], [4.1, 1.5, .1, 0x9a7bff, .25]]) {
    const pivot = new THREE.Group(); pivot.rotation.set(tx, 0, tz); group.add(pivot);
    pivot.add(new THREE.Mesh(new THREE.TorusGeometry(r, .02, 8, 220), glow(c, .9)));
    const sat = new THREE.Mesh(new THREE.SphereGeometry(.08, 12, 8), glow(0xffffff, 1)); sat.position.x = r; pivot.add(sat);
    orbits.push({pivot, sp});
  }
  scene.add(group);
  parts.globe = {group, wire, orbits};
}

// ---------- robot chibi ----------
function faceTexture(color, mood, blink) {
  return canvasTexture(320, 208, (g, w, h) => {
    const bg = g.createRadialGradient(w / 2, h * .45, 10, w / 2, h / 2, w * .7); bg.addColorStop(0, '#0d1a3c'); bg.addColorStop(1, '#02050e');
    g.fillStyle = bg; g.fillRect(0, 0, w, h);
    g.fillStyle = 'rgba(255,255,255,.07)'; g.beginPath(); g.ellipse(w * .32, h * .2, w * .28, h * .09, -.15, 0, Math.PI * 2); g.fill();  // vệt bóng kính
    const c = mood === 'error' ? '#ff5d6c' : mood === 'done' ? '#5cf2b8' : mood === 'waiting' ? rgba(color, .75) : '#8ff3ff';
    g.strokeStyle = c; g.fillStyle = c; g.lineCap = 'round'; g.shadowColor = c; g.shadowBlur = 26;
    for (const x of [100, 220]) {
      g.lineWidth = 17; g.beginPath();
      if (mood === 'error') { g.moveTo(x - 22, 86); g.lineTo(x + 22, 122); g.moveTo(x + 22, 86); g.lineTo(x - 22, 122); }   // mắt X
      else if (blink || mood === 'waiting') { g.moveTo(x - 28, 108); g.lineTo(x + 28, 108); }                                 // nhắm / lim dim
      else g.arc(x, 122, 32, Math.PI * 1.13, Math.PI * 1.87);                                                                  // mắt cười ^ ^
      g.stroke();
    }
    g.shadowBlur = 0; g.fillStyle = mood === 'error' ? 'rgba(255,80,100,.25)' : 'rgba(255,120,175,.5)';
    g.beginPath(); g.ellipse(64, 156, 20, 10, 0, 0, Math.PI * 2); g.fill(); g.beginPath(); g.ellipse(256, 156, 20, 10, 0, 0, Math.PI * 2); g.fill();
    g.strokeStyle = c; g.lineWidth = 7; g.shadowBlur = 14; g.beginPath();
    if (mood === 'error') { g.arc(160, 172, 14, Math.PI * 1.15, Math.PI * 1.85); }
    else g.arc(160, 150, mood === 'waiting' ? 9 : 18, Math.PI * .15, Math.PI * .85);
    g.stroke();
  });
}

// Thân robot dạng quả trứng (tiện tròn) cho vỏ bo cong mềm
function shellGeometry(points, scaleZ = .82, segments = 48) {
  const geo = new THREE.LatheGeometry(points.map(([x, y]) => new THREE.Vector2(x, y)), segments);
  geo.scale(1, 1, scaleZ);
  geo.computeVertexNormals();
  return geo;
}

function robot(color, master, trim) {
  const accentColor = trim || color;
  const shell = new THREE.MeshPhysicalMaterial({color: 0xf3f6fb, metalness: 0, roughness: .23, clearcoat: 1, clearcoatRoughness: .05, envMapIntensity: .45});
  const silver = new THREE.MeshPhysicalMaterial({color: 0xc3cede, metalness: .85, roughness: .22, clearcoat: .7, envMapIntensity: .6});
  const darkJoint = new THREE.MeshPhysicalMaterial({color: 0x2a3550, metalness: .7, roughness: .3, clearcoat: .5, envMapIntensity: .5});
  const accent = new THREE.MeshPhysicalMaterial({color: accentColor, metalness: .85, roughness: .22, clearcoat: 1, emissive: accentColor, emissiveIntensity: .22, envMapIntensity: .6});
  const glass = new THREE.MeshPhysicalMaterial({color: 0x02050d, metalness: .4, roughness: .03, clearcoat: 1, envMapIntensity: 1.1});
  const light = new THREE.MeshBasicMaterial({color, toneMapped: false});
  const root = new THREE.Group();
  // thân: vỏ trứng + tấm ngực + đai eo
  const torso = new THREE.Mesh(shellGeometry([[0, 0], [.2, .01], [.3, .06], [.37, .2], [.39, .36], [.36, .52], [.27, .63], [.12, .69], [0, .7]]), shell);
  torso.position.y = .62; root.add(torso);
  const chestPlate = new THREE.Mesh(new THREE.CylinderGeometry(.17, .17, .05, 40), silver); chestPlate.rotation.x = Math.PI / 2; chestPlate.position.set(0, 1.0, .29); root.add(chestPlate);
  const chestRing = new THREE.Mesh(new THREE.TorusGeometry(.14, .022, 12, 48), accent); chestRing.position.set(0, 1.0, .318); root.add(chestRing);
  const chest = new THREE.Mesh(new THREE.CircleGeometry(.1, 40), light); chest.position.set(0, 1.0, .32); root.add(chest);
  const waist = new THREE.Mesh(new THREE.TorusGeometry(.3, .035, 10, 48), accent); waist.rotation.x = Math.PI / 2; waist.scale.set(1, .82, 1); waist.position.y = .68; root.add(waist);
  const hip = new THREE.Mesh(shellGeometry([[0, 0], [.22, .02], [.28, .1], [.26, .18], [0, .2]], .82, 32), silver); hip.position.y = .5; root.add(hip);
  for (const side of [-1, 1]) {  // các vạch vỏ tạo chi tiết cơ khí
    const seam = new THREE.Mesh(new THREE.TorusGeometry(.37, .006, 6, 48, Math.PI * .5), darkJoint);
    seam.rotation.set(0, side * Math.PI / 2, Math.PI / 2); seam.position.set(side * .02, .98, 0); root.add(seam);
  }
  // cổ + đầu
  const neck = new THREE.Mesh(new THREE.CylinderGeometry(.11, .14, .16, 24), darkJoint); neck.position.y = 1.38; root.add(neck);
  const neckRing = new THREE.Mesh(new THREE.TorusGeometry(.13, .02, 8, 32), accent); neckRing.rotation.x = Math.PI / 2; neckRing.position.y = 1.33; root.add(neckRing);
  const head = new THREE.Group(); head.position.y = 1.92; root.add(head);
  head.add(new THREE.Mesh(new RoundedBoxGeometry(1.26, 1.0, .98, 10, .44), shell));
  const back = new THREE.Mesh(new RoundedBoxGeometry(.9, .7, .2, 6, .1), silver); back.position.z = -.46; head.add(back);
  const bezel = new THREE.Mesh(new RoundedBoxGeometry(1.06, .74, .08, 8, .26), darkJoint); bezel.position.z = .455; head.add(bezel);
  const visor = new THREE.Mesh(new RoundedBoxGeometry(.98, .66, .08, 8, .23), glass); visor.position.z = .485; head.add(visor);
  const face = new THREE.Mesh(new THREE.PlaneGeometry(.9, .58), new THREE.MeshBasicMaterial({map: faceTexture(color, 'working'), toneMapped: false, transparent: true}));
  face.position.z = .528; head.add(face);
  for (const x of [-.66, .66]) {  // tai nghe nhiều lớp
    const cup = new THREE.Mesh(new THREE.CylinderGeometry(.25, .25, .14, 40), accent); cup.rotation.z = Math.PI / 2; cup.position.x = x; head.add(cup);
    const pad = new THREE.Mesh(new THREE.CylinderGeometry(.18, .18, .16, 32), silver); pad.rotation.z = Math.PI / 2; pad.position.x = x * 1.04; head.add(pad);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(.14, .028, 10, 40), light); ring.rotation.y = Math.PI / 2; ring.position.x = x * 1.13; head.add(ring);
    const dot = new THREE.Mesh(new THREE.CircleGeometry(.06, 24), light); dot.rotation.y = Math.sign(x) * Math.PI / 2; dot.position.x = x * 1.135; head.add(dot);
  }
  const band = new THREE.Mesh(new THREE.TorusGeometry(.68, .05, 12, 48, Math.PI), silver); band.position.y = .04; head.add(band);
  let tip = null;
  if (!master) {  // ăng-ten
    const antenna = new THREE.Mesh(new THREE.CylinderGeometry(.02, .025, .24, 10), silver); antenna.position.y = .62; head.add(antenna);
    tip = new THREE.Mesh(new THREE.SphereGeometry(.065, 20, 14), light); tip.position.y = .77; head.add(tip);
  }
  // tay: vai → khuỷu → bàn tay 3 ngón
  const arms = [];
  for (const side of [-1, 1]) {
    const shoulder = new THREE.Group(); shoulder.position.set(side * .42, 1.16, 0); root.add(shoulder);
    shoulder.add(new THREE.Mesh(new THREE.SphereGeometry(.13, 24, 18), accent));
    const upper = new THREE.Mesh(new THREE.CapsuleGeometry(.085, .16, 8, 18), shell); upper.position.y = -.17; shoulder.add(upper);
    const elbow = new THREE.Group(); elbow.position.y = -.32; shoulder.add(elbow);
    elbow.add(new THREE.Mesh(new THREE.SphereGeometry(.085, 20, 14), darkJoint));
    const fore = new THREE.Mesh(new THREE.CapsuleGeometry(.095, .16, 8, 18), shell); fore.position.y = -.16; elbow.add(fore);
    const cuff = new THREE.Mesh(new THREE.TorusGeometry(.085, .018, 8, 24), accent); cuff.rotation.x = Math.PI / 2; cuff.position.y = -.27; elbow.add(cuff);
    const hand = new THREE.Group(); hand.position.y = -.36; elbow.add(hand);
    hand.add(new THREE.Mesh(new THREE.SphereGeometry(.095, 20, 14), silver));
    for (let f = 0; f < 3; f++) {
      const finger = new THREE.Mesh(new THREE.CapsuleGeometry(.026, .07, 4, 8), silver);
      finger.position.set((f - 1) * .045, -.1, .03); finger.rotation.x = .25; hand.add(finger);
    }
    const thumb = new THREE.Mesh(new THREE.CapsuleGeometry(.03, .09, 4, 8), silver);
    thumb.position.set(-side * .07, -.02, .07); thumb.rotation.set(1.1, 0, side * .4); hand.add(thumb);
    arms.push({shoulder, elbow, hand, thumb, side});
  }
  // Master đứng: chân + bàn chân
  if (master) {
    for (const side of [-1, 1]) {
      const leg = new THREE.Mesh(new THREE.CapsuleGeometry(.1, .22, 8, 16), shell); leg.position.set(side * .16, .3, 0); root.add(leg);
      const knee = new THREE.Mesh(new THREE.TorusGeometry(.1, .02, 8, 24), accent); knee.rotation.x = Math.PI / 2; knee.position.set(side * .16, .34, 0); root.add(knee);
      const foot = new THREE.Mesh(new RoundedBoxGeometry(.22, .1, .32, 4, .05), silver); foot.position.set(side * .16, .06, .04); root.add(foot);
    }
  }
  let crown = null;
  if (master) {
    const gold = new THREE.MeshPhysicalMaterial({color: 0xffc83d, metalness: 1, roughness: .15, clearcoat: 1, emissive: 0x5a3400, emissiveIntensity: .6});
    crown = new THREE.Group(); crown.position.y = .58; head.add(crown);
    crown.add(new THREE.Mesh(new THREE.CylinderGeometry(.34, .37, .17, 48, 1, true), gold));
    const band2 = new THREE.Mesh(new THREE.TorusGeometry(.36, .025, 8, 48), gold); band2.rotation.x = Math.PI / 2; band2.position.y = -.07; crown.add(band2);
    for (let i = 0; i < 5; i++) {
      const a = i / 5 * Math.PI * 2, spike = new THREE.Mesh(new THREE.ConeGeometry(.085, .3, 16), gold);
      spike.position.set(Math.sin(a) * .34, .22, Math.cos(a) * .34); crown.add(spike);
      const ball = new THREE.Mesh(new THREE.SphereGeometry(.045, 14, 10), gold); ball.position.set(Math.sin(a) * .34, .39, Math.cos(a) * .34); crown.add(ball);
      const gem = new THREE.Mesh(new THREE.OctahedronGeometry(.055), new THREE.MeshBasicMaterial({color: [0xff4f6d, 0x5ab8ff, 0x34e0a8][i % 3], toneMapped: false}));
      gem.position.set(Math.sin(a) * .375, 0, Math.cos(a) * .375); crown.add(gem);
    }
  }
  root.userData = {head, arms, light, face, tip, crown, mood: '', blink: false, color, baseColor: new THREE.Color(color), master};
  root.traverse(o => { if (o.isMesh) o.userData.robot = root; });
  return root;
}

function chair(color) {
  const group = new THREE.Group();
  const dark = new THREE.MeshPhysicalMaterial({color: 0x1b2238, metalness: .3, roughness: .38, clearcoat: .8, envMapIntensity: .45});
  const metal = new THREE.MeshStandardMaterial({color: 0xb9c6dc, metalness: .9, roughness: .2});
  const seat = new THREE.Mesh(new RoundedBoxGeometry(1.05, .2, .95, 5, .09), dark); seat.position.y = .5; group.add(seat);
  const back = new THREE.Mesh(new RoundedBoxGeometry(1.05, 1.35, .22, 5, .11), dark); back.position.set(0, 1.22, -.5); back.rotation.x = -.12; group.add(back);
  const headrest = new THREE.Mesh(new RoundedBoxGeometry(.7, .32, .2, 4, .09), dark); headrest.position.set(0, 2.02, -.6); headrest.rotation.x = -.12; group.add(headrest);
  for (const x of [-.6, .6]) {
    const wing = new THREE.Mesh(new RoundedBoxGeometry(.14, 1.0, .3, 3, .06), dark); wing.position.set(x * .92, 1.25, -.42); wing.rotation.set(-.12, x > 0 ? -.35 : .35, 0); group.add(wing);
    const armrest = new THREE.Mesh(new RoundedBoxGeometry(.13, .1, .72, 3, .04), metal); armrest.position.set(x, .8, -.05); group.add(armrest);
    const post = new THREE.Mesh(new THREE.CylinderGeometry(.03, .03, .28, 8), metal); post.position.set(x, .65, -.1); group.add(post);
  }
  for (const x of [-.42, .42]) { const trim = new THREE.Mesh(new THREE.BoxGeometry(.03, 1.1, .02), glow(color, 1)); trim.position.set(x, 1.22, -.37); trim.rotation.x = -.12; group.add(trim); }
  const stem = new THREE.Mesh(new THREE.CylinderGeometry(.06, .06, .42, 12), metal); stem.position.y = .25; group.add(stem);
  for (let i = 0; i < 5; i++) {
    const a = i / 5 * Math.PI * 2, spoke = new THREE.Mesh(new THREE.BoxGeometry(.5, .04, .06), metal);
    spoke.position.set(Math.cos(a) * .22, .05, Math.sin(a) * .22); spoke.rotation.y = -a; group.add(spoke);
  }
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
  const top = new THREE.MeshPhysicalMaterial({color: 0xe9eef7, metalness: .55, roughness: .16, clearcoat: 1, envMapIntensity: .7});
  const side = new THREE.MeshPhysicalMaterial({color: 0x1a2546, metalness: .75, roughness: .2, clearcoat: 1, envMapIntensity: .8});
  const chrome = new THREE.MeshPhysicalMaterial({color: 0xd5deec, metalness: 1, roughness: .12, clearcoat: 1, envMapIntensity: .9});
  const half = .82;
  const plate = new THREE.Mesh(new THREE.ExtrudeGeometry(arcShape(.9, 1.75, half), {depth: .12, bevelEnabled: true, bevelThickness: .04, bevelSize: .04, bevelSegments: 4, curveSegments: 64}), top);
  plate.rotation.x = Math.PI / 2; plate.position.y = 1.16; group.add(plate);
  const lip = new THREE.Mesh(new THREE.TorusGeometry(1.79, .04, 10, 72, half * 2), chrome); lip.rotation.x = -Math.PI / 2; lip.rotation.z = Math.PI / 2 - half; lip.position.y = 1.1; group.add(lip);
  const front = new THREE.Mesh(new THREE.CylinderGeometry(1.72, 1.45, 1.04, 72, 1, true, -half, half * 2), side); front.position.y = .56; group.add(front);
  const inner = new THREE.Mesh(new THREE.CylinderGeometry(.92, .92, 1.04, 48, 1, true, -half, half * 2), side); inner.position.y = .56; group.add(inner);
  for (const [r, y] of [[1.72, .96], [1.5, .16]]) {
    const trim = new THREE.Mesh(new THREE.TorusGeometry(r, .022, 8, 72, half * 2), glow(color, 1));
    trim.rotation.x = -Math.PI / 2; trim.rotation.z = Math.PI / 2 - half; trim.position.y = y; group.add(trim);
  }
  const base = new THREE.Mesh(new THREE.TorusGeometry(1.48, .06, 10, 72, half * 2), chrome); base.rotation.x = -Math.PI / 2; base.rotation.z = Math.PI / 2 - half; base.position.y = .04; group.add(base);
  const keys = new THREE.Mesh(new THREE.PlaneGeometry(.74, .24), new THREE.MeshBasicMaterial({map: canvasTexture(150, 48, (g, w, h) => {
    g.fillStyle = '#0a1430'; g.fillRect(0, 0, w, h); g.fillStyle = hexOf(color);
    for (let y = 5; y < h - 5; y += 10) for (let x = 5; x < w - 5; x += 11) g.fillRect(x, y, 8, 7);
  }), toneMapped: false}));
  keys.rotation.x = -Math.PI / 2; keys.position.set(0, 1.335, 1.1); group.add(keys);
  return group;
}

// ---------- bảng hologram động ----------
class Panel {
  constructor(width, height, color, kind, title, extra = {}) {
    this.color = color; this.kind = kind; this.title = title; this.extra = extra; this.seed = rand() * 100;
    this.canvas = document.createElement('canvas'); this.canvas.width = 512; this.canvas.height = Math.round(512 * height / width);
    this.g = this.canvas.getContext('2d');
    this.texture = new THREE.CanvasTexture(this.canvas); this.texture.colorSpace = THREE.SRGBColorSpace;
    this.mesh = new THREE.Mesh(extra.curve ? curvedPlane(width, height, extra.curve) : new THREE.PlaneGeometry(width, height),
      new THREE.MeshBasicMaterial({map: this.texture, transparent: true, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false}));
    this.shown = 0; this.lines = []; this.value = 0; this.target = .6;
    this.draw(0); parts.panels.push(this);
  }
  frame(g, w, h, c) {
    g.clearRect(0, 0, w, h);
    const bg = g.createLinearGradient(0, 0, 0, h); bg.addColorStop(0, rgba(theme.panel[0], .42)); bg.addColorStop(1, rgba(theme.panel[1], .26));
    g.fillStyle = bg; g.fillRect(0, 0, w, h);
    g.fillStyle = 'rgba(255,255,255,.045)'; for (let y = (this.t * 40) % 4; y < h; y += 4) g.fillRect(0, y, w, 1);
    g.strokeStyle = c; g.lineWidth = 4; g.strokeRect(3, 3, w - 6, h - 6);
    g.lineWidth = 8;
    for (const [x, y, dx, dy] of [[4, 4, 1, 1], [w - 4, 4, -1, 1], [4, h - 4, 1, -1], [w - 4, h - 4, -1, -1]]) { g.beginPath(); g.moveTo(x, y + dy * 28); g.lineTo(x, y); g.lineTo(x + dx * 28, y); g.stroke(); }
    if (this.title) {
      g.fillStyle = c; g.font = 'bold 26px Arial'; g.fillText(this.title, 22, 40);
      g.fillStyle = (Math.sin(this.t * 4) > 0) ? '#ff4d6a' : 'rgba(255,77,106,.35)'; g.beginPath(); g.arc(w - 30, 32, 8, 0, Math.PI * 2); g.fill();
      g.fillStyle = rgba(this.color, .5); g.fillRect(22, 50, w - 44, 2);
    }
  }
  draw(t) {
    this.t = t;
    const g = this.g, w = this.canvas.width, h = this.canvas.height, c = hexOf(this.color), top = this.title ? 64 : 16;
    this.frame(g, w, h, c);
    const k = this.kind;
    if (k === 'bars') {
      const n = 10, bw = (w - 60) / n;
      for (let i = 0; i < n; i++) {
        const v = .35 + .3 * Math.sin(t * 1.7 + i * .8 + this.seed) + .25 * Math.sin(t * 3.1 + i * 1.9);
        const bh = Math.max(.08, v) * (h - top - 30);
        const grad = g.createLinearGradient(0, h - 20 - bh, 0, h - 20); grad.addColorStop(0, i % 3 ? c : '#ffffff'); grad.addColorStop(1, rgba(this.color, .25));
        g.fillStyle = grad; g.fillRect(30 + i * bw + 4, h - 20 - bh, bw - 10, bh);
      }
    } else if (k === 'line') {
      g.strokeStyle = c; g.lineWidth = 4; g.beginPath();
      for (let x = 20; x < w - 20; x += 6) { const y = top + (h - top) * (.55 - .22 * Math.sin((x + t * 90) / 40 + this.seed) - .12 * Math.sin((x + t * 160) / 13)); x === 20 ? g.moveTo(x, y) : g.lineTo(x, y); }
      g.stroke(); g.lineTo(w - 20, h - 12); g.lineTo(20, h - 12); g.closePath(); g.fillStyle = rgba(this.color, .15); g.fill();
      if (this.extra.status) { g.fillStyle = '#e8f1ff'; g.font = '24px Arial'; g.fillText(this.extra.status(), 22, top + 22); }
    } else if (k === 'donut') {
      this.value += ((this.extra.value ? this.extra.value() : .5 + .4 * Math.sin(t * .4 + this.seed)) - this.value) * .05;
      const cx = w / 2, cy = top + (h - top) / 2, r = Math.min(w, h - top) * .32;
      g.lineWidth = r * .32; g.strokeStyle = 'rgba(255,255,255,.12)'; g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.stroke();
      g.strokeStyle = c; g.beginPath(); g.arc(cx, cy, r, -Math.PI / 2 + t * .6, -Math.PI / 2 + t * .6 + Math.PI * 2 * Math.max(.02, this.value)); g.stroke();
      g.fillStyle = '#ffffff'; g.font = `bold ${r * .55 | 0}px Arial`; g.textAlign = 'center'; g.fillText(Math.round(this.value * 100) + '%', cx, cy + r * .2); g.textAlign = 'left';
    } else if (k === 'big') {
      const target = masterPct == null ? 0 : masterPct; this.value += (target - this.value) * .06;
      g.fillStyle = '#d8f3ff'; g.font = `bold ${h * .5 | 0}px Arial`; g.textAlign = 'center'; g.shadowColor = c; g.shadowBlur = 20;
      g.fillText(masterPct == null ? '—' : Math.round(this.value) + '%', w / 2, top + (h - top) * .62); g.shadowBlur = 0; g.textAlign = 'left';
      g.fillStyle = 'rgba(255,255,255,.12)'; g.fillRect(30, h - 34, w - 60, 10); g.fillStyle = c; g.fillRect(30, h - 34, (w - 60) * this.value / 100, 10);
    } else if (k === 'list') {
      const lines = screens[this.extra.agent] || [];
      if (!lines.length) {
        g.fillStyle = '#d8e6ff'; g.font = '26px Arial'; g.fillText(state[this.extra.agent] === 'done' ? 'Đã xong việc ✓' : 'Đang chờ việc…', 22, top + 34);
        g.strokeStyle = c; g.lineWidth = 3; g.beginPath();
        for (let x = 22; x < w - 22; x += 6) { const y = top + (h - top) * .68 + Math.sin((x + t * 80) / 26) * 16 * Math.sin(t * .7) + Math.sin((x - t * 50) / 9) * 4; x === 22 ? g.moveTo(x, y) : g.lineTo(x, y); }
        g.stroke();
      } else {
        lines.slice(0, 3).forEach((l, i) => {
          const y = top + 38 + i * 50, shown = (this['s' + i] = (this['s' + i] || 0) + (l.pct - (this['s' + i] || 0)) * .08);
          g.fillStyle = '#ffffff'; g.font = 'bold 24px Arial'; g.fillText(l.name.slice(0, 14), 22, y);
          g.fillStyle = 'rgba(255,255,255,.14)'; g.fillRect(250, y - 18, 180, 16);
          const grad = g.createLinearGradient(250, 0, 430, 0); grad.addColorStop(0, rgba(this.color, .7)); grad.addColorStop(1, c);
          g.fillStyle = grad; g.fillRect(250, y - 18, 1.8 * shown, 16);
          g.fillStyle = 'rgba(255,255,255,.55)'; g.fillRect(250 + ((t * 120 + i * 40) % 180), y - 18, 10, 16);
          g.fillStyle = '#ffffff'; g.font = '22px Arial'; g.fillText(Math.round(shown) + '%', 444, y);
        });
      }
    } else if (k === 'code') {
      const words = ['SCENE', 'Duration', 'ENVIRONMENT:', 'CAMERA:', 'ACTION:', 'fast', 'tracking', 'no slow motion', 'CONTINUITY:', '16:9'];
      g.fillStyle = c; g.font = 'bold 54px Arial'; g.fillText('</>', 26, top + 50);
      g.font = '18px monospace';
      for (let i = 0; i < 8; i++) {
        const line = Math.floor(t * 3) + i, y = top + 80 + i * 22 - ((t * 66) % 22);
        if (y > h - 10 || y < top + 62) continue;
        g.fillStyle = i % 3 === 0 ? c : 'rgba(220,235,255,.85)';
        g.fillText(`${line % 99} ${words[line % words.length]} ${words[(line * 7) % words.length]}`, 26, y);
      }
      g.fillStyle = rgba(this.color, .35); g.fillRect(300, top + 10, w - 330, h - top - 30);
      g.fillStyle = '#ffffff'; g.beginPath(); g.moveTo(330, h - 40); g.lineTo(390, top + 70); g.lineTo(440, h - 40); g.fill();
    } else if (k === 'thumbs' || k === 'video') {
      const cols = 3, tw = (w - 50) / cols, th = (h - top - 30) / 2;
      for (let i = 0; i < 6; i++) {
        const x = 20 + (i % cols) * tw, y = top + 6 + Math.floor(i / cols) * th;
        const sky = g.createLinearGradient(0, y, 0, y + th - 10); sky.addColorStop(0, '#5aa8ff'); sky.addColorStop(.6, '#f6b38a'); sky.addColorStop(.61, '#2f6b4a'); sky.addColorStop(1, '#1d3b2c');
        g.fillStyle = sky; g.fillRect(x + 4, y + 4, tw - 10, th - 12);
        g.fillStyle = 'rgba(20,30,40,.8)'; const ax = x + 10 + ((t * 30 + i * 37) % (tw - 30)); g.fillRect(ax, y + th * .55, 16, 8);
        if (k === 'video') { const p = ((t * .2 + i * .17) % 1); g.fillStyle = 'rgba(255,255,255,.25)'; g.fillRect(x + 4, y + th - 14, tw - 10, 4); g.fillStyle = c; g.fillRect(x + 4, y + th - 14, (tw - 10) * p, 4); }
        g.strokeStyle = i === Math.floor(t * 1.2) % 6 ? c : 'rgba(255,255,255,.25)'; g.lineWidth = 3; g.strokeRect(x + 4, y + 4, tw - 10, th - 12);
      }
    }
    this.texture.needsUpdate = true;
  }
}

function curvedPlane(width, height, bend) {
  const geo = new THREE.PlaneGeometry(width, height, 24, 1), pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) { const x = pos.getX(i); pos.setZ(i, -bend * (x / (width / 2)) ** 2); }
  geo.computeVertexNormals();
  return geo;
}

// ---------- các agent ----------
function buildAgents() {
  for (const [id, a] of Object.entries(AGENTS)) {
    const group = new THREE.Group();
    group.position.set(...a.pos); group.rotation.y = a.rot || 0; scene.add(group);
    const lamp = new THREE.PointLight(a.color, 2, 6, 1.8); lamp.position.set(0, 2.8, 1.2); group.add(lamp); parts.lights[id] = lamp;
    const ring = new THREE.Mesh(new THREE.RingGeometry(1.85, 1.98, 120), glow(a.color, .9)); ring.rotation.x = -Math.PI / 2; ring.position.y = .03; group.add(ring);
    const ring2 = new THREE.Mesh(new THREE.RingGeometry(2.15, 2.2, 120), glow(a.color, .4)); ring2.rotation.x = -Math.PI / 2; ring2.position.y = .03; group.add(ring2);
    parts.rings[id] = [ring, ring2];
    const bot = robot(a.color, a.master, a.trim); group.add(bot); parts.robots[id] = bot;
    if (a.master) {
      const metal = new THREE.MeshPhysicalMaterial({color: 0x9eabc6, metalness: .9, roughness: .2, clearcoat: 1, envMapIntensity: .5});
      const darkMetal = new THREE.MeshPhysicalMaterial({color: 0x16264e, metalness: .85, roughness: .25, clearcoat: 1, envMapIntensity: .5});
      [[2.5, 2.7, .3, .15, metal], [2.0, 2.2, .3, .45, darkMetal], [1.45, 1.6, .3, .75, metal]].forEach(([rt, rb, hh, y, m]) => {
        const tier = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, hh, 120), m); tier.position.y = y; group.add(tier);
        const rim = new THREE.Mesh(new THREE.TorusGeometry(rt + .01, .03, 10, 160), glow(0x7fd6ff, 1)); rim.rotation.x = Math.PI / 2; rim.position.y = y + hh / 2; group.add(rim);
      });
      const halo = new THREE.Mesh(new THREE.RingGeometry(1.0, 1.4, 96), glow(0x5ab8ff, .35)); halo.rotation.x = -Math.PI / 2; halo.position.y = .92; group.add(halo); parts.halo = halo;
      bot.userData.floatBase = 1.25; bot.position.y = 1.25;
      const pedestal = new THREE.Mesh(new THREE.CylinderGeometry(.75, .9, .36, 64), metal); pedestal.position.y = 1.08; group.add(pedestal);
      const prim = new THREE.Mesh(new THREE.TorusGeometry(.76, .025, 8, 96), glow(0x7fd6ff, 1)); prim.rotation.x = Math.PI / 2; prim.position.y = 1.26; group.add(prim);
      // màn hình quanh Master
      const p1 = new Panel(1.5, .9, 0x7fd6ff, 'bars', ''); p1.mesh.position.set(-2.6, 3.4, .3); p1.mesh.rotation.y = .5; group.add(p1.mesh);
      const p2 = new Panel(1.5, .9, 0x7fd6ff, 'big', ''); p2.mesh.position.set(2.6, 3.4, .3); p2.mesh.rotation.y = -.5; group.add(p2.mesh);
      continue;
    }
    const seat = chair(a.color); seat.position.z = -.38; group.add(seat);
    bot.position.set(0, .12, -.25);
    group.add(desk(a.color));
    // màn hình cong trên bàn (phía trước robot) + bảng lớn phía sau
    const deskPanel = new Panel(1.5, .62, a.color, a.desk, '', {curve: .12});
    deskPanel.mesh.position.set(0, 1.62, .95); deskPanel.mesh.rotation.x = -.12; group.add(deskPanel.mesh);
    const main = new Panel(2.2, 1.25, a.color, 'list', a.title, {agent: id});
    main.mesh.position.set(0, id === 'writer' ? 2.75 : 3.45, id === 'writer' ? -1.3 : -1.1); if (id === 'writer') main.mesh.scale.setScalar(.85); group.add(main.mesh);
    const left = new Panel(1.05, .75, a.color, id === 'analyst' ? 'line' : 'bars', '');
    left.mesh.position.set(-1.75, 3.0, -.8); left.mesh.rotation.y = .55; group.add(left.mesh);
    const right = new Panel(1.05, .75, a.color, 'donut', '', {value: () => { const l = screens[id] || []; return l.length ? l.reduce((s, x) => s + x.pct, 0) / l.length / 100 : state[id] === 'done' ? 1 : .08; }});
    right.mesh.position.set(1.75, 3.0, -.8); right.mesh.rotation.y = -.55; group.add(right.mesh);
  }
  // dây sáng nối các bàn về Master + gói dữ liệu
  const flow = canvasTexture(256, 8, (g, w, h) => {
    const grad = g.createLinearGradient(0, 0, w, 0); grad.addColorStop(0, 'rgba(255,255,255,0)'); grad.addColorStop(.7, 'rgba(255,255,255,.95)'); grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = 'rgba(255,255,255,.2)'; g.fillRect(0, 0, w, h); g.fillStyle = grad; g.fillRect(0, 0, w, h);
  }, [6, 1]);
  for (const id of ['analyst', 'writer', 'studio']) {
    const from = new THREE.Vector3(...AGENTS[id].pos), to = new THREE.Vector3(...AGENTS.conductor.pos);
    from.y = to.y = .05;
    const mid = from.clone().lerp(to, .5);
    const curve = new THREE.CatmullRomCurve3([from, from.clone().lerp(mid, .5), mid, mid.clone().lerp(to, .5), to]);
    const tex = flow.clone(); tex.needsUpdate = true;
    const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 80, .05, 10, false),
      new THREE.MeshBasicMaterial({color: 0x3d6fb7, map: tex, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false}));
    scene.add(tube);
    const packets = [];
    for (let i = 0; i < 6; i++) { const p = new THREE.Mesh(new THREE.SphereGeometry(.09, 14, 10), glow(AGENTS[id].color, 1)); p.visible = false; scene.add(p); packets.push(p); }
    parts.links[id] = {curve, tube, tex, packets};
  }
}

function buildDust() {
  const count = 600, pos = new Float32Array(count * 3), speed = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const r = Math.sqrt(rand()) * 13.5, a = rand() * Math.PI * 2;
    pos[i * 3] = Math.cos(a) * r; pos[i * 3 + 1] = rand() * 9; pos[i * 3 + 2] = Math.sin(a) * r; speed[i] = .06 + rand() * .2;
  }
  const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const dust = new THREE.Points(geo, new THREE.PointsMaterial({color: 0xa8dcff, size: .06, map: softDot(), transparent: true, opacity: .7, blending: THREE.AdditiveBlending, depthWrite: false}));
  dust.userData.speed = speed; scene.add(dust); parts.dust = dust;
}

// ---------- camera, khung hình ----------
function applyCamera() {
  const ease = 1 - Math.pow(1 - Math.min(view.intro, 1), 3);
  const r = view.radius + (1 - ease) * 3, p = view.phi + (1 - ease) * .12, t = view.theta + (1 - ease) * .9;  // bay vào trong phòng, dưới trần
  camera.position.set(view.target.x + r * Math.sin(p) * Math.sin(t), view.target.y + r * Math.cos(p), view.target.z + r * Math.sin(p) * Math.cos(t));
  camera.lookAt(view.target);
}

function resize() {
  const w = host.clientWidth, h = host.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  composer?.setSize(w, h);
  bloom?.resolution.set(w / 2, h / 2);
  mirror?.getRenderTarget().setSize(Math.round(w * .5), Math.round(h * .5));
  camera.aspect = w / h; camera.updateProjectionMatrix();
}

function placeLabels() {
  const w = host.clientWidth, h = host.clientHeight, v = new THREE.Vector3(), up = new THREE.Vector3(0, 1, 0);
  for (const [id, el] of Object.entries(labels)) {
    const a = AGENTS[id], g = new THREE.Vector3(...a.pos);
    const off = new THREE.Vector3(0, 0, a.master ? .6 : 1.75).applyAxisAngle(up, a.rot || 0);
    v.set(g.x + off.x, a.master ? 5.75 : .55, g.z + off.z).project(camera);
    el.style.transform = `translate(-50%, 0) translate(${(v.x * .5 + .5) * w}px, ${(-v.y * .5 + .5) * h}px)`;
    el.style.opacity = v.z < 1 ? '1' : '0';
    el.style.zIndex = String(Math.round((1 - v.z) * 10000));
    el.classList.toggle('compact', view.focus ? view.focus !== id : (Math.abs(Math.atan2(Math.sin(view.theta), Math.cos(view.theta))) > .5 || view.radius > 17));
    el.classList.toggle('focus', view.focus === id);
    el.classList.toggle('hover', hovered === id);
  }
}

function setFace(bot, mood, blink) {
  const u = bot.userData;
  if (u.mood === mood && u.blink === blink) return;
  u.mood = mood; u.blink = blink;
  u.face.material.map?.dispose();
  u.face.material.map = faceTexture(u.color, mood, blink);
  u.face.material.needsUpdate = true;
}

let panelClock = 0;
function frame() {
  raf = 0;
  if (!running) return;
  const dt = Math.min(clock.getDelta(), .05);
  view.t += dt; view.intro = (performance.now() - view.start) / 2600;  // theo giờ thật: máy chậm cũng bay vào đúng 2,6 giây
  const t = view.t;
  if (view.goal) {  // camera bay tới robot được chọn (hoặc về góc mặc định)
    const g = view.goal, k = 1 - Math.pow(.02, dt);
    view.target.lerp(g.target, k); view.radius += (g.radius - view.radius) * k; view.phi += (g.phi - view.phi) * k;
    view.theta += (Math.atan2(Math.sin(g.theta - view.theta), Math.cos(g.theta - view.theta))) * k;
  } else if (view.auto && !view.drag) view.theta += (Math.sin(t * .08) * .45 - view.theta) * .01;
  applyCamera();
  // địa cầu, quỹ đạo, vệt quét, bụi, cây, đèn nóc toà nhà, xe bay
  const gl = parts.globe;
  if (gl) { gl.group.rotation.y += dt * .15; gl.wire.rotation.y -= dt * .04; for (const o of gl.orbits) o.pivot.rotation.y += dt * o.sp; gl.group.position.y = 6.55 + Math.sin(t * .6) * .08; }
  for (const scan of parts.scan) scan.rotation.z -= dt * .3;
  if (parts.studio && parts.studio.group.visible) {  // phòng thu: vòng sóng âm nhảy theo nhịp + biển ON AIR nhấp nháy
    const busy = Object.values(state).some(v => v === 'working') ? 1 : .45;
    parts.studio.bars.forEach((bar, i) => {
      const level = .25 + busy * (Math.abs(Math.sin(t * 2.6 + i * .55)) * .9 + Math.abs(Math.sin(t * 5.1 + i * 1.3)) * .5);
      bar.scale.y = level; bar.position.y = .06 + level * .5 * bar.userData.h;
    });
    parts.studio.reels.forEach((reel, i) => { reel.rotation.z += dt * (i ? -.5 : .5) * (busy + .3); });
    parts.studio.signs.forEach(sign => { sign.material.opacity = .75 + Math.sin(t * 3) * .25; });
  }
  if (parts.halo) parts.halo.rotation.z += dt * .4;
  if (parts.dust) {
    const p = parts.dust.geometry.attributes.position, sp = parts.dust.userData.speed;
    for (let i = 0; i < sp.length; i++) { let y = p.array[i * 3 + 1] + sp[i] * dt; if (y > 9) y = 0; p.array[i * 3 + 1] = y; }
    p.needsUpdate = true;
  }
  for (const {plant} of parts.pulses) plant.rotation.z = Math.sin(t * .8 + plant.userData.sway) * .03;
  for (const b of parts.beacons) b.mesh.visible = Math.sin(t * 2.4 + b.phase) > .2;
  for (const v of parts.vehicles) {
    const u = v.userData, a = u.phase + t * u.speed;
    const x = Math.cos(a) * u.r, z = Math.sin(a) * u.r, y = u.y + Math.sin(t * .5 + u.wobble) * .8;
    v.position.set(x, y, z);
    const dir = u.speed > 0 ? 1 : -1;
    v.rotation.set(0, -a - dir * Math.PI / 2 + Math.PI, dir * -.18 + Math.sin(t + u.wobble) * .05);
    u.tail.visible = Math.sin(t * 6 + u.wobble * 3) > 0;
  }
  // robot
  for (const [id, bot] of Object.entries(parts.robots)) {
    const st = state[id] || 'waiting', u = bot.userData, time = t + id.length * 1.7, working = st === 'working';
    setFace(bot, st, (time % 4.4) < .12 && st !== 'waiting' && st !== 'error');
    u.head.rotation.z = working ? Math.sin(time * 3.4) * .06 : st === 'error' ? Math.sin(time * 9) * .05 : Math.sin(time * .7) * .04;
    u.head.rotation.x = working && !u.master ? .12 + Math.sin(time * 1.2) * .04 : st === 'error' ? .2 : Math.sin(time * .5) * .02;
    u.head.rotation.y = u.master ? Math.sin(time * .45) * .25 : 0;
    if (u.master) {  // Master dang tay điều phối, khuỷu tay co nhịp nhàng
      u.arms.forEach(({shoulder, elbow, side}) => {
        shoulder.rotation.set(-.35 + Math.sin(time * 1.4 + side) * .12, 0, side * (1.05 + Math.sin(time * 1.2) * .12));
        elbow.rotation.set(-.5 - Math.sin(time * 1.4 + side) * .2, 0, side * -.3);
      });
    } else if (id === 'studio' && st !== 'waiting' && st !== 'error' && Math.sin(time * .5) > .55) {  // giơ ngón cái
      u.arms.forEach(({shoulder, elbow, side}) => {
        if (side < 0) { shoulder.rotation.set(-1.25, 0, -.35); elbow.rotation.set(-1.6, 0, 0); }
        else { shoulder.rotation.set(-.75, 0, .12); elbow.rotation.set(-.85, 0, 0); }
      });
    } else {  // gõ phím: vai đưa ra trước, khuỷu co, cẳng tay nhịp lên xuống
      u.arms.forEach(({shoulder, elbow, side}, i) => {
        const type = working ? Math.sin(time * 15 + i * Math.PI) * .16 : Math.sin(time * .8 + i) * .03;
        shoulder.rotation.set(-.75 + type * .4, 0, side * .12);
        elbow.rotation.set(-.85 + type, 0, 0);
      });
    }
    u.light.color.set(st === 'error' ? 0xff4d5e : st === 'done' ? 0x5cf2b8 : st === 'waiting' ? u.baseColor.clone().multiplyScalar(.55) : u.baseColor);
    if (u.tip) u.tip.visible = !working || Math.sin(time * 10) > -.3;
    if (u.crown) u.crown.rotation.y = time * .5;
    if (u.floatBase !== undefined) bot.position.y = u.floatBase + Math.sin(time * 1.3) * .06;
    const [r1, r2] = parts.rings[id];
    r1.scale.setScalar(working ? 1 + Math.sin(time * 3) * .04 : 1); r1.material.opacity = working ? 1 : st === 'done' ? .75 : st === 'error' ? .6 + Math.sin(time * 8) * .4 : .45;
    r1.material.color.set(st === 'error' ? 0xff4d5e : AGENTS[id].color);
    r2.rotation.z += dt * (working ? .8 : .2);
    parts.lights[id].intensity = working ? 1.6 + Math.sin(time * 3) * .4 : st === 'done' ? 1 : .5;
    bot.scale.setScalar((u.master ? 1.3 : 1) * (hovered === id ? 1.06 : 1));
  }
  for (const [id, link] of Object.entries(parts.links)) {
    const working = state[id] === 'working';
    link.tube.material.color.set(working ? AGENTS[id].color : state[id] === 'done' ? 0x5cf2b8 : 0x2e5aa0);
    link.tex.offset.x -= dt * (working ? 1.3 : .3);
    link.packets.forEach((p, i) => { p.visible = working; if (working) p.position.copy(link.curve.getPointAt((t * .32 + i / link.packets.length) % 1)); });
  }
  // bảng số liệu: vẽ lại ~15 khung/giây
  panelClock += dt;
  if (panelClock > (perf.low ? .2 : .066)) { panelClock = 0; for (const p of parts.panels) p.draw(t); }
  if (composer && !perf.low) composer.render(); else renderer.render(scene, camera);
  placeLabels();
  perf.frames++; perf.time += dt;
  if (perf.time > 3) {
    const fps = perf.frames / perf.time;
    if (view.intro > 1.2 && fps < 24) {
      if (!perf.stage) {  // bước 1: bỏ sàn phản chiếu (tốn nhất), giữ quầng sáng neon
        perf.stage = 1; renderer.setPixelRatio(1); if (mirror) mirror.visible = false; if (parts.tint) parts.tint.material.opacity = .96; resize();
      } else if (perf.stage === 1 && fps < 18) {  // bước 2: máy vẫn giật → tắt quầng sáng, vẽ bảng thưa hơn
        perf.stage = 2; perf.low = true;
      }
    }
    perf.frames = 0; perf.time = 0;
  }
  raf = requestAnimationFrame(frame);
}

function focusOn(id) {
  if (!id) { view.goal = {target: new THREE.Vector3(HOME.tx, HOME.ty, HOME.tz), radius: HOME.radius, phi: HOME.phi, theta: 0}; view.focus = null;
    setTimeout(() => { if (!view.focus) view.goal = null; }, 2500); return; }
  const a = AGENTS[id], p = new THREE.Vector3(...a.pos);
  view.focus = id;
  view.goal = {target: new THREE.Vector3(p.x, a.master ? 3.2 : 2.0, p.z + (a.master ? 0 : .4)), radius: a.master ? 7.5 : 6.2, phi: 1.28, theta: a.rot || 0};
  host.dispatchEvent(new CustomEvent('hq-focus', {detail: id}));
}

function pick(e) {
  const r = canvas.getBoundingClientRect();
  pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects(Object.values(parts.robots), true)[0];
  const bot = hit?.object.userData.robot;
  hovered = bot ? Object.keys(parts.robots).find(id => parts.robots[id] === bot) : null;
  canvas.style.cursor = hovered ? 'pointer' : 'grab';
  canvas.title = hovered ? 'Bấm để phóng tới agent này' : '';
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
  renderer.toneMappingExposure = .88;
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x050a20);
  scene.fog = new THREE.Fog(0x3a2c62, 45, 130);
  try {
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(renderer), .04).texture;
  } catch (error) { /* không có phản chiếu môi trường vẫn chạy */ }
  camera = new THREE.PerspectiveCamera(48, 16 / 9, .1, 200);
  scene.add(new THREE.HemisphereLight(0xa9c8ff, 0x2a1840, .42));
  const front = new THREE.DirectionalLight(0xe8f0ff, .45); front.position.set(0, 9, 16); scene.add(front);
  const sun = new THREE.DirectionalLight(0xffb48a, .55); sun.position.set(-10, 14, -14); scene.add(sun);
  const fill = new THREE.DirectionalLight(0x9fc4ff, .55); fill.position.set(8, 10, 12); scene.add(fill);
  const center = new THREE.PointLight(0x5ab8ff, 3, 16, 1.6); center.position.set(0, 7.5, -2); scene.add(center);
  buildSky(); buildCity(); buildVehicles(); buildRoom(); buildGlobe(); buildAgents(); buildDust();
  try {
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    bloom = new UnrealBloomPass(new THREE.Vector2(640, 360), .7, .45, .86);
    composer.addPass(bloom);
    composer.addPass(new OutputPass());
  } catch (error) {
    composer = null;
  }
  clock = new THREE.Clock();
  canvas.addEventListener('pointerdown', e => { view.drag = {x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY}; if (view.goal && !view.focus) view.goal = null; canvas.setPointerCapture(e.pointerId); view.start = Math.min(view.start || 0, performance.now() - 3000); });
  canvas.addEventListener('pointermove', e => {
    if (!view.drag) { pick(e); return; }
    view.theta -= (e.clientX - view.drag.x) * .006;
    view.phi = Math.max(.6, Math.min(1.45, view.phi - (e.clientY - view.drag.y) * .004));
    view.drag = {...view.drag, x: e.clientX, y: e.clientY};
    if (view.focus && Math.hypot(e.clientX - view.drag.sx, e.clientY - view.drag.sy) > 6) { view.goal = null; view.focus = null; }
  });
  const end = e => {
    const d = view.drag; view.drag = null;
    if (!d || !e || Math.hypot(e.clientX - d.sx, e.clientY - d.sy) > 6) return;  // kéo xoay, không phải bấm
    pick(e);
    focusOn(hovered && hovered !== view.focus ? hovered : null);  // bấm robot: bay tới; bấm lại / bấm chỗ trống: về mặc định
  };
  canvas.addEventListener('pointerup', end); canvas.addEventListener('pointercancel', () => { view.drag = null; });
  canvas.addEventListener('pointerleave', () => { hovered = null; });
  canvas.addEventListener('wheel', e => { e.preventDefault(); const k = e.deltaY < 0 ? .92 : 1.08; if (view.goal) view.goal.radius = Math.max(4, Math.min(22, view.goal.radius * k)); else view.radius = Math.max(5, Math.min(22, view.radius * k)); }, {passive: false});
  new ResizeObserver(resize).observe(host);
  resize();
  return true;
}

// ---------- giao diện theo workflow ----------
// default: phòng điều khiển xanh tím (động vật hoang dã) · studio: phòng thu hoàng hôn vàng cam (thoại phim) · lab: xanh ngọc (workflow khác)
const THEMES = {
  default: {panel: [0x2a6bea, 0x0b2a7a]},
  studio: {panel: [0xe8742a, 0x7a2a0b], band: [.045, .14], sat: 1.05, sky: ['#120608', '#3a1420', '#7a2a2a', '#e0703a', '#ffc46a', '#40202a', '#0d0606']},
  lab: {panel: [0x1fa890, 0x0b5a4a], band: [.40, .50], sat: .95, sky: ['#04100f', '#0a2a30', '#14505a', '#2a9a8a', '#a8f0c8', '#143a3a', '#040c0c']},
};
let theme = THEMES.default, themeName = 'default', themed = null;
function collectThemed() {
  themed = [];
  const seen = new Set(), add = color => { if (color && color.isColor && !seen.has(color)) { seen.add(color); themed.push({color, base: color.clone()}); } };
  const skip = new Set();
  for (const bot of Object.values(parts.robots)) bot.traverse(o => skip.add(o));  // robot giữ màu riêng của từng agent
  if (parts.studio) parts.studio.group.traverse(o => skip.add(o));
  scene.traverse(o => {
    if (skip.has(o)) return;
    if (o.isLight) { add(o.color); add(o.groundColor); }
    for (const m of Array.isArray(o.material) ? o.material : o.material ? [o.material] : []) { add(m.color); add(m.emissive); if (m.uniforms && m.uniforms.color) add(m.uniforms.color.value); }
  });
  add(scene.background); add(scene.fog && scene.fog.color);
}
function skyTexture(stops) {
  return canvasTexture(16, 512, (g, w, h) => {
    const grad = g.createLinearGradient(0, 0, 0, h);
    [0, .3, .42, .48, .52, .56, 1].forEach((at, i) => grad.addColorStop(at, stops[i]));
    g.fillStyle = grad; g.fillRect(0, 0, w, h);
  });
}
function buildStudio() {
  const group = new THREE.Group(); group.visible = false;
  const bars = [];
  for (let i = 0; i < 72; i++) {  // vòng sóng âm quanh bục Master
    const a = i / 72 * Math.PI * 2, h = .38 + (i % 3) * .14;
    const bar = new THREE.Mesh(new THREE.BoxGeometry(.09, h, .09), glow(i % 2 ? 0xffb347 : 0xff6a3d, .85));
    bar.position.set(Math.cos(a) * 2.95, .3, Math.sin(a) * 2.95 - 1.9); bar.rotation.y = -a; bar.userData.h = h;
    group.add(bar); bars.push(bar);
  }
  const reels = [], signs = [];
  for (const side of [-1, 1]) {
    // cuộn phim quay hai bên quả cầu
    const reel = new THREE.Group();
    reel.add(new THREE.Mesh(new THREE.TorusGeometry(1.25, .07, 10, 64), glow(0xffc46a, .9)));
    reel.add(new THREE.Mesh(new THREE.TorusGeometry(.28, .06, 8, 32), glow(0xffe2a8, .9)));
    for (let k = 0; k < 6; k++) { const hole = new THREE.Mesh(new THREE.RingGeometry(.2, .27, 24), glow(0xff8a4a, .8)); const a = k / 6 * Math.PI * 2; hole.position.set(Math.cos(a) * .75, Math.sin(a) * .75, 0); reel.add(hole); }
    reel.position.set(side * 6.6, 6.7, -5.2); reel.rotation.y = -side * .5; group.add(reel); reels.push(reel);
    // biển ON AIR
    const tex = canvasTexture(512, 160, (g, w, h) => {
      g.clearRect(0, 0, w, h); g.strokeStyle = '#ff5a4a'; g.lineWidth = 10; g.strokeRect(8, 8, w - 16, h - 16);
      g.fillStyle = '#ff5a4a'; g.beginPath(); g.arc(70, h / 2, 22, 0, Math.PI * 2); g.fill();
      g.fillStyle = '#ffe9d8'; g.font = 'bold 84px Arial'; g.textBaseline = 'middle'; g.fillText('ON AIR', 120, h / 2 + 4);
    });
    const sign = new THREE.Mesh(new THREE.PlaneGeometry(2.3, .72), new THREE.MeshBasicMaterial({map: tex, transparent: true, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false}));
    sign.position.set(side * 6.6, 4.75, -5.2); sign.rotation.y = -side * .5; group.add(sign); signs.push(sign);
  }
  scene.add(group);
  parts.studio = {group, bars, reels, signs};
}
// name: default | studio | lab; titles: {analyst|writer|studio: chữ trên bảng lớn sau lưng agent}
export function setTheme(name, titles) {
  if (!scene) return;
  const next = THEMES[name] ? name : 'default';
  if (titles) for (const panel of parts.panels) if (panel.title && panel.extra.agent && titles[panel.extra.agent]) panel.title = titles[panel.extra.agent];
  if (next === themeName) return;
  themeName = next; theme = THEMES[next];
  if (!parts.studio) buildStudio();
  if (!themed) collectThemed();
  const hsl = {};
  for (const {color, base} of themed) {
    color.copy(base);
    if (!theme.band) continue;
    base.getHSL(hsl);
    if (hsl.s < .08 || hsl.h < .47 || hsl.h > .86) continue;  // xám / trắng và màu ấm giữ nguyên; dải xanh–tím đổi sang dải màu của giao diện
    const k = (hsl.h - .47) / .39;
    color.setHSL(theme.band[0] + k * (theme.band[1] - theme.band[0]), Math.min(1, hsl.s * theme.sat), hsl.l);
  }
  if (parts.sky) {
    if (theme.sky && !parts.sky.maps[next]) parts.sky.maps[next] = skyTexture(theme.sky);
    parts.sky.mesh.material.map = parts.sky.maps[next] || parts.sky.maps.default; parts.sky.mesh.material.needsUpdate = true;
  }
  parts.studio.group.visible = next === 'studio';
}

export function start() { if (!renderer || running) return; running = true; clock.getDelta(); if (!view.start) view.start = performance.now(); raf = requestAnimationFrame(frame); }
export function stop() { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }
export function setAuto(on) { view.auto = on; }
export function focus(id) { focusOn(id || null); }
export function resetView() { focusOn(null); }

// states: {conductor|analyst|writer|studio: waiting/working/done/error}; lines: {id: [{name, pct}]}; pct: % của video đang theo dõi
export function update(states, lines, pct) {
  state = states || {};
  screens = lines || {};
  masterPct = pct;
}
