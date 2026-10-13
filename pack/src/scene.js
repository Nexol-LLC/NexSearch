/* PackOnTruck — интерактивная 3D-сцена (Three.js r128).
   Мировые координаты: X → длина, Y → высота, Z → ширина.
   Зоны: секции текущей грузовой площади + склад слева/справа.
   Выделение места кликом, перетаскивание мышью с примагничиванием,
   красная подсветка пересечений. Локальные координаты мест — мм. */

const SECTION_GAP = 0.6;   // м, зазор между секциями
const WH_DEPTH = 3200;     // мм, глубина зоны склада
const WH_GAP = 1.7;        // м, отступ склада от борта (место под линейку и подписи нагрузок)
const SNAP = 60;           // мм, радиус примагничивания

let _renderer, _scene, _camera, _canvasEl, _cargoGroup, _vehGroup, _labelGroup;
let _cgGroup,_cgDrag=null;
let _axlePos = []; // [{sec, kind:'front'|'rear', x (м), W (м)}] — позиции для подписей нагрузок
let _orbit = { theta: -Math.PI / 4, phi: Math.PI / 3.2, dist: 18, target: null };
let _zones = [];           // [{kind:'sec'|'wh', ... , x0, z0 (м), L, W, H (мм)}]
let _boxes = [];           // ссылка на массив мест приложения (текущий экран)
let _colors = [];
let _selectedId = null;
let _api = {};             // { onSelect(id), onDrop(box), boxesVisible() }
let _drag = null;          // {box, mesh, offX, offZ, plane}
let _raycaster, _mouse;

function sceneInit(container, api) {
  _api = api || {};
  _scene = new THREE.Scene();
  _scene.background = new THREE.Color(0xf2f4f7);
  _camera = new THREE.PerspectiveCamera(45, 1, 0.05, 500);
  _renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  container.appendChild(_renderer.domElement);
  _canvasEl = _renderer.domElement;
  _raycaster = new THREE.Raycaster();
  _mouse = new THREE.Vector2();

  _scene.add(new THREE.AmbientLight(0xffffff, 0.75));
  const dl = new THREE.DirectionalLight(0xffffff, 0.6);
  dl.position.set(10, 20, 8);
  _scene.add(dl);

  _vehGroup = new THREE.Group();
  _cargoGroup = new THREE.Group();
  _labelGroup = new THREE.Group();
  _scene.add(_vehGroup, _cargoGroup, _labelGroup);
  _cgGroup=new THREE.Group();_scene.add(_cgGroup);
  _orbit.target = new THREE.Vector3(0, 0, 0);

  let orbiting = null;
  _canvasEl.addEventListener('mousedown', e => {
    const cg=pickCg(e);
    if(e.button===0&&cg){const zone=_zones.find(z=>z.kind==='sec'&&z.idx===cg.userData.section);_cgDrag={zone,plane:new THREE.Plane(new THREE.Vector3(0,1,0),-cg.position.y)};return;}
    const hit = pickBox(e);
    if(hit&&(e.ctrlKey||e.metaKey)){if(_api.onMultiSelect)_api.onMultiSelect(hit.userData.boxId);return;}
    if (hit && e.button === 0) {
      if(_api.onBeforeMove)_api.onBeforeMove();
      startDrag(hit, e);
    } else {
      if (e.button === 0 && !hit) selectBox(null);
      orbiting = { x: e.clientX, y: e.clientY, btn: e.button, shift: e.shiftKey };
    }
  });
  window.addEventListener('mouseup', () => {
    _cgDrag=null;
    if (_drag) endDrag();
    orbiting = null;
  });
  window.addEventListener('mousemove', e => {
    if(_cgDrag){const pt=planePoint(e,_cgDrag.plane);if(pt&&_api.onCgMove)_api.onCgMove(_cgDrag.zone.idx,(pt.x-_cgDrag.zone.x0)*1000,pt.z*1000);return;}
    if (_drag) { moveDrag(e); return; }
    if (!orbiting) return;
    const dx = e.clientX - orbiting.x, dy = e.clientY - orbiting.y;
    orbiting.x = e.clientX; orbiting.y = e.clientY;
    if (orbiting.btn === 2 || orbiting.shift) {
      const s = _orbit.dist * 0.0015;
      const right = new THREE.Vector3().subVectors(_camera.position, _orbit.target)
        .cross(_camera.up).normalize().multiplyScalar(dx * s);
      _orbit.target.add(right).add(new THREE.Vector3(0, dy * s, 0));
    } else {
      _orbit.theta -= dx * 0.006;
      _orbit.phi = Math.min(Math.PI - 0.05, Math.max(0.05, _orbit.phi - dy * 0.006));
    }
    sceneRender();
  });
  _canvasEl.addEventListener('wheel', e => {
    e.preventDefault();
    _orbit.dist = Math.min(150, Math.max(2, _orbit.dist * (e.deltaY > 0 ? 1.1 : 0.9)));
    sceneRender();
  }, { passive: false });
  _canvasEl.addEventListener('contextmenu', e => e.preventDefault());

  sceneResize(container);
  window.addEventListener('resize', () => sceneResize(container));
}

function sceneResize(container) {
  const w = container.clientWidth, h = container.clientHeight || 400;
  _renderer.setSize(w, h);
  _camera.aspect = w / h;
  _camera.updateProjectionMatrix();
  sceneRender();
}

function sceneRender() {
  const t = _orbit.target;
  _camera.position.set(
    t.x + _orbit.dist * Math.sin(_orbit.phi) * Math.cos(_orbit.theta),
    t.y + _orbit.dist * Math.cos(_orbit.phi),
    t.z + _orbit.dist * Math.sin(_orbit.phi) * Math.sin(_orbit.theta));
  _camera.lookAt(t);
  _renderer.render(_scene, _camera);
}

/* ---------- Текстовые подписи ---------- */

function textCanvas(text, color) {
  const c = document.createElement('canvas');
  let ctx = c.getContext('2d');
  const font = 'bold 64px "Segoe UI", Arial, sans-serif';
  ctx.font = font;
  c.width = Math.ceil(ctx.measureText(text).width) + 24;
  c.height = 88;
  ctx = c.getContext('2d');
  ctx.font = font;
  ctx.fillStyle = color;
  ctx.textBaseline = 'middle';
  ctx.fillText(text, 12, 46);
  return c;
}

/* Спрайт (всегда лицом к камере) — для значений нагрузок */
function makeTextSprite(text, color, sizeM) {
  const c = textCanvas(text, color);
  const tex = new THREE.CanvasTexture(c);
  tex.minFilter = THREE.LinearFilter;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true }));
  sp.scale.set(sizeM * c.width / c.height, sizeM, 1);
  return sp;
}

/* Подписи мест: кэш текстур по тексту (позиций немного, текстуры переиспользуются) */
const _labelTexCache = {};
function boxLabelSprite(text) {
  let tex = _labelTexCache[text];
  if (!tex) {
    tex = new THREE.CanvasTexture(textCanvas(text, '#1b1f24'));
    tex.minFilter = THREE.LinearFilter;
    _labelTexCache[text] = tex;
  }
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true }));
  const h = 0.2;
  sp.scale.set(Math.min(1.6, h * tex.image.width / tex.image.height), h, 1);
  return sp;
}

/* Плоская надпись на земле — для зон складов */
function makeGroundText(text, color, heightM) {
  const c = textCanvas(text, color);
  const tex = new THREE.CanvasTexture(c);
  tex.minFilter = THREE.LinearFilter;
  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(heightM * c.width / c.height, heightM),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.65, depthWrite: false }));
  mesh.rotation.x = -Math.PI / 2;
  return mesh;
}

/* Ворота сзади секции: левая створка закрыта, правая приоткрыта (у последней).
   У бортового кузова (flatbed) — единый задний борт, откинутый вниз. */
function addDoors(xR, Wm, Hm, ajar, flatbed) {
  if (flatbed) {
    const hb = 0.55; // высота борта, м
    const g = new THREE.Group();
    const geo = new THREE.PlaneGeometry(Wm, hb);
    const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
      color: 0x2c3e50, transparent: true, opacity: 0.14, side: THREE.DoubleSide }));
    mesh.rotation.y = Math.PI / 2; // плоскость поперёк оси движения
    mesh.position.y = hb / 2;
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo),
      new THREE.LineBasicMaterial({ color: 0x2c3e50 }));
    edges.rotation.y = Math.PI / 2;
    edges.position.y = hb / 2;
    g.add(mesh, edges);
    g.position.set(xR, 0, Wm / 2);      // петля — по нижней кромке
    g.rotation.z = ajar ? -2.1 : 0;     // откинут вниз-наружу (~120°)
    _vehGroup.add(g);
    return;
  }
  const mkDoor = (hingeZ, dir, angle) => {
    const g = new THREE.Group();
    const geo = new THREE.PlaneGeometry(Wm / 2, Hm);
    const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
      color: 0x2c3e50, transparent: true, opacity: 0.14, side: THREE.DoubleSide }));
    mesh.rotation.y = Math.PI / 2;
    mesh.position.z = dir * Wm / 4;
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo),
      new THREE.LineBasicMaterial({ color: 0x2c3e50 }));
    edges.rotation.y = Math.PI / 2;
    edges.position.z = dir * Wm / 4;
    g.add(mesh, edges);
    g.position.set(xR, Hm / 2, hingeZ);
    g.rotation.y = angle;
    _vehGroup.add(g);
  };
  mkDoor(0, +1, 0);                    // левая створка — закрыта
  mkDoor(Wm, -1, ajar ? -0.95 : 0);    // правая — приоткрыта (~55°) наружу
}

/* ---------- 3D-модель седельного тягача (STL в src/tractor-data.js) ---------- */

let _geoCache = {}, _geoWait = {};

/* Разбор бинарного STL → BufferGeometry (без внешних загрузчиков) */
function parseBinSTL(buf) {
  const dv = new DataView(buf);
  const n = dv.getUint32(80, true);
  const pos = new Float32Array(n * 9);
  const norm = new Float32Array(n * 9);
  let off = 84;
  for (let i = 0; i < n; i++) {
    const nx = dv.getFloat32(off, true),
          ny = dv.getFloat32(off + 4, true),
          nz = dv.getFloat32(off + 8, true);
    for (let v = 0; v < 3; v++) {
      const p = off + 12 + v * 12, k = i * 9 + v * 3;
      pos[k] = dv.getFloat32(p, true);
      pos[k + 1] = dv.getFloat32(p + 4, true);
      pos[k + 2] = dv.getFloat32(p + 8, true);
      norm[k] = nx; norm[k + 1] = ny; norm[k + 2] = nz;
    }
    off += 50;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.BufferAttribute(norm, 3));
  return g;
}

/* Распаковка gzip+base64 (однократно, с кэшем по ключу) */
function loadPackedGeo(key, b64, cb) {
  if (_geoCache[key]) return cb(_geoCache[key] === 'fail' ? null : _geoCache[key]);
  (_geoWait[key] = _geoWait[key] || []).push(cb);
  if (_geoWait[key].length > 1) return;
  const flush = geo => {
    _geoCache[key] = geo || 'fail';
    const cbs = _geoWait[key]; _geoWait[key] = [];
    cbs.forEach(f => f(geo));
  };
  if (!b64 || typeof DecompressionStream === 'undefined') return flush(null);
  try {
    const bin = atob(b64);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    new Response(new Blob([arr]).stream().pipeThrough(new DecompressionStream('gzip')))
      .arrayBuffer()
      .then(b => flush(parseBinSTL(b)))
      .catch(() => flush(null));
  } catch (e) { flush(null); }
}

function modelMat() {
  return new THREE.MeshPhongMaterial({ color: 0xd9dee5, flatShading: true, shininess: 18 });
}
function tractorB64() { return typeof TRACTOR_STL_GZ_B64 !== 'undefined' ? TRACTOR_STL_GZ_B64 : null; }
function axleB64() { return typeof AXLE_STL_GZ_B64 !== 'undefined' ? AXLE_STL_GZ_B64 : null; }

const TRACTOR_WIDTH = 2.5;         // м — реальная ширина тягача (масштаб модели)
const TRACTOR_REAR_OVERHANG = 0.9; // м — свес рамы за седлом (fallback-якорь без оси)

/* Сборка тягача: truck_cut + rear_axle (общая система координат модели).
   Якорь — центр задней оси под седлом (xKingpin); колёса на земле (-1 м);
   перёд модели (+X) разворачивается в -X сцены.
   Заглушка (каркасная кабина) удаляется после успешной распаковки. */
function addTractorModel(xKingpin, W0, placeholder) {
  loadPackedGeo('tractor', tractorB64(), geoT => {
    if (!geoT) return;                             // нет данных/поддержки — остаётся каркас
    loadPackedGeo('axle', axleB64(), geoA => {
      if (placeholder.parent !== _vehGroup) return; // сцена уже перестроена
      _vehGroup.remove(placeholder);
      geoT.computeBoundingBox();
      const bbT = geoT.boundingBox;
      const scale = TRACTOR_WIDTH / (bbT.max.z - bbT.min.z);
      const g = new THREE.Group();
      g.add(new THREE.Mesh(geoT, modelMat()));
      let tx, minY = bbT.min.y;
      if (geoA) {
        geoA.computeBoundingBox();
        const bbA = geoA.boundingBox;
        g.add(new THREE.Mesh(geoA, modelMat())); // задняя ось на родном месте
        tx = xKingpin + scale * (bbA.min.x + bbA.max.x) / 2; // центр оси → под седло
        minY = Math.min(minY, bbA.min.y);
      } else {
        tx = xKingpin + TRACTOR_REAR_OVERHANG + scale * bbT.min.x;
      }
      g.rotation.y = Math.PI;
      g.scale.setScalar(scale);
      g.position.set(tx, -1.0 - scale * minY, W0 / 2 + scale * (bbT.min.z + bbT.max.z) / 2);
      _vehGroup.add(g);
      sceneRender();
    });
  });
}

/* Линейка размеров кузова на земле: вдоль длины (деления по 1 м, подписи
   через 2 м и итоговая длина) и по ширине у заднего края последней секции */
function addRuler(x0, L, W, isLast) {
  const mat = new THREE.LineBasicMaterial({ color: 0x5a6472 });
  const v = (x, z) => new THREE.Vector3(x, 0.01, z);
  const zr = -0.4;
  const pts = [v(x0, zr), v(x0 + L, zr)];
  for (let m = 0; m <= Math.floor(L); m++)
    pts.push(v(x0 + m, zr), v(x0 + m, zr - (m % 2 ? 0.09 : 0.16)));
  pts.push(v(x0 + L, zr), v(x0 + L, zr - 0.2));
  _vehGroup.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), mat));
  for (let m = 0; m <= Math.floor(L); m += 2) {
    const lbl = makeGroundText(String(m), '#7c8aa0', 0.26);
    lbl.position.set(x0 + m, 0.015, zr - 0.42);
    _vehGroup.add(lbl);
  }
  const lend = makeGroundText(String(+L.toFixed(2)), '#37474f', 0.3);
  lend.position.set(x0 + L, 0.015, zr - 0.75);
  _vehGroup.add(lend);

  if (isLast) {
    const xw = x0 + L + 0.45;
    const p2 = [v(xw, 0), v(xw, W)];
    for (let m = 0; m <= Math.floor(W); m++) p2.push(v(xw, m), v(xw + 0.12, m));
    p2.push(v(xw, W), v(xw + 0.16, W));
    _vehGroup.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(p2), mat));
    const wl = makeGroundText(String(+W.toFixed(2)), '#37474f', 0.26);
    wl.position.set(xw + 0.5, 0.015, W / 2);
    _vehGroup.add(wl);
  }
}

/* Кабина для обычных грузовиков (Газель, 5-тонник, автосцепка):
   из модели тягача вырезается блок кабины с передними колёсами
   (в модели он отделён от рамы, X ≥ CAB_CLIP_X) и масштабируется
   по ширине кузова. Задний срез кабины — в 0.15 м перед кузовом. */
const CAB_CLIP_X = 25; // ед. модели: правее — только кабина и передние колёса

function cabGeoFrom(full) {
  const p = full.getAttribute('position').array;
  const nm = full.getAttribute('normal').array;
  const idx = [];
  for (let i = 0; i < p.length; i += 9) {
    if ((p[i] + p[i + 3] + p[i + 6]) / 3 >= CAB_CLIP_X) idx.push(i);
  }
  const pos = new Float32Array(idx.length * 9);
  const nor = new Float32Array(idx.length * 9);
  idx.forEach((s, k) => {
    pos.set(p.subarray(s, s + 9), k * 9);
    nor.set(nm.subarray(s, s + 9), k * 9);
  });
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
  return g;
}

function addCabModel(W0, placeholder) {
  loadPackedGeo('tractor', tractorB64(), geoT => {
    if (!geoT) return;                              // остаётся каркасная кабина
    if (!_geoCache.cab || _geoCache.cab === 'fail') _geoCache.cab = cabGeoFrom(geoT);
    const geo = _geoCache.cab;
    if (placeholder.parent !== _vehGroup) return;
    _vehGroup.remove(placeholder);
    geo.computeBoundingBox();
    const bb = geo.boundingBox;
    const scale = (W0 * 0.98) / (bb.max.z - bb.min.z);
    const g = new THREE.Group();
    g.add(new THREE.Mesh(geo, modelMat()));
    g.rotation.y = Math.PI;
    g.scale.setScalar(scale);
    g.position.set(-0.15 + scale * bb.min.x, -1.0 - scale * bb.min.y,
                   W0 / 2 + scale * (bb.min.z + bb.max.z) / 2);
    _vehGroup.add(g);
    sceneRender();
  });
}

/* Оси с колёсами (rear_axle) на местах колёс прицепа/секций.
   spots: [{x (м, центр оси), W (м, ширина секции)}]; ширина оси = ширине секции.
   Каркасные колёса-заглушки (ph) удаляются после успешной распаковки. */
function addAxleModels(spots, ph) {
  loadPackedGeo('axle', axleB64(), geo => {
    if (!geo || ph.parent !== _vehGroup) return;
    _vehGroup.remove(ph);
    geo.computeBoundingBox();
    const bb = geo.boundingBox;
    const cx = (bb.min.x + bb.max.x) / 2;
    for (const s of spots) {
      const sc = s.W / (bb.max.z - bb.min.z);
      const g = new THREE.Group();
      g.add(new THREE.Mesh(geo, modelMat()));
      g.rotation.y = Math.PI;
      g.scale.setScalar(sc);
      g.position.set(s.x + sc * cx, -1.0 - sc * bb.min.y,
                     s.W / 2 + sc * (bb.min.z + bb.max.z) / 2);
      _vehGroup.add(g);
    }
    sceneRender();
  });
}

/* ---------- Построение статики: транспорт + склады ---------- */

function sceneBuild(vehicle, truckIdx) {
  if(_cgGroup)_cgGroup.clear();
  _vehGroup.clear();
  _labelGroup.clear();
  _zones = [];
  _axlePos = [];
  // vehicle может быть null (все отсеки удалены) — рисуются только склады
  const sections = vehicle ? getSections(vehicle) : [];
  let offset = 0, maxW = 0, maxH = 0;
  const wheelPh = new THREE.Group(); // каркасные колёса секций — заглушка до модели оси
  const axleSpots = [];              // точки установки rear_axle

  const wheelGeo = new THREE.CylinderGeometry(0.5, 0.5, 0.32, 14);
  const lineMat = new THREE.LineBasicMaterial({ color: 0x37474f });
  const addWheelPair = (x, W, parent = _vehGroup) => {
    for (const zc of [W * 0.16, W * 0.84]) {
      const wh = new THREE.LineSegments(new THREE.EdgesGeometry(wheelGeo, 10), lineMat);
      wh.rotation.x = Math.PI / 2;
      wh.position.set(x, -0.5, zc);
      parent.add(wh);
    }
  };

  // кабина у автомашин; у седельных тягачей (передняя опора = седло, front >= 0)
  // каркас-заглушка заменяется 3D-моделью тягача
  let seatTractor = false;
  if (vehicle && vehicle.group === 'truck' && sections.length) {
    const s0 = sections[0];
    const W0 = s0.W / 1000, H0 = s0.H / 1000;
    const gap = 0.3, cabL = 2.0, cabW = W0 * 0.94;
    const cabH = Math.min(H0 * 0.97, 3.1);
    const xr = -gap, xf = -gap - cabL;
    const p = new THREE.Shape();
    p.moveTo(xr, 0.25);
    p.lineTo(xr, cabH);
    p.lineTo(xf + 0.45, cabH);
    p.lineTo(xf + 0.12, cabH * 0.62);
    p.lineTo(xf, cabH * 0.45);
    p.lineTo(xf, 0.25);
    p.lineTo(xr, 0.25);
    const cabGeo = new THREE.ExtrudeGeometry(p, { depth: cabW, bevelEnabled: false });
    const cab = new THREE.LineSegments(new THREE.EdgesGeometry(cabGeo, 10), lineMat);
    cab.position.z = (W0 - cabW) / 2;
    // заглушка: каркасная кабина + колёса; у седельных тягачей заменяется 3D-моделью
    seatTractor = !!(s0.axles && s0.axles.front >= 0);
    const ph = new THREE.Group();
    ph.add(cab);
    const ax0 = s0.axles && s0.axles.front < 0 ? s0.axles.front / 1000 : (xr + xf) / 2;
    addWheelPair(ax0, W0, ph);
    if (seatTractor) addWheelPair(s0.axles.front / 1000, W0, ph);
    _vehGroup.add(ph);
    if (seatTractor) addTractorModel(s0.axles.front / 1000, W0, ph);
    else addCabModel(W0, ph); // обычные грузовики — кабина из модели
  }

  for (const s of sections) {
    const L = s.L / 1000, W = s.W / 1000, H = s.H / 1000;
    maxW = Math.max(maxW, W); maxH = Math.max(maxH, H);
    const bounds=loadingBounds(s);
    _zones.push({kind:'sec',idx:s.index,x0:offset,z0:0,L:s.L,W:s.W,...bounds,section:s});
    if(bounds.oversize){const frame=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry((bounds.maxX-bounds.minX)/1000,bounds.H/1000,(bounds.maxY-bounds.minY)/1000)),new THREE.LineBasicMaterial({color:0xc58b46,transparent:true,opacity:.5}));frame.position.set(offset+(bounds.minX+bounds.maxX)/2000,bounds.H/2000,(bounds.minY+bounds.maxY)/2000);_vehGroup.add(frame)}

    for(const deck of floorSegments(s)){
      const slab=new THREE.Mesh(new THREE.BoxGeometry(deck.l/1000,Math.max(.02,(deck.z||0)/1000),deck.w/1000),new THREE.MeshLambertMaterial({color:(deck.z||0)>0?0x7970a0:0x8a929e}));
      slab.position.set(offset+(deck.x+deck.l/2)/1000,(deck.z||0)/2000-.01,(deck.y+deck.w/2)/1000);_vehGroup.add(slab);
    }
    for(const blocked of s.blockedRegions||[]){const shape=new THREE.Mesh(new THREE.BoxGeometry(blocked.l/1000,.05,blocked.w/1000),new THREE.MeshBasicMaterial({color:0xc06371,transparent:true,opacity:.55}));shape.position.set(offset+(blocked.x+blocked.l/2)/1000,.03,(blocked.y+blocked.w/2)/1000);_vehGroup.add(shape)}
    const frame = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(L, H, W)),
      new THREE.LineBasicMaterial({ color: 0x2c3e50 }));
    frame.position.set(offset + L / 2, H / 2, W / 2);
    _vehGroup.add(frame);
    const wall = new THREE.Mesh(new THREE.PlaneGeometry(W, H),
      new THREE.MeshBasicMaterial({ color: 0x2c3e50, transparent: true, opacity: 0.12, side: THREE.DoubleSide }));
    wall.rotation.y = Math.PI / 2;
    wall.position.set(offset, H / 2, W / 2);
    if(!bounds.oversize)_vehGroup.add(wall);

    if (vehicle.group === 'truck') {
      const axleXs = [];
      const gear=s.runningGear;const defined=gear?.kind==='rigid'?[...(gear.front||[]),...(gear.rear||[])].filter(a=>finiteNumber(a.x)):gear?.kind==='semitrailer'?[...(gear.trailerAxles||[]).filter(a=>finiteNumber(a.x)),...[...(gear.tractorFront||[]),...(gear.tractorRear||[])].filter(a=>finiteNumber(a.x)&&finiteNumber(gear.fifthWheelX)&&finiteNumber(gear.kingpinX)).map(a=>({...a,x:a.x-gear.fifthWheelX+gear.kingpinX}))]:[];
      const nAx = (isFinite(s.maxPayload) && s.maxPayload > 15000) ? (L > 10 ? 3 : 2) : 1;
      if(defined.length){axleXs.push(...defined.map(a=>a.x/1000));}
      else if (s.axles) {
        // колёса под седлом первой секции рисует заглушка/модель тягача
        if (s.axles.front >= 0 && !(s.index === 0 && seatTractor))
          axleXs.push(s.axles.front / 1000);
        for (let i = 0; i < nAx; i++)
          axleXs.push(s.axles.rear / 1000 + (i - (nAx - 1) / 2) * 1.31);
      } else {
        // позиция осей неизвестна (каталог/свой размер) — задняя ось на 2/3 длины
        for (let i = 0; i < nAx; i++)
          axleXs.push(L * 2 / 3 + (i - (nAx - 1) / 2) * 1.31);
      }
      for (const ax of axleXs) {
        addWheelPair(offset + ax, W, wheelPh);
        axleSpots.push({ x: offset + ax, W });
      }
    }
    // позиции подписей осевых нагрузок (значения задаёт sceneSetAxleLoads)
    if (s.axles) {
      _axlePos.push({ sec: s.index, kind: 'front', x: offset + s.axles.front / 1000, W });
      _axlePos.push({ sec: s.index, kind: 'rear', x: offset + s.axles.rear / 1000, W });
    }
    // ворота сзади секции; у последней правая створка приоткрыта;
    // у бортового кузова — единый борт, откинутый вниз
    const isLast = s === sections[sections.length - 1];
    addDoors(offset + L, W, H, isLast, vehicle.body === 'flatbed');
    // линейка размеров кузова
    addRuler(offset, L, W, isLast);

    offset += L + SECTION_GAP;
  }
  const totalL = Math.max(0.1, offset - SECTION_GAP);

  // колёса секций: каркас-заглушка, заменяется моделями осей rear_axle
  _vehGroup.add(wheelPh);
  if (vehicle && vehicle.group === 'truck' && axleSpots.length) addAxleModels(axleSpots, wheelPh);

  // склады слева (z<0) и справа (z>W) — пунктирные зоны на земле
  const whL = Math.max(totalL, 8); // м
  for (const side of ['L', 'R']) {
    const z0 = side === 'L' ? -WH_GAP - WH_DEPTH / 1000 : maxW + WH_GAP;
    _zones.push({ kind: 'wh', side, x0: 0, z0,
                  L: whL * 1000, W: WH_DEPTH, H: 100000 });
    const g = new THREE.PlaneGeometry(whL, WH_DEPTH / 1000);
    const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({
      color: side === 'L' ? 0xdde3ea : 0xe3eadd, transparent: true, opacity: 0.5, side: THREE.DoubleSide }));
    m.rotation.x = -Math.PI / 2;
    m.position.set(whL / 2, -0.005, z0 + WH_DEPTH / 2000);
    _vehGroup.add(m);
    const edge = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.PlaneGeometry(whL, WH_DEPTH / 1000)),
      new THREE.LineBasicMaterial({ color: 0x8a97a8 }));
    edge.rotation.x = -Math.PI / 2;
    edge.position.copy(m.position);
    _vehGroup.add(edge);

    // подпись зоны склада на земле
    const label = makeGroundText(t(side === 'L' ? 'whLeft' : 'whRight'), '#7c8aa0', 0.85);
    label.position.set(whL / 2, 0.02, z0 + WH_DEPTH / 2000);
    _vehGroup.add(label);
  }

  _orbit.target = new THREE.Vector3(totalL / 2, maxH / 3, maxW / 2);
  _orbit.dist = Math.max(totalL, maxW + 2 * (WH_GAP + WH_DEPTH / 1000), maxH) * 1.35 + 2;
  sceneRender();
}

/* ---------- Места ---------- */

function zoneOf(box) {
  if (box.zone.type === 'wh')
    return _zones.find(z => z.kind === 'wh' && z.side === box.zone.side);
  return _zones.find(z => z.kind === 'sec' && z.idx === box.zone.section);
}

function boxWorld(box) {
  const z = zoneOf(box);
  if (!z) return null;
  return {
    x: z.x0 + (box.x + box.l / 2) / 1000,
    y: (box.z + box.h / 2) / 1000,
    z: z.z0 + (box.y + box.w / 2) / 1000,
  };
}

/* Перерисовка всех мест текущего экрана.
   boxes: [{id,x,y,z,l,w,h,item,zone,invalid,...}] */
function sceneBoxes(boxes, colors, selectedId) {
  _boxes = boxes; _colors = colors; _selectedId = selectedId ?? _selectedId;
  _cargoGroup.clear();
  for (const b of boxes) {
    const wp = boxWorld(b);
    if (!wp) continue;
    const ci = b.item.ci != null ? b.item.ci : 0;
    const sel = b.id === _selectedId || (typeof selectedBoxes!=='undefined'&&selectedBoxes.has(b.id));
    const g = new THREE.BoxGeometry(b.l / 1000, b.h / 1000, b.w / 1000);
    const mat = new THREE.MeshLambertMaterial({
      color: b.invalid ? 0xe57373 : colors[ci % colors.length],
      transparent: true, opacity: b.invalid ? 0.8 : (sel ? 0.75 : 0.55),
      emissive: sel ? 0x555500 : 0x000000 });
    const m = new THREE.Mesh(g, mat);
    m.position.set(wp.x, wp.y, wp.z);
    m.userData.boxId = b.id;
    _cargoGroup.add(m);
    const e = new THREE.LineSegments(new THREE.EdgesGeometry(g),
      new THREE.LineBasicMaterial({
        color: b.invalid ? 0xd32f2f : (sel ? 0xffd600 : 0x1b1f24),
        linewidth: 2 }));
    e.position.copy(m.position);
    _cargoGroup.add(e);

    // подпись места мелким шрифтом над верхней гранью
    const label = `${ci + 1}. ${b.item.name || t('pos')}`.slice(0, 26);
    const sp = boxLabelSprite(label);
    sp.position.set(wp.x, (b.z + b.h) / 1000 + 0.14, wp.z);
    _cargoGroup.add(sp);
  }
  sceneRender();
}

function selectBox(id) {
  _selectedId = id;
  sceneBoxes(_boxes, _colors, id);
  if (_api.onSelect) _api.onSelect(id);
}

/* ---------- Выбор и перетаскивание ---------- */

function pickBox(e) {
  const r = _canvasEl.getBoundingClientRect();
  _mouse.x = ((e.clientX - r.left) / r.width) * 2 - 1;
  _mouse.y = -((e.clientY - r.top) / r.height) * 2 + 1;
  _raycaster.setFromCamera(_mouse, _camera);
  const hits = _raycaster.intersectObjects(_cargoGroup.children.filter(o => o.isMesh));
  return hits.length ? hits[0].object : null;
}

function startDrag(mesh, e) {
  const box = _boxes.find(b => b.id === mesh.userData.boxId);
  if (!box) return;
  selectBox(box.id);
  const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -box.z / 1000);
  const pt = planePoint(e, plane);
  const wp = boxWorld(box);
  _drag = { box, plane, offX: pt ? pt.x - wp.x : 0, offZ: pt ? pt.z - wp.z : 0,
            zoneKey: zoneKeyOf(box.zone), lastX: box.x, lastY: box.y };
}

function zoneKeyOf(zone) {
  return zone.type === 'wh' ? 'wh' + zone.side : 'sec' + zone.section;
}

/* Конфликт с учётом технологического зазора CARGO_GAP по горизонтали;
   по вертикали (штабель) — вплотную без зазора */
function hit3(a, o) {
  return a.x < o.x + o.l + CARGO_GAP && a.x + a.l + CARGO_GAP > o.x &&
         a.y < o.y + o.w + CARGO_GAP && a.y + a.w + CARGO_GAP > o.y &&
         a.z < o.z + o.h && a.z + a.h > o.z;
}

function planePoint(e, plane) {
  const r = _canvasEl.getBoundingClientRect();
  _mouse.x = ((e.clientX - r.left) / r.width) * 2 - 1;
  _mouse.y = -((e.clientY - r.top) / r.height) * 2 + 1;
  _raycaster.setFromCamera(_mouse, _camera);
  const out = new THREE.Vector3();
  return _raycaster.ray.intersectPlane(plane, out) ? out : null;
}

function moveDrag(e) {
  const b = _drag.box;
  const pt = planePoint(e, _drag.plane);
  if (!pt) return;
  const cx = pt.x - _drag.offX, cz = pt.z - _drag.offZ; // мировой центр места

  const zone = zoneAt(cx, cz, b);
  if (!zone) return;
  const zoneKey = zone.kind === 'wh' ? 'wh' + zone.side : 'sec' + zone.idx;
  const newZone = zone.kind === 'wh'
    ? { type: 'wh', side: zone.side }
    : { type: 'truck', truck: _api.truckIdx ? _api.truckIdx() : 0, section: zone.idx };

  // кандидат: локальные мм, жёсткий зажим в границы зоны
  let lx = (cx - zone.x0) * 1000 - b.l / 2;
  let ly = (cz - zone.z0) * 1000 - b.w / 2;
  lx=Math.max(zone.minX||0,Math.min(Math.max(zone.minX||0,(zone.maxX??zone.L)-b.l),lx));
  ly=Math.max(zone.minY||0,Math.min(Math.max(zone.minY||0,(zone.maxY??zone.W)-b.w),ly));

  const zCand = zoneKey === _drag.zoneKey ? b.z : 0; // в новой зоне — на пол
  const probe = { x: lx, y: ly, z: zCand, l: b.l, w: b.w, h: b.h };
  const near = _boxes.filter(o => o !== b &&
    (newZone.type === 'wh'
      ? o.zone.type === 'wh' && o.zone.side === newZone.side
      : o.zone.type === 'truck' && o.zone.truck === newZone.truck && o.zone.section === newZone.section));

  // примагничивание к соседям и стенкам
  const snap1 = (v, targets) => {
    for (const tg of targets) if (Math.abs(v - tg) <= SNAP) return tg;
    return v;
  };
  // цели примагничивания: грани соседей с зазором CARGO_GAP, стенки, выравнивание
  const tx=[zone.minX||0,(zone.maxX??zone.L)-b.l,0,zone.L-b.l],ty=[zone.minY||0,(zone.maxY??zone.W)-b.w,0,zone.W-b.w];
  for (const o of near) {
    tx.push(o.x + o.l + CARGO_GAP, o.x - b.l - CARGO_GAP, o.x);
    ty.push(o.y + o.w + CARGO_GAP, o.y - b.w - CARGO_GAP, o.y);
  }
  let desX = Math.round(snap1(lx, tx));
  let desY = Math.round(snap1(ly, ty));

  if (zoneKey !== _drag.zoneKey) {
    // вход в другую зону возможен только через свободное пространство
    probe.x = desX; probe.y = desY;
    if (near.some(o => hit3(probe, o))) return; // занято — остаёмся на прежнем
    b.zone = newZone; b.z = zCand;
    b.x = desX; b.y = desY;
    _drag.zoneKey = zoneKey;
  } else {
    // движение с упором: сначала по X (при прежнем Y), затем по Y —
    // место скользит вдоль соседей и не может их пересечь
    const prevX = _drag.lastX, prevY = _drag.lastY;
    probe.y = prevY;
    probe.x = desX;
    let hits = near.filter(o => hit3(probe, o));
    if (hits.length) {
      if (desX > prevX) probe.x = Math.min(...hits.map(o => o.x - b.l - CARGO_GAP));
      else if (desX < prevX) probe.x = Math.max(...hits.map(o => o.x + o.l + CARGO_GAP));
      else probe.x = prevX;
      if (near.some(o => hit3(probe, o))) probe.x = prevX;
    }
    probe.y = desY;
    hits = near.filter(o => hit3(probe, o));
    if (hits.length) {
      if (desY > prevY) probe.y = Math.min(...hits.map(o => o.y - b.w - CARGO_GAP));
      else if (desY < prevY) probe.y = Math.max(...hits.map(o => o.y + o.w + CARGO_GAP));
      else probe.y = prevY;
      if (near.some(o => hit3(probe, o))) probe.y = prevY;
    }
    b.x=Math.round(Math.max(zone.minX||0,Math.min(Math.max(zone.minX||0,(zone.maxX??zone.L)-b.l),probe.x)));
    b.y=Math.round(Math.max(zone.minY||0,Math.min(Math.max(zone.minY||0,(zone.maxY??zone.W)-b.w),probe.y)));
  }
  _drag.lastX = b.x; _drag.lastY = b.y;

  // двигаем только mesh выделенного места (полная перерисовка — на отпускании)
  const wp = boxWorld(b);
  for (const o of _cargoGroup.children) {
    if (o.userData && o.userData.boxId === b.id) o.position.set(wp.x, wp.y, wp.z);
    else if (o.isLineSegments && o.userData.forBox === b.id) o.position.set(wp.x, wp.y, wp.z);
  }
  sceneRender();
}

function sameZone(a, b) {
  if (a.zone.type !== b.zone.type) return false;
  if (a.zone.type === 'wh') return a.zone.side === b.zone.side;
  return a.zone.section === b.zone.section;
}

function zoneAt(wx, wz, box) {
  // секции: приоритет, с допуском по ширине
  for (const z of _zones) {
    if (z.kind !== 'sec') continue;
    if(wx>=z.x0+(z.minX||0)/1000-.3&&wx<=z.x0+(z.maxX??z.L)/1000+.3&&wz>=(z.minY||0)/1000-.45&&wz<=(z.maxY??z.W)/1000+.45)return z;
  }
  for (const z of _zones) {
    if (z.kind !== 'wh') continue;
    if (z.side === 'L' && wz < 0) return z;
    if (z.side === 'R' && wz > 0) return z;
  }
  return null;
}

function endDrag() {
  const b = _drag.box;
  _drag = null;
  if (_api.onDrop) _api.onDrop(b); // приложение: осадка по ярусу, валидация, перерисовка
}

function sceneSelectedId() { return _selectedId; }

/* Подписи значений осевых нагрузок у колёс.
   loadsBySec: { [secIndex]: {front, rear, frontOver, rearOver} | null } */
function sceneSetAxleLoads(loadsBySec) {
  _labelGroup.clear();
  for (const ap of _axlePos) {
    const al = loadsBySec && loadsBySec[ap.sec];
    if (!al) continue;
    const val = ap.kind === 'front' ? al.front : al.rear;
    const over = ap.kind === 'front' ? al.frontOver : al.rearOver;
    const sp = makeTextSprite(`${val} ${t('kg')}`, over ? '#d32f2f' : '#37474f', 0.42);
    sp.position.set(ap.x, -1.35, -0.35);
    _labelGroup.add(sp);
  }
  sceneRender();
}

function sceneScreenshot() {
  sceneRender();
  return _renderer.domElement.toDataURL('image/png');
}

function sceneSetCg(points){if(!_cgGroup)return;for(const child of _cgGroup.children){child.geometry?.dispose();if(child.material?.map)child.material.map.dispose();child.material?.dispose()}_cgGroup.clear();for(const point of points){if(!point.visible)continue;const zone=_zones.find(z=>z.kind==='sec'&&z.idx===point.section);if(!zone)continue;const sphere=new THREE.Mesh(new THREE.SphereGeometry(.13,16,12),new THREE.MeshBasicMaterial({color:point.manual?0xc78d48:0x337f99,depthTest:false,depthWrite:false}));sphere.position.set(zone.x0+point.x/1000,Math.max(.14,point.z/1000),point.y/1000);sphere.userData.section=point.section;sphere.userData.manual=point.manual;sphere.renderOrder=999;_cgGroup.add(sphere);const label=makeTextSprite(point.manual?'Целевой ЦМ':'Фактический ЦМ',point.manual?'#9b6e39':'#337f99',.3);label.position.copy(sphere.position);label.position.y+=.3;label.material.depthTest=false;label.renderOrder=1000;_cgGroup.add(label)}sceneRender()}
function pickCg(e){if(!_cgGroup)return null;const r=_canvasEl.getBoundingClientRect();_mouse.x=((e.clientX-r.left)/r.width)*2-1;_mouse.y=-((e.clientY-r.top)/r.height)*2+1;_raycaster.setFromCamera(_mouse,_camera);return _raycaster.intersectObjects(_cgGroup.children.filter(o=>o.isMesh&&o.userData.manual))[0]?.object||null}
