/* PackOnTruck — логика приложения (v0.5).
   Проект: несколько грузовых площадей + общий груз + склады L/R.
   Авторасчёт переливом по площадям; перемещённые вручную места (pinned)
   сохраняются при пересчёте; «Расставить заново» игнорирует их. */

const WH_DEPTH_MM = 3200;
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

let project = { trucks: [], cur: 0, boxes: [], seq: 1 };
let currentItems = [];

function $(id) { return document.getElementById(id); }
function curTruck() { return project.trucks[project.cur]; }
function whLenMM() {
  let m = 8000;
  for (const t of project.trucks) {
    const L = getSections(t.vehicle).reduce((s, x) => s + x.L, 0) + 600 * (getSections(t.vehicle).length - 1);
    m = Math.max(m, L);
  }
  return m;
}

/* ---------- Инициализация ---------- */

function init() {
  buildVehicleSelect();
  applyLang();
  sceneInit($('scene'), {
    onSelect: onBoxSelect,
    onDrop: onBoxDrop,
    onCgMove:onCgMove,
    truckIdx: () => project.cur,
  });
  project.trucks.push({ vehicle: vehicleById('tent82') });
  $('vehSel').value='tent82';
  addCargoRow({ name: 'Паллета EUR', L: 1200, W: 800, H: 1500, weight: 500, qty: 10, tiers: 1 });

  $('btnCalc').onclick = () => calc(false);
  $('btnAuto').onclick = () => calc(true);
  $('btnPdf').onclick = exportPdf;
  $('btnShot').onclick = downloadScreenshot;
  $('btnAdd').onclick = () => addCargoRow({});
  $('btnImport').onclick = () => $('fileInput').click();
  $('fileInput').onchange = e => {
    const f = e.target.files[0];
    if (!f) return;
    importFile(f, items => {
      $('cargoRows').innerHTML = '';
      items.forEach(addCargoRow);
      e.target.value = '';
    }, () => alert(t('importHint')));
  };
  $('langSel').onchange = e => { LANG = e.target.value; applyLang(); refreshAll(); };
  $('vehSel').onchange = onVehicleSelChange;
  $('presetSel').onchange = e => {
    const p = CARGO_PRESETS.find(x => x.id === e.target.value);
    if (p) addCargoRow({ name: p.name[LANG] || p.name.ru, L: p.L, W: p.W, H: p.H, weight: p.weight, qty: 1, tiers: 1 });
    e.target.value = '';
  };

  // «Начать» в заглушке — скрыть сообщение и развернуть боковую панель
  $('noSpaceStart').onclick = () => {
    _noSpaceDismissed = true;
    $('noSpace').classList.remove('show');
    $('panel').classList.remove('collapsed');
  };
  // каталог
  $('catClose').onclick = () => $('catModal').classList.remove('show');
  $('catModal').onclick = e => { if (e.target === $('catModal')) $('catModal').classList.remove('show'); };
  $('catSearch').oninput = e => renderCatalog(e.target.value);
  for (const id of ['cvL', 'cvW', 'cvH']) // ручная правка размеров сбрасывает выбор из каталога
    $(id).addEventListener('input', () => { _catalogName = null; _catalogFlatbed = false; });

  // площади
  $('btnAddSpace').onclick = addSpace;
  $('btnDelSpace').onclick = removeSpace;
  $('navPrev').onclick = () => switchTruck(project.cur - 1);
  $('navNext').onclick = () => switchTruck(project.cur + 1);

  // тулбар выделенного места
  $('bbRotate').onclick = () => withSel(rotateBox);
  $('bbRotateVertical').onclick=()=>withSel(b=>rotateBox(b,'vertical'));
  $('oversizeEnabled').onchange=updateOversize;
  for(const id of ['overFront','overRear','overLeft','overRight','overHeight'])$(id).onchange=updateOversize;
  $('cgSection').onchange=showStats;$('cgManual').onchange=updateManualCg;
  for(const id of ['cgX','cgY'])$(id).oninput=()=>{$(id+'Value').value=$(id).value;updateManualCg(true)};
  for(const id of ['cgXValue','cgYValue'])$(id).onchange=()=>{$(id.replace('Value','')).value=$(id).value;updateManualCg(true)};
  $('bbUp').onclick = () => withSel(tierUp);
  $('bbDown').onclick = () => withSel(tierDown);
  $('bbWhL').onclick = () => withSel(b => toWarehouse(b, 'L'));
  $('bbWhR').onclick = () => withSel(b => toWarehouse(b, 'R'));
  $('bbDel').onclick = () => withSel(deleteBox);
  window.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.key === 'r' || e.key === 'R' || e.key === 'к' || e.key === 'К') withSel(rotateBox);
    else if (e.key === 'PageUp' || e.key === 'ArrowUp') { e.preventDefault(); withSel(tierUp); }
    else if (e.key === 'PageDown' || e.key === 'ArrowDown') { e.preventDefault(); withSel(tierDown); }
    else if (e.key === 'Delete') withSel(deleteBox);
  });

  // сворачиваемая панель
  const panel = $('panel');
  let hideTimer = null, pinned = true;
  $('panelPin').classList.add('on');
  $('panelPin').onclick = () => {
    pinned = !pinned;
    $('panelPin').classList.toggle('on', pinned);
    if (pinned) panel.classList.remove('collapsed');
  };
  panel.addEventListener('mouseenter', () => {
    clearTimeout(hideTimer);
    panel.classList.remove('collapsed');
  });
  panel.addEventListener('mouseleave', () => {
    if (pinned) return;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => panel.classList.add('collapsed'), 500);
  });
  $('panelHandle').onclick = () => panel.classList.remove('collapsed');

  refreshAll();
}

/* ---------- Грузовые площади ---------- */

/* Размеры транспорта в метрах для подписи в списке */
function vehDims(v) {
  const f = x => +(x / 1000).toFixed(2);
  if (v.sections) {
    const s = v.sections;
    return `${s.map(x => f(x.L)).join('+')}×${f(s[0].W)}×${f(s[0].H)} ${t('m')}`;
  }
  return `${f(v.L)}×${f(v.W)}×${f(v.H)} ${t('m')}`;
}

function buildVehicleSelect() {
  const sel = $('vehSel');
  sel.innerHTML = '';
  const all = [...VEHICLE_TEMPLATES, ...loadCustomVehicles()];
  for (const v of all) {
    const o = document.createElement('option');
    o.value = v.id;
    const nm = typeof v.name === 'object' ? (v.name[LANG] || v.name.ru) : v.name;
    o.textContent = `${nm} (${vehDims(v)})`;
    sel.appendChild(o);
  }
  const oc = document.createElement('option');
  oc.value = '__custom__';
  oc.textContent = t('custom');
  sel.appendChild(oc);
  const ocat = document.createElement('option');
  ocat.value = '__catalog__';
  ocat.textContent = t('catalog');
  sel.appendChild(ocat);
}

let _prevVehSel = null;
function onVehicleSelChange() {
  const v = $('vehSel').value;
  if (v === '__catalog__') {
    openCatalog();
    $('vehSel').value = _prevVehSel || VEHICLE_TEMPLATES[0].id;
    return;
  }
  _prevVehSel = v;
  updateTrailerControls();
  $('customVeh').style.display = v === '__custom__' ? 'grid' : 'none';
}

/* ---------- Каталог грузовых площадей (docs/vehicles-catalog.md) ---------- */

let _catalogName = null;    // имя выбранной в каталоге модели (для «своего размера»)
let _catalogFlatbed = false; // выбранная модель — бортовая (открытый кузов)

function openCatalog() {
  renderCatalog($('catSearch').value || '');
  $('catModal').classList.add('show');
  $('catSearch').focus();
}

function renderCatalog(filter) {
  if (typeof VEHICLE_CATALOG === 'undefined') { $('catList').textContent = '—'; return; }
  const q = filter.trim().toLowerCase();
  const rows = VEHICLE_CATALOG
    .filter(e => !q || e.name.toLowerCase().includes(q) || e.cls.toLowerCase().includes(q))
    .map((e, i) => `<tr class="pick" data-i="${VEHICLE_CATALOG.indexOf(e)}">
      <td>${e.name}</td><td class="cls">${e.cls}</td>
      <td>${(e.gp / 1000).toFixed(2)} т</td>
      <td>${(e.L / 1000).toFixed(2)}×${(e.W / 1000).toFixed(2)}×${(e.H / 1000).toFixed(2)}</td>
      <td>${e.vol || '—'}</td></tr>`).join('');
  $('catList').innerHTML = `<table class="cat">
    <tr><th>${t('name')}</th><th></th><th>${t('payload')}</th><th>${t('size')}</th><th>${t('m3')}</th></tr>
    ${rows}</table>`;
  $('catList').querySelectorAll('tr.pick').forEach(tr => {
    tr.onclick = () => pickCatalog(VEHICLE_CATALOG[+tr.dataset.i]);
  });
}

function pickCatalog(e) {
  if(e.templateId){$('catModal').classList.remove('show');$('vehSel').value=e.templateId;onVehicleSelChange();addSpace();return;}
  $('catModal').classList.remove('show');
  // бортовой (открытый кузов): предельная высота места 3 м вместо высоты борта
  const flatbed = /борт|самосвал|пикап/i.test(e.name);
  $('vehSel').value = '__custom__';
  _prevVehSel = '__custom__';
  $('customVeh').style.display = 'grid';
  $('cvL').value = e.L;
  $('cvW').value = e.W;
  $('cvH').value = flatbed ? 3000 : e.H;
  $('cvP').value = e.gp;
  _catalogName = e.name;
  _catalogFlatbed = flatbed;
  addSpace(); // площадь добавляется сразу, без «+ Площадь»
}

function selectedVehicle() {
  const id = $('vehSel').value;
  if (id !== '__custom__') {
    const v = vehicleById(id) || loadCustomVehicles().find(x => x.id === id);
    if (v) return configuredTrailer(v);
  }
  const v = { name: _catalogName ? { ru: _catalogName, en: _catalogName } : null,
              group: 'truck', L: +$('cvL').value, W: +$('cvW').value,
              H: +$('cvH').value, maxPayload: +$('cvP').value || Infinity, axles: null,
              body: _catalogFlatbed ? 'flatbed' : undefined };
  return v.L && v.W && v.H ? v : null;
}

function addSpace() {
  const v = selectedVehicle();
  if (!v) return;
  project.trucks.push({ vehicle: v });
  switchTruck(project.trucks.length - 1);
}

function removeSpace() {
  if (!project.trucks.length) return;
  const ti = project.cur;
  // места этого отсека — на склад справа
  for (const b of project.boxes) {
    if (b.zone.type === 'truck' && b.zone.truck === ti) {
      b.zone = { type: 'wh', side: 'R' };
      placeWhSpot(b);
    } else if (b.zone.type === 'truck' && b.zone.truck > ti) {
      b.zone.truck--;
    }
  }
  project.trucks.splice(ti, 1);
  project.cur = Math.min(Math.max(0, ti - 1), project.trucks.length - 1);
  refreshAll(); // при 0 отсеков появится приглашение выбрать новый
}

function switchTruck(i) {
  if (i < 0 || i >= project.trucks.length) return;
  project.cur = i;
  refreshAll();
}

function truckName(t) {
  const v = t.vehicle;
  return v.name ? (v.name[LANG] || v.name.ru) : `${v.L}×${v.W}×${v.H}`;
}

/* ---------- Таблица груза ---------- */

function addCargoRow(data) {
  const tr = document.createElement('tr');
  const color = CARGO_COLORS[$('cargoRows').children.length % CARGO_COLORS.length];
  tr.innerHTML = `
    <td><span class="sw" style="background:${color}"></span></td>
    <td><input class="c-name" value="${escapeHtml(data.name || '')}"></td>
    <td><input class="c-l num noarrow" type="number" value="${data.L || ''}"></td>
    <td><input class="c-w num noarrow" type="number" value="${data.W || ''}"></td>
    <td><input class="c-h num noarrow" type="number" value="${data.H || ''}"></td>
    <td><input class="c-wt num noarrow" type="number" value="${data.weight != null ? data.weight : ''}"></td>
    <td><input class="c-q num" type="number" value="${data.qty != null ? data.qty : 1}" min="0"></td>
    <td><input class="c-tiers tiers" type="number" value="${data.tiers || 1}" min="1" max="100"></td>
    <td><input class="c-rot" type="checkbox" ${data.rotatable === false ? '' : 'checked'}></td>
    <td><input class="c-grp" value="${escapeHtml(data.group || '')}"></td>
    <td><button class="del" title="${t('delRow')}">×</button></td>`;
  tr.querySelector('.del').onclick = () => tr.remove();
  $('cargoRows').appendChild(tr);
}

function readCargo() {
  const items = [];
  for (const tr of $('cargoRows').children) {
    const g = c => tr.querySelector(c);
    if (!+g('.c-l').value || !+g('.c-w').value || !+g('.c-h').value) continue;
    const it = makeCargoItem({
      name: g('.c-name').value, L: g('.c-l').value, W: g('.c-w').value, H: g('.c-h').value,
      weight: g('.c-wt').value, qty: g('.c-q').value,
      tiers: g('.c-tiers').value, rotatable: g('.c-rot').checked,
      group: g('.c-grp').value,
    });
    it.ci = items.length;
    it._qtyInput = tr.querySelector('.c-q'); // для декремента при удалении места
    items.push(it);
  }
  // индексы групп (в порядке появления) — упаковщик держит группу кластером
  const gmap = new Map();
  for (const it of items) {
    if (it.group) {
      if (!gmap.has(it.group)) gmap.set(it.group, gmap.size);
      it.gi = gmap.get(it.group);
    } else it.gi = null;
  }
  return items;
}

/* ---------- Авторасчёт (перелив по площадям) ---------- */

function calc(auto) {
  if (!project.trucks.length) { alert(t('pickSpaceFirst')); return; }
  const items = readCargo();
  if (!items.length) { alert(t('noCargo')); return; }
  currentItems = items;

  if (auto) {
    project.boxes = [];
  } else {
    // оставить закреплённые пользователем; закреплено только МЕСТОПОЛОЖЕНИЕ
    // и ориентация — размеры/вес обновляются из текущей строки позиции
    project.boxes = project.boxes.filter(b => b.pinned && b.item.ci != null && items[b.item.ci]);
    for (const b of project.boxes) {
      const old = b.item;
      const it = items[old.ci];
      const resized=resizedCargo(b,it);
      b.item = it;
      Object.assign(b,resized);
    }
  }

  // остаток к раскладке = qty − закреплённые
  const remain = items.map(it => ({ ...it }));
  for (const b of project.boxes) if (remain[b.item.ci]) remain[b.item.ci].qty--;
  let rem = remain.filter(it => it.qty > 0);

  project.trucks.forEach((tr, ti) => {
    getSections(tr.vehicle).forEach(sec => {
      if (!rem.length) return;
      const fixed = project.boxes
        .filter(b => b.zone.type === 'truck' && b.zone.truck === ti && b.zone.section === sec.index)
        .map(b => ({ x: b.x, y: b.y, z: b.z, l: b.l, w: b.w, h: b.h,
                     weight: b.item.weight, item: b.item, loadOnTop: 0, below: null, level: null }));
      const bounds=loadingBounds(sec);
      const virtual={...sec,L:bounds.maxX-bounds.minX,W:bounds.maxY-bounds.minY,H:bounds.H,axles:sec.axles?{...sec.axles,front:sec.axles.front-bounds.minX,rear:sec.axles.rear-bounds.minX}:null};
      const r=pack(virtual,rem,fixed.map(b=>({...b,x:b.x-bounds.minX,y:b.y-bounds.minY})));
      for (const p of r.placed) {
        project.boxes.push({ id: project.seq++, item: p.item,
          l: p.l, w: p.w, h: p.h, x: p.x+bounds.minX, y: p.y+bounds.minY, z: p.z,
          zone: { type: 'truck', truck: ti, section: sec.index }, pinned: false });
      }
      rem = r.unplaced.map(u => ({ ...u.item, qty: u.qty }));
    });
  });

  // не влезшее — на склад справа
  for (const it of rem) {
    for (let i = 0; i < it.qty; i++) {
      const b = { id: project.seq++, item: it, l: it.L, w: it.W, h: it.H,
                  x: 0, y: 0, z: 0, zone: { type: 'wh', side: 'R' }, pinned: false };
      placeWhSpot(b);
      project.boxes.push(b);
    }
  }
  $('btnPdf').disabled = false;
  refreshAll();
}

/* Размещение на складе:
   1) штабель на стопку той же позиции (по ярусности места);
   2) свободный участок пола этого склада;
   3) не хватает места — другой склад (справа → слева и наоборот). */
const WH_SPACING = 100; // мм, шаг между местами на складе

function findWhFloorSpot(b, others) {
  const LEN = whLenMM(), DEPTH = WH_DEPTH_MM;
  const floor = others.filter(o => o.z === 0);
  for (let y = 0; y + b.w <= DEPTH; y += WH_SPACING) {
    let x = 0;
    while (x + b.l <= LEN) {
      const c = floor.find(o =>
        x < o.x + o.l + WH_SPACING && x + b.l + WH_SPACING > o.x &&
        y < o.y + o.w + WH_SPACING && y + b.w + WH_SPACING > o.y);
      if (!c) return { x, y };
      x = c.x + c.l + WH_SPACING; // прыжок за конфликтующее место
    }
  }
  return null;
}

function placeWhSpot(b) {
  const sides = b.zone.side === 'L' ? ['L', 'R'] : ['R', 'L'];
  for (const side of sides) {
    const others = project.boxes.filter(o => o !== b &&
      o.zone.type === 'wh' && o.zone.side === side);
    // штабель: стопка той же позиции и ориентации, ярусов меньше лимита
    if (b.item.tiers > 1) {
      const piles = new Map();
      for (const o of others) {
        if (o.item.ci !== b.item.ci || o.l !== b.l || o.w !== b.w) continue;
        const key = o.x + ':' + o.y;
        (piles.get(key) || piles.set(key, []).get(key)).push(o);
      }
      for (const pile of piles.values()) {
        if (pile.length >= b.item.tiers) continue;
        b.zone = { type: 'wh', side };
        b.x = pile[0].x;
        b.y = pile[0].y;
        b.z = Math.max(...pile.map(o => o.z + o.h));
        return;
      }
    }
    // пол склада
    const spot = findWhFloorSpot(b, others);
    if (spot) {
      b.zone = { type: 'wh', side };
      b.x = spot.x; b.y = spot.y; b.z = 0;
      return;
    }
  }
  // оба склада заполнены — в начало исходного (пометится красным)
  b.x = 0; b.y = 0; b.z = 0;
}

/* ---------- Ручные операции ---------- */

function selBox() {
  const id = sceneSelectedId();
  return project.boxes.find(b => b.id === id) || null;
}
function withSel(fn) {
  const b = selBox();
  if (b) fn(b);
}

/* Строгое перекрытие по XY — для определения опоры под местом (штабель) */
function overlapsXY(a, b) {
  return a.x < b.x + b.l && a.x + a.l > b.x && a.y < b.y + b.w && a.y + a.w > b.y;
}
/* Близость по XY с учётом технологического зазора CARGO_GAP — для конфликтов */
function nearXY(a, b) {
  return a.x < b.x + b.l + CARGO_GAP && a.x + a.l + CARGO_GAP > b.x &&
         a.y < b.y + b.w + CARGO_GAP && a.y + a.w + CARGO_GAP > b.y;
}
function collides3(a, o) {
  return nearXY(a, o) && a.z < o.z + o.h && a.z + a.h > o.z;
}
function zoneMates(b) {
  return project.boxes.filter(o => o !== b &&
    o.zone.type === b.zone.type &&
    (b.zone.type === 'wh' ? o.zone.side === b.zone.side
      : o.zone.truck === b.zone.truck && o.zone.section === b.zone.section));
}
function boxCollides(b) { return zoneMates(b).some(o => collides3(b, o)); }

function zoneDims(b) {
  if (b.zone.type === 'wh') return { L: whLenMM(), W: WH_DEPTH_MM, H: Infinity };
  const sec = getSections(project.trucks[b.zone.truck].vehicle)
    .find(s => s.index === b.zone.section);
  return sec?{L:sec.L,W:sec.W,...loadingBounds(sec)}:{L:Infinity,W:Infinity,H:Infinity,minX:0,minY:0,maxX:Infinity,maxY:Infinity};
}
function clampToZone(b) {
  const d = zoneDims(b);
  b.x=Math.max(d.minX||0,Math.min(Math.max(d.minX||0,(d.maxX??d.L)-b.l),b.x));
  b.y=Math.max(d.minY||0,Math.min(Math.max(d.minY||0,(d.maxY??d.W)-b.w),b.y));
}
function fitsHeight(b) {
  const d = zoneDims(b);
  return b.z + b.h <= d.H;
}

/* Поворот на 90°: если после поворота место упирается в соседей
   или не помещается — поворот отменяется */
function rotateBox(b,axis='horizontal') {
  if(b.item.rotatable===false)return;
  const old={...b,orientation:boxOrientation(b)};
  Object.assign(b,rotatedCargo(b,axis));
  clampToZone(b);
  const dimensions=zoneDims(b);
  if(boxCollides(b)||!fitsHeight(b)||b.l>(dimensions.maxX??dimensions.L)-(dimensions.minX||0)||b.w>(dimensions.maxY??dimensions.W)-(dimensions.minY||0)){Object.assign(b,old);return;}
  b.pinned = true;
  refreshAll();
}

/* Ярус выше/ниже: перебираем уровни (верхние грани мест зоны),
   берём ближайший СВОБОДНЫЙ в пределах высоты кузова.
   Требования перекрытия по XY нет: место поднимается на уровень
   соседней стопки, дальше его можно перетащить поверх — при
   отпускании оно осядет на стопку. */
function tierUp(b) {
  const tops = [...new Set(zoneMates(b).map(o => o.z + o.h).filter(v => v > b.z))]
    .sort((a, c) => a - c);
  const save = b.z;
  for (const z of tops) {
    b.z = z;
    if (!boxCollides(b) && fitsHeight(b)) { b.pinned = true; refreshAll(); return; }
  }
  b.z = save;
}
function tierDown(b) {
  const levels = [...new Set([0, ...zoneMates(b).map(o => o.z + o.h).filter(v => v < b.z)])]
    .sort((a, c) => c - a);
  const save = b.z;
  for (const z of levels) {
    b.z = z;
    if (!boxCollides(b)) { b.pinned = true; refreshAll(); return; }
  }
  b.z = save;
}

function toWarehouse(b, side) {
  b.zone = { type: 'wh', side };
  b.z = 0;
  placeWhSpot(b);
  b.pinned = true;
  refreshAll();
}

function deleteBox(b) {
  project.boxes = project.boxes.filter(o => o !== b);
  // синхронно уменьшить количество в строке позиции
  if (b.item && b.item._qtyInput) {
    const q = Math.max(0, (+b.item._qtyInput.value || 0) - 1);
    b.item._qtyInput.value = q;
  }
  refreshAll();
}

/* Отпускание после перетаскивания: осадка по вертикали + закрепление.
   Если осадка привела бы к пересечению — остаёмся на прежней высоте. */
function onBoxDrop(b) {
  const save = b.z;
  const tops = zoneMates(b).filter(o => overlapsXY(o, b))
    .map(o => o.z + o.h).filter(v => v <= b.z);
  const floor=b.zone.type==='truck'?floorHeight(b,getSections(project.trucks[b.zone.truck].vehicle).find(s=>s.index===b.zone.section)):0;
  b.z = Math.max(tops.length?Math.max(...tops):0,floor??0);
  if (boxCollides(b)) b.z = save;
  b.pinned = true;
  refreshAll();
}

function onBoxSelect(id) {
  const b = project.boxes.find(x => x.id === id);
  const bar = $('boxbar');
  if (!b) { bar.classList.remove('show'); return; }
  const nm = b.item.name || `${t('pos')} ${(b.item.ci ?? 0) + 1}`;
  $('boxbarLbl').textContent = `${nm} · ${b.l}×${b.w}×${b.h} · ${b.item.weight} ${t('kg')}${b.pinned ? ' · 📌' : ''}`;
  bar.classList.add('show');
}

/* ---------- Валидация: границы и пересечения ---------- */

function validate() {
  const list = project.boxes;
  for (const b of list) b.invalid = false;
  for (const b of list) {
    if (b.zone.type === 'truck') {
      const sec = getSections(project.trucks[b.zone.truck].vehicle)
        .find(s => s.index === b.zone.section);
      if(!sec||!insideLoadingBounds(b,sec)||hitsDeckObstacle(b,sec)||support({...b,weight:b.item.weight,item:b.item},boxesInSection(b.zone.truck,b.zone.section).filter(o=>o!==b).map(o=>({...o,weight:o.item.weight})),sec).ratio<SUPPORT_RATIO)b.invalid=true;
    }
  }
  for (let i = 0; i < list.length; i++) {
    for (let j = i + 1; j < list.length; j++) {
      const a = list[i], c = list[j];
      if (a.zone.type !== c.zone.type) continue;
      if (a.zone.type === 'wh' ? a.zone.side !== c.zone.side
        : a.zone.truck !== c.zone.truck || a.zone.section !== c.zone.section) continue;
      if (collides3(a, c)) {  // включая нарушение зазора CARGO_GAP
        a.invalid = c.invalid = true;
      }
    }
  }
  const any = list.some(b => b.invalid &&
    (b.zone.type === 'wh' || b.zone.truck === project.cur || b.zone.type === 'truck'));
  $('overlapBanner').classList.toggle('show', list.some(b => b.invalid));
}

/* ---------- Отрисовка и статистика ---------- */

function visibleBoxes() {
  return project.boxes.filter(b => b.zone.type === 'wh' ||
    (b.zone.type === 'truck' && b.zone.truck === project.cur));
}

let _noSpaceDismissed = false; // «Начать» нажата — сообщение не показываем

function refreshAll() {
  validate();
  const tr = curTruck();
  if (tr) _noSpaceDismissed = false; // отсек есть — при следующем удалении показать снова
  $('noSpace').classList.toggle('show', !tr && !_noSpaceDismissed);
  if (!tr) {
    $('truckLabel').textContent = `${t('space')} 0/0`;
    $('navPrev').disabled = $('navNext').disabled = true;
    sceneBuild(null, 0);
    sceneBoxes(visibleBoxes(), CARGO_COLORS, sceneSelectedId());
    $('stats').innerHTML = '';
    $('statsOverlay').innerHTML = '';
    sceneSetAxleLoads({});
    return;
  }
  $('truckLabel').textContent =
    `${t('space')} ${project.cur + 1}/${project.trucks.length}: ${truckName(tr)}`;
  $('navPrev').disabled = project.cur === 0;
  $('navNext').disabled = project.cur === project.trucks.length - 1;
  sceneBuild(tr.vehicle, project.cur);
  sceneBoxes(visibleBoxes(), CARGO_COLORS, sceneSelectedId());
  onBoxSelect(sceneSelectedId());
  showStats();
}

function boxesInSection(ti, si) {
  return project.boxes.filter(b => b.zone.type === 'truck' &&
    b.zone.truck === ti && b.zone.section === si);
}

function showStats() {
  const tr = curTruck();
  const sections = getSections(tr.vehicle);
  const multi = sections.length > 1;
  const row = (k, v, warn) =>
    `<tr${warn ? ' class="warn"' : ''}><td>${t(k)}</td><td>${v}</td></tr>`;

  // агрегат по текущей площади
  const agg = { count: 0, weight: 0, volume: 0, vehVolume: 0, ldm: 0 };
  const secStats = sections.map(sec => {
    const bs = boxesInSection(project.cur, sec.index)
      .map(b => ({ x: b.x, y: b.y, z: b.z, l: b.l, w: b.w, h: b.h, weight: b.item.weight,item:b.item,orientation:b.orientation,orientationSigns:b.orientationSigns }));
    const st=effectiveStats(tr,sec,computeStats(sec,bs));
    agg.count += st.count; agg.weight += st.weight;
    agg.volume += st.volume; agg.vehVolume += st.vehVolume; agg.ldm += st.ldm;
    return { sec, st };
  });
  const payload = tr.vehicle.maxPayload || sections.reduce((s, x) => s + x.maxPayload, 0);

  let html = '<table>' +
    row('placedN', `${agg.count} ${t('pcs')}`) +
    row('weight', `${agg.weight} ${t('kg')}${isFinite(payload) ? ` (${(100 * agg.weight / payload).toFixed(1)}%)` : ''}`) +
    row('volume', `${agg.volume.toFixed(2)} / ${agg.vehVolume.toFixed(2)} ${t('m3')}`) +
    row('ldm', agg.ldm.toFixed(2)) +
    row('freeW', isFinite(payload) ? `${payload - agg.weight} ${t('kg')}` : '—') +
    '</table>';

  const axleBySec = {}; // для подписей у колёс в 3D-сцене
  for (const { sec, st } of secStats) {
    const axle = axleLoads(sec, st);
    const gross=grossAxleLoads(sec,st);
    axleBySec[sec.index] = gross.available?gross:{available:false,errors:gross.errors,legacy:axle};
    if (multi) {
      const nm = sec.name ? (sec.name[LANG] || sec.name.ru) : `${t('section')} ${sec.index + 1}`;
      html += `<h3 class="secTitle">${nm}</h3>`;
    }
    html += '<table>';
    if (multi) html += row('weight', `${st.weight} ${t('kg')} (${st.weightUse}%)`);
    html += row('usedLen', `${st.usedLength} ${t('mm')}`) +
            row('freeLen', `${st.freeLength} ${t('mm')}`) +
            row('cg', `${st.cgX} / ${st.cgY} / ${st.cgZ} ${t('mm')} (X/Y/Z)`);
    html += '</table>';
    html+='<h3 class="secTitle">Полные расчётные нагрузки на оси</h3>';
    if(gross.available)html+='<table>'+gross.axles.map(a=>`<tr class="${a.over||a.unstable?'warn':''}"><td>${escapeHtml(a.label)}</td><td>${(a.kg/1000).toFixed(2)} т / ${a.limitKg?(a.limitKg/1000).toFixed(2)+' т':'лимит не задан'}</td></tr>`).join('')+'</table><p class="hint">'+escapeHtml(gross.method)+'. '+escapeHtml(gross.assumption)+'</p>';
    else html+='<p class="hint">'+gross.errors.map(escapeHtml).join(' · ')+'</p>';
    if (gross.available && gross.unstable) html += `<p class="warn">⚠ ${t('unstable')}</p>`;
  }

  // склады и закреплённые
  const whL = project.boxes.filter(b => b.zone.type === 'wh' && b.zone.side === 'L');
  const whR = project.boxes.filter(b => b.zone.type === 'wh' && b.zone.side === 'R');
  const pinnedN = project.boxes.filter(b => b.pinned).length;
  html += '<table>' +
    row('whLeft', `${whL.length} ${t('pcs')} · ${whL.reduce((s, b) => s + b.item.weight, 0)} ${t('kg')}`) +
    row('whRight', `${whR.length} ${t('pcs')} · ${whR.reduce((s, b) => s + b.item.weight, 0)} ${t('kg')}`) +
    '</table>';
  if (pinnedN) html += `<p class="hint">📌 ${pinnedN} ${t('pcs')} — ${t('movedManually')}</p>`;

  // группы (заказчики): итоги по всем зонам проекта
  const groups = new Map();
  for (const b of project.boxes) {
    const gname = b.item.group;
    if (!gname) continue;
    const e = groups.get(gname) || { n: 0, w: 0 };
    e.n++;
    e.w += b.item.weight;
    groups.set(gname, e);
  }
  if (groups.size) {
    html += `<h3 class="secTitle">${t('groupsTitle')}</h3><table>` +
      [...groups].map(([g, e]) =>
        `<tr><td>${escapeHtml(g)}</td><td>${e.n} ${t('pcs')} · ${e.w} ${t('kg')}</td></tr>`).join('') +
      '</table>';
  }

  $('stats').innerHTML = html;
  $('statsOverlay').innerHTML = html; // дубликат в правом углу сцены
  sceneSetAxleLoads(axleBySec);
  syncLoadingControls(tr,secStats);

}

/* ---------- Экспорт ---------- */

function exportPdf() {
  const tr = curTruck();
  if (!tr) { alert(t('pickSpaceFirst')); return; }
  const sections = getSections(tr.vehicle);
  const secResults = sections.map(sec => {
    const placed = boxesInSection(project.cur, sec.index).map((b, i) => ({
      x: b.x, y: b.y, z: b.z, l: b.l, w: b.w, h: b.h,
      weight: b.item.weight, item: b.item, itemIndex: b.item.ci ?? 0,orientation:b.orientation,orientationSigns:b.orientationSigns,unitNo: i + 1 }));
    return {section:sec,placed,stats:effectiveStats(tr,sec,computeStats(sec,placed))};
  });
  const agg = { count: 0, weight: 0, volume: 0, vehVolume: 0, ldm: 0, ldmMax: 0, freeWeight: 0 };
  for (const r of secResults) {
    agg.count += r.stats.count; agg.weight += r.stats.weight;
    agg.volume += r.stats.volume; agg.vehVolume += r.stats.vehVolume;
    agg.ldm += r.stats.ldm; agg.ldmMax += r.stats.ldmMax;
  }
  agg.volume = +agg.volume.toFixed(2);
  agg.vehVolume = +agg.vehVolume.toFixed(2);
  agg.ldm = +agg.ldm.toFixed(2);
  agg.ldmMax = +agg.ldmMax.toFixed(2);
  agg.volumeUse = agg.vehVolume ? +(100 * agg.volume / agg.vehVolume).toFixed(1) : 0;
  const payload = tr.vehicle.maxPayload || sections.reduce((s, x) => s + x.maxPayload, 0);
  agg.weightUse = isFinite(payload) ? +(100 * agg.weight / payload).toFixed(1) : 0;
  agg.freeWeight = isFinite(payload) ? payload - agg.weight : Infinity;
  // на складах — как «не поместилось» в текущую площадь
  const byCi = new Map();
  for (const b of project.boxes.filter(b => b.zone.type === 'wh')) {
    const e = byCi.get(b.item.ci) || { item: b.item, qty: 0, byWeight: 0, bySpace: 0 };
    e.qty++;
    byCi.set(b.item.ci, e);
  }
  openPackingList(tr.vehicle, { sections: secResults, unplaced: [...byCi.values()], stats: agg },
    CARGO_COLORS, sceneScreenshot());
}

function downloadScreenshot() {
  const a = document.createElement('a');
  a.href = sceneScreenshot();
  a.download = 'packontruck-scene.png';
  a.click();
}

function applyLang() {
  document.title = t('title');
  document.querySelectorAll('[data-i18n]').forEach(el => el.textContent = t(el.dataset.i18n));
  $('catSearch').placeholder = t('searchPh');
  buildVehicleSelect();
  const ps = $('presetSel');
  ps.innerHTML = `<option value="">${t('preset')}</option>` + CARGO_PRESETS.map(p =>
    `<option value="${p.id}">${p.name[LANG] || p.name.ru}</option>`).join('');
}

window.addEventListener('DOMContentLoaded', init);
