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
let _vehicleBounds={minX:0,maxX:14,minY:-1.5,maxY:4,W:2.5};
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

/* ---------- Построение статики: транспорт + склады ---------- */

function sceneBuild(vehicle, truckIdx) {
  if(_cgGroup)_cgGroup.clear();
  _vehGroup.traverse(o=>{o.geometry?.dispose();if(o.material){for(const m of Array.isArray(o.material)?o.material:[o.material]){m.map?.dispose();m.dispose()}}});
  _vehGroup.clear();
  _labelGroup.clear();
  _zones = [];
  _axlePos = [];
  // vehicle может быть null (все отсеки удалены) — рисуются только склады
  const sections = vehicle ? getSections(vehicle) : [];
  let offset = 0, maxW = 0, maxH = 0;
  let minX=0,maxX=0;
  const tireMat=new THREE.MeshPhongMaterial({color:0x25313d,shininess:8});
  const rimMat=new THREE.MeshPhongMaterial({color:0xb8c5d2,shininess:55});
  const chassisMat=new THREE.MeshLambertMaterial({color:0x576779});
  const addPart=(parent,l,h,w,x,y,z,mat)=>{const mesh=new THREE.Mesh(new THREE.BoxGeometry(l,h,w),mat);mesh.position.set(x,y,z);parent.add(mesh);return mesh};
  const addAxle=(axis,W,x0)=>{
    const group=new THREE.Group();group.name='axle:'+axis.code;group.userData={axle:axis};
    const x=x0+axis.x/1000,r=axis.radius/1000,y=axis.centerY/1000;
    const track=Math.min(W,2.5),mid=W/2,tw=.24;
    const shaft=new THREE.Mesh(new THREE.CylinderGeometry(.055,.055,track-.32,12),chassisMat);
    shaft.rotation.x=Math.PI/2;shaft.position.set(x,y,mid);group.add(shaft);
    for(const side of [-1,1]){
      const z=mid+side*(track/2-tw/2-.035);
      const wheel=new THREE.Mesh(new THREE.CylinderGeometry(r,r,tw,32),tireMat);
      wheel.name='tire';wheel.rotation.x=Math.PI/2;wheel.position.set(x,y,z);group.add(wheel);
      const rim=new THREE.Mesh(new THREE.CylinderGeometry(r*.6,r*.6,tw+.008,24),rimMat);
      rim.rotation.x=Math.PI/2;rim.position.copy(wheel.position);group.add(rim);
      const hub=new THREE.Mesh(new THREE.CylinderGeometry(r*.2,r*.2,tw+.025,16),chassisMat);
      hub.rotation.x=Math.PI/2;hub.position.copy(wheel.position);group.add(hub);
    }
    _vehGroup.add(group);
    minX=Math.min(minX,x-r);maxX=Math.max(maxX,x+r);
  };
  // The old STL contained its own axles. A wheel-free cab/chassis now uses
  // the same explicit axle layout as the labels and calculation profile.
  const addCab=(layout,W,x0)=>{
    const front=layout.axles.find(a=>a.kind==='tractor-front'||a.kind==='rigid-front');if(!front)return;
    const rear=layout.axles.filter(a=>a.kind==='tractor-rear');
    const x=x0+front.x/1000,ground=layout.groundY/1000;
    const cab=new THREE.Group();cab.name='cab-without-embedded-wheels';
    const cabW=Math.min(W*.95,2.4),base=front.wheelTop/1000+.08,h=2.1;
    addPart(cab,2.05,h,cabW,x-.03,base+h/2,W/2,new THREE.MeshPhongMaterial({color:0xd4dce8,shininess:24}));
    const glass=new THREE.MeshLambertMaterial({color:0x647e98});
    addPart(cab,.015,.72,cabW*.86,x-1.06,base+1.35,W/2,glass);
    for(const z of [W/2-cabW/2-.009,W/2+cabW/2+.009])addPart(cab,1.13,.72,.015,x-.2,base+1.35,z,glass);
    addPart(cab,.12,.16,cabW*.98,x-1.1,base+.1,W/2,chassisMat);
    const end=rear.length?x0+Math.max(...rear.map(a=>a.x))/1000+.65:x+.85;
    addPart(cab,Math.max(.5,end-x),.18,.8,(x+end)/2,ground+.63,W/2,chassisMat);
    _vehGroup.add(cab);minX=Math.min(minX,x-1.2);
  };

  for (const s of sections) {
    const L = s.L / 1000, W = s.W / 1000, H = s.H / 1000;
    maxW = Math.max(maxW, W); maxH = Math.max(maxH, H);
    const bounds=loadingBounds(s);
    _zones.push({kind:'sec',idx:s.index,x0:offset,z0:0,L:s.L,W:s.W,...bounds,section:s});
    if(bounds.oversize){const frame=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry((bounds.maxX-bounds.minX)/1000,bounds.H/1000,(bounds.maxY-bounds.minY)/1000)),new THREE.LineBasicMaterial({color:0xc58b46,transparent:true,opacity:.5}));frame.position.set(offset+(bounds.minX+bounds.maxX)/2000,bounds.H/2000,(bounds.minY+bounds.maxY)/2000);_vehGroup.add(frame)}

    for(const deck of floorSegments(s)){
      const slab=new THREE.Mesh(new THREE.BoxGeometry(deck.l/1000,.04,deck.w/1000),new THREE.MeshLambertMaterial({color:(deck.z||0)>0?0x7970a0:0x8a929e}));
      slab.position.set(offset+(deck.x+deck.l/2)/1000,(deck.z||0)/1000-.02,(deck.y+deck.w/2)/1000);_vehGroup.add(slab);
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
    if(!bounds.oversize&&vehicle.body!=='flatbed')_vehGroup.add(wall);

    if (vehicle.group === 'truck') {
      const layout=sectionAxleLayout(s);_zones[_zones.length-1].axleLayout=layout;
      for(const axis of layout.axles)addAxle(axis,W,offset);
      if(s.index===0)addCab(layout,W,offset);
      for(const module of layout.modules){
        const top=module.z/1000;
        addPart(_vehGroup,module.l/1000,.08,W,offset+(module.x+module.l/2)/1000,top-.04,W/2,chassisMat);
        addPart(_vehGroup,module.l/1000,.16,W*.32,offset+(module.x+module.l/2)/1000,top-.16,W/2,chassisMat);
        minX=Math.min(minX,offset+module.x/1000);maxX=Math.max(maxX,offset+(module.x+module.l)/1000);
        const front=module.x<0,edge=front?(module.x+module.l)/1000:module.x/1000;
        const floorEdge=front?0:L;
        const transition=new THREE.Shape();transition.moveTo(floorEdge,-.04);transition.lineTo(floorEdge,0);transition.lineTo(edge,top);transition.lineTo(edge,top-.08);transition.closePath();
        const ramp=new THREE.Mesh(new THREE.ExtrudeGeometry(transition,{depth:W*.32,bevelEnabled:false}),chassisMat);ramp.position.set(offset,0,W*.34);_vehGroup.add(ramp);
      }
      if(layout.modules.length){const rear=layout.axles.filter(a=>a.kind==='tractor-rear').at(-1);if(rear){const x=rear.x/1000-.5,end=layout.modules[0].x/1000;addPart(_vehGroup,end-x,.12,W*.32,offset+(x+end)/2,.74,W/2,chassisMat)}}
      // Flatbed neck joins the loading deck to the tractor outside usable cargo space.
      if(s.trailer&&layout.kind==='semitrailer'&&!layout.modules.length){
        const drawing=trailerDrawingLayout(s.id,s.L),kingpin=s.runningGear?.kingpinX??drawing?.kingpinX;
        if(Number.isFinite(kingpin)&&kingpin<0){
          const neckL=-kingpin/1000+.4,top=.55;
          addPart(_vehGroup,neckL,.08,W*.92,offset-neckL/2,top-.04,W/2,chassisMat);
          addPart(_vehGroup,.16,top+.08,W*.32,offset-.08,top/2-.04,W/2,chassisMat);
          const bottom=layout.groundY/1000+.72;
          addPart(_vehGroup,.55,top-bottom,.6,offset+kingpin/1000,(top+bottom)/2,W/2,chassisMat);
          minX=Math.min(minX,offset-neckL);
        }
      }
      addPart(_vehGroup,L,.08,Math.min(W*.32,.85),offset+L/2,-.1,W/2,chassisMat);
    }
    // ворота сзади секции; у последней правая створка приоткрыта;
    // у бортового кузова — единый борт, откинутый вниз
    const isLast = s === sections[sections.length - 1];
    if(!s.trailer)addDoors(offset + L, W, H, isLast, vehicle.body === 'flatbed');
    // линейка размеров кузова
    addRuler(offset, L, W, isLast);

    offset += L + SECTION_GAP;
  }
  const totalL = Math.max(0.1, offset - SECTION_GAP);

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

  maxX=Math.max(maxX,totalL);
  _vehicleBounds={minX,maxX,minY:Math.min(-1.5,...sections.map(s=>-(s.trailer?.loadingHeight||1250)/1000-.9)),maxY:maxH,W:maxW};
  _orbit.target = new THREE.Vector3((minX+maxX)/2, maxH/3, maxW/2);
  _orbit.dist = Math.max(maxX-minX, maxW + 2 * (WH_GAP + WH_DEPTH / 1000), maxH) * 1.35 + 2;
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
