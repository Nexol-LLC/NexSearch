/* PackOnTruck — алгоритм 3D-упаковки.

   Критерии оценки размещения (поэтапно, в порядке приоритета):
   1. Вместительность — максимум размещённых мест.
   2. Нагрузка на ось — минимальный штраф за превышение лимитов опор
      и неустойчивое распределение.
   3. Минимум свободного пространства вдоль бортов и максимум
      к воротам загрузки (задний борт).
   4. Продолговатые места — длинной стороной поперёк оси движения,
      если это не в ущерб пунктам 1–3.

   Однотипный груз (одна позиция) — плановый расчёт паллетных схем:
   перебор комбинаций рядов «поперёк»/«вдоль» и колонных схем,
   выбор по критериям 1–4. Пример: 33 EUR в тенте 13.6 м = 15×2 + 1×3.
   Смешанный груз — эвристика Extreme Points: тяжёлые вниз и вперёд,
   границы, коллизии, штабелируемость, лимит нагрузки, опора ≥ 70%. */

const SUPPORT_RATIO = 0.7; // минимальная доля опоры основания
// технологический зазор между местами (мм, по горизонтали) — см. models.js
const _GAP = typeof CARGO_GAP !== 'undefined' ? CARGO_GAP
  : (typeof require === 'function' ? require('./models.js').CARGO_GAP : 10);

/* ---------- Плановая упаковка однотипных мест (паллетные схемы) ---------- */

function packUniform(vehicle, it) {
  // возврат включает причины неразмещения: byWeight (грузоподъёмность) / bySpace (габариты)
  if (it.H > vehicle.H) return { placed: [], unplacedQty: it.qty, byWeight: 0, bySpace: it.qty };
  const long = Math.max(it.L, it.W), short = Math.min(it.L, it.W);
  const rot = it.rotatable && it.L !== it.W;

  // ярусы штабелирования: по высоте, по ярусности места (it.tiers)
  // и по лимиту нагрузки на нижнее место
  let tiers = Math.max(1, Math.floor(vehicle.H / it.H));
  tiers = Math.min(tiers, it.tiers || (it.stackable ? tiers : 1));
  if (it.stackable && isFinite(it.maxLoad) && it.weight > 0)
    tiers = Math.min(tiers, 1 + Math.floor(it.maxLoad / it.weight));

  const weightCap = it.weight > 0 && isFinite(vehicle.maxPayload)
    ? Math.floor(vehicle.maxPayload / it.weight) : Infinity;
  const target = Math.min(it.qty, weightCap);
  if (target <= 0) return { placed: [], unplacedQty: it.qty, byWeight: it.qty, bySpace: 0 };

  // ориентации (d — по ходу движения/оси X, a — поперёк):
  // P = поперёк (длинная сторона поперёк кузова), V = вдоль
  const oriP = (rot || it.L === short) ? { d: short, a: long } : null;
  const oriV = (rot || it.L === long) ? { d: long, a: short } : null;

  // ёмкости с учётом зазора _GAP между местами (алгебра «раздутых» размеров:
  // n мест занимают n*размер + (n-1)*зазор, т.е. n*(размер+зазор) ≤ габарит+зазор)
  const L = vehicle.L, W = vehicle.W;
  const nP = oriP && oriP.a <= W ? Math.floor((W + _GAP) / (oriP.a + _GAP)) : 0; // в ряду поперёк
  const nV = oriV && oriV.a <= W ? Math.floor((W + _GAP) / (oriV.a + _GAP)) : 0; // в ряду вдоль

  const plans = [];
  // Планы «рядами» вдоль длины: a рядов поперёк + b рядов вдоль
  // (перебираются все комбинации — альтернативы нужны критерию осевых нагрузок)
  const maxA = nP ? Math.floor((L + _GAP) / (oriP.d + _GAP)) : 0;
  for (let a = 0; a <= maxA; a++) {
    const remLG = L + _GAP - a * (oriP ? oriP.d + _GAP : 0);
    const maxB = nV && oriV ? Math.floor(remLG / (oriV.d + _GAP)) : 0;
    for (let b = 0; b <= maxB; b++) {
      if (a + b > 0) plans.push({ kind: 'row', a, b });
    }
  }
  // Планы «колоннами» по ширине: k2 колонн поперёк + k1 колонн вдоль
  const perColP = oriP ? Math.floor((L + _GAP) / (oriP.d + _GAP)) : 0; // в колонне поперёк
  const perColV = oriV ? Math.floor((L + _GAP) / (oriV.d + _GAP)) : 0;
  const maxK2 = oriP ? Math.floor((W + _GAP) / (oriP.a + _GAP)) : 0;
  for (let k2 = 0; k2 <= maxK2; k2++) {
    const remWG = W + _GAP - k2 * (oriP ? oriP.a + _GAP : 0);
    const maxK1 = oriV ? Math.floor(remWG / (oriV.a + _GAP)) : 0;
    for (let k1 = 0; k1 <= Math.max(0, maxK1); k1++) {
      if (k2 + k1 > 0) plans.push({ kind: 'col', k2, k1 });
    }
  }

  const _axleLoads = typeof axleLoads !== 'undefined' ? axleLoads
    : (typeof require === 'function' ? require('./axles.js').axleLoads : null);

  let best = null;
  for (const plan of plans) {
    const placed = materializePlan(vehicle, it, plan, { oriP, oriV, nP, nV, perColP, perColV, tiers, target });
    if (!placed.length) continue;

    // 1. вместительность
    const n = placed.length;
    // 2. штраф по осевым нагрузкам: превышение лимитов + неустойчивость
    let axlePen = 0;
    if (_axleLoads && vehicle.axles && it.weight > 0) {
      const al = _axleLoads(vehicle, computeStats(vehicle, placed));
      if (al) {
        axlePen = Math.max(0, al.front - (al.frontLimit || Infinity)) +
                  Math.max(0, al.rear - (al.rearLimit || Infinity));
        if (al.unstable) axlePen += 1e9;
      }
    }
    // 3. максимум свободного к воротам (мин. занятая длина)
    //    и минимум свободного вдоль бортов (макс. занятая ширина)
    let usedLength = 0, usedWidth = 0;
    for (const p of placed) {
      usedLength = Math.max(usedLength, p.x + p.l);
      usedWidth = Math.max(usedWidth, p.y + p.w);
    }
    const sideFree = W - usedWidth;
    // 4. продолговатые места поперёк оси движения
    const cross = placed.filter(p => p.w > p.l).length;

    const cand = { placed, n, axlePen, usedLength, sideFree, cross };
    if (!best || betterPlan(cand, best)) best = cand;
  }
  if (!best) return { placed: [], unplacedQty: it.qty, byWeight: it.qty - target, bySpace: target };
  return { placed: best.placed, unplacedQty: it.qty - best.n,
           byWeight: it.qty - target, bySpace: target - best.n };
}

/* Сравнение планов по критериям 1–4 (см. шапку файла).
   Критерий 4 применяется «не в ущерб 1–3»: различия по критерию 3
   в пределах допуска PLAN_EPS не считаются ухудшением (иначе зазоры
   по 10 мм арифметически перевешивали бы ориентацию поперёк). */
const PLAN_EPS = 100; // мм

function betterPlan(a, b) {
  if (a.n !== b.n) return a.n > b.n;                        // 1. вместительность
  if (a.axlePen !== b.axlePen) return a.axlePen < b.axlePen; // 2. оси
  if (Math.abs(a.usedLength - b.usedLength) > PLAN_EPS)      // 3. к воротам
    return a.usedLength < b.usedLength;
  if (Math.abs(a.sideFree - b.sideFree) > PLAN_EPS)          // 3. вдоль бортов
    return a.sideFree < b.sideFree;
  if (a.cross !== b.cross) return a.cross > b.cross;         // 4. поперёк
  if (a.usedLength !== b.usedLength) return a.usedLength < b.usedLength; // точные разделители
  return a.sideFree < b.sideFree;
}

function materializePlan(vehicle, it, plan, ctx) {
  const { oriP, oriV, nP, nV, perColP, perColV, tiers, target } = ctx;
  const placed = [];
  let rem = target, unit = 1;
  const put = (x, y, z, l, w) => {
    placed.push({ x, y, z, l, w, h: it.H, weight: it.weight, item: it,
                  itemIndex: 0, unitNo: unit++, loadOnTop: 0, below: null });
    rem--;
  };

  if (plan.kind === 'row') {
    // ряды поперёк — в начале кузова, ряды вдоль — хвост
    let x = 0;
    const bands = [];
    for (let i = 0; i < plan.a; i++) bands.push('P');
    for (let i = 0; i < plan.b; i++) bands.push('V');
    for (const band of bands) {
      if (rem <= 0) break;
      const o = band === 'P' ? oriP : oriV;
      const per = band === 'P' ? nP : nV;
      if (!o || !per) continue;
      let used = false;
      for (let i = 0; i < per && rem > 0; i++)
        for (let t = 0; t < tiers && rem > 0; t++) { put(x, i * (o.a + _GAP), t * it.H, o.d, o.a); used = true; }
      if (used) x += o.d + _GAP;
    }
  } else {
    // колонны: поперёк — у борта, вдоль — рядом
    let y = 0;
    const cols = [];
    for (let i = 0; i < plan.k2; i++) cols.push('P');
    for (let i = 0; i < plan.k1; i++) cols.push('V');
    for (const col of cols) {
      if (rem <= 0) break;
      const o = col === 'P' ? oriP : oriV;
      const per = col === 'P' ? perColP : perColV;
      if (!o || !per) continue;
      let used = false;
      for (let i = 0; i < per && rem > 0; i++)
        for (let t = 0; t < tiers && rem > 0; t++) { put(i * (o.d + _GAP), y, t * it.H, o.d, o.a); used = true; }
      if (used) y += o.a + _GAP;
    }
  }
  return placed;
}

/* ---------- Эвристика Extreme Points для смешанного груза ---------- */

/* Разворачивает позиции в отдельные единицы и сортирует:
   группа (заказчик) — кластером в порядке появления, затем
   вес ↓, площадь основания ↓, высота ↓.
   Позиции без группы идут после групп (gi = null → в конец). */
function expandUnits(items) {
  const units = [];
  items.forEach((it, idx) => {
    for (let i = 0; i < it.qty; i++) {
      units.push({ item: it, itemIndex: idx, unitNo: i + 1 });
    }
  });
  const gi = it => (it.gi == null ? 1e9 : it.gi);
  units.sort((a, b) =>
    gi(a.item) - gi(b.item) ||
    b.item.weight - a.item.weight ||
    b.item.L * b.item.W - a.item.L * a.item.W ||
    b.item.H - a.item.H);
  return units;
}

/* Коллизия с учётом зазора _GAP по горизонтали:
   места ближе _GAP друг к другу (на пересекающихся высотах) — конфликт.
   По вертикали зазора нет — штабель стоит вплотную. */
function collides(box, placed) {
  for (const p of placed) {
    if (box.x < p.x + p.l + _GAP && box.x + box.l + _GAP > p.x &&
        box.y < p.y + p.w + _GAP && box.y + box.w + _GAP > p.y &&
        box.z < p.z + p.h && box.z + box.h > p.z) return true;
  }
  return false;
}

/* Опора: доля основания, лежащая на полу или на верхних гранях коробок. */
function support(box, placed, vehicle) {
  const floor=typeof floorSupport==='function'?floorSupport(box,vehicle):box.z===0?1:0;
  if(floor>=1)return {ratio:floor,supporters:[]};
  const base = box.l * box.w;
  let area = floor*base; const supporters = [];
  for (const p of placed) {
    if (p.z + p.h !== box.z) continue;
    const ox = Math.max(0, Math.min(box.x + box.l, p.x + p.l) - Math.max(box.x, p.x));
    const oy = Math.max(0, Math.min(box.y + box.w, p.y + p.w) - Math.max(box.y, p.y));
    if (ox > 0 && oy > 0) { area += ox * oy; supporters.push(p); }
  }
  return { ratio: Math.min(1,area / base), supporters };
}

/* Проверка ярусности и лимита нагрузки: новый ярус = уровень опоры + 1;
   для каждого места в цепочке вниз высота стопки над ним не должна
   превышать его ярусность (item.tiers). */
function loadOk(box, supporters) {
  for (const s of supporters) {
    const newLevel = (s.level || 1) + 1;
    let node = s;
    while (node) {
      const allowed = node.item.tiers || (node.item.stackable ? 99 : 1);
      if (newLevel - (node.level || 1) + 1 > allowed) return false;
      if (node.loadOnTop + box.weight > node.item.maxLoad) return false;
      node = node.below;
    }
  }
  return true;
}

function applyLoad(box, supporters) {
  let main = null, best = -1;
  for (const s of supporters) {
    const ox = Math.min(box.x + box.l, s.x + s.l) - Math.max(box.x, s.x);
    const oy = Math.min(box.y + box.w, s.y + s.w) - Math.max(box.y, s.y);
    if (ox * oy > best) { best = ox * oy; main = s; }
    let node = s;
    while (node) { node.loadOnTop += box.weight; node = node.below; }
  }
  box.below = main;
  box.level = main ? (main.level || 1) + 1 : 1;
}

/* Основная функция (один грузовой объём).
   vehicle: {L,W,H,maxPayload}, items: [cargoItem],
   fixed: уже размещённые (закреплённые пользователем) места — препятствия
   → { placed: [box] (только новые), unplaced: [...], stats (по всем) } */
function pack(vehicle, items, fixed = []) {
  // однотипный груз без препятствий — плановые паллетные схемы
  if (items.length === 1 && fixed.length === 0 && !vehicle.floorSegments && !vehicle.blockedRegions?.length && vehicle.packingStrategy!=='unloading' && !items[0].verticalRotation) {
    const u = packUniform(vehicle, items[0]);
    const unplaced = u.unplacedQty > 0
      ? [{ item: items[0], itemIndex: 0, qty: u.unplacedQty,
           byWeight: u.byWeight, bySpace: u.bySpace }] : [];
    return { placed: u.placed, unplaced, stats: computeStats(vehicle, u.placed) };
  }

  const units = expandUnits(items);
  if(vehicle.packingStrategy==='unloading')units.sort((a,b)=>(b.item.stop||1)-(a.item.stop||1)||b.item.weight-a.item.weight);
  const placed = [...fixed];        // препятствия участвуют в коллизиях и опоре
  const newPlaced = [];
  const unplacedCount = new Map();
  let anchors = (typeof floorSegments==='function'?floorSegments(vehicle):[{x:0,y:0,z:0}]).map(f=>({x:f.x,y:f.y,z:f.z||0}));
  for (const f of fixed) {
    if (f.level == null) f.level = 1;
    if (f.loadOnTop == null) f.loadOnTop = 0;
    anchors.push({ x: f.x + f.l + _GAP, y: f.y, z: f.z },
                 { x: f.x, y: f.y + f.w + _GAP, z: f.z },
                 { x: f.x, y: f.y, z: f.z + f.h });
  }
  let totalWeight = fixed.reduce((s, f) => s + (f.weight || 0), 0);

  // счётчики неразмещённых с причиной: w — по весу, s — по месту
  const bump = (idx, key) => {
    const e = unplacedCount.get(idx) || { w: 0, s: 0 };
    e[key]++;
    unplacedCount.set(idx, e);
  };

  for (const u of units) {
    const it = u.item;
    if (totalWeight + it.weight > vehicle.maxPayload) {
      bump(u.itemIndex, 'w');
      continue;
    }
    // приоритет ориентации: длинная сторона поперёк кузова;
    // продольная ориентация — запасной вариант
    const long = Math.max(it.L, it.W), short = Math.min(it.L, it.W);
    const axes=it.verticalRotation?[[0,1,2],[1,0,2],[2,1,0],[0,2,1],[1,2,0],[2,0,1]]:it.rotatable&&it.L!==it.W?(it.L<it.W?[[0,1,2],[1,0,2]]:[[1,0,2],[0,1,2]]):[[0,1,2]];
    const dims=[it.L,it.W,it.H];
    const rotations=axes.map(a=>[dims[a[0]],dims[a[1]],dims[a[2]],a,a[0]===1&&a[1]===0?[1,-1,1]:a[0]===2&&a[2]===0?[1,1,-1]:a[1]===2&&a[2]===1?[-1,1,1]:[1,1,1]]);

    // якоря: ближе к переднему борту — раньше, затем ниже;
    // сначала заполняется «стена» у переднего борта (в т.ч. вверх), и только
    // потом укладка двигается назад — так сзади остаётся максимум свободной площади
    anchors.sort((a, b) => a.x - b.x || a.z - b.z || a.y - b.y);

    let done = null;
    for (const a of anchors) {
      for (const [l, w,h,orientation,orientationSigns] of rotations) {
        const box = { x: a.x, y: a.y, z: a.z, l, w, h,orientation,orientationSigns,
                      weight: it.weight, item: it, itemIndex: u.itemIndex,
                      unitNo: u.unitNo, loadOnTop: 0, below: null };
        if (box.x<0||box.y<0||box.x + l > vehicle.L || box.y + w > vehicle.W || box.z + h > vehicle.H) continue;
        if(typeof hitsDeckObstacle==='function'&&hitsDeckObstacle(box,vehicle))continue;
        if (collides(box, placed)) continue;
        const sup = support(box, placed,vehicle);
        if (sup.ratio < SUPPORT_RATIO) continue;
        if (!loadOk(box, sup.supporters)) continue;
        applyLoad(box, sup.supporters);
        done = box;
        break;
      }
      if (done) break;
    }

    if (done) {
      placed.push(done);
      newPlaced.push(done);
      totalWeight += it.weight;
      anchors = anchors.filter(a => !(a.x === done.x && a.y === done.y && a.z === done.z));
      anchors.push({ x: done.x + done.l + _GAP, y: done.y, z: done.z },
                   { x: done.x, y: done.y + done.w + _GAP, z: done.z },
                   { x: done.x, y: done.y, z: done.z + done.h });
      if(typeof floorSegments==='function')for(const level of new Set(floorSegments(vehicle).map(f=>f.z||0))){
        anchors.push({x:done.x+done.l+_GAP,y:done.y,z:level},{x:done.x,y:done.y+done.w+_GAP,z:level});
      }
    } else {
      bump(u.itemIndex, 's');
    }
  }

  const unplaced = [...unplacedCount].map(([i, e]) => ({
    item: items[i], itemIndex: i, qty: e.w + e.s, byWeight: e.w, bySpace: e.s }));
  return { placed: newPlaced, unplaced, stats: computeStats(vehicle, placed) };
}

function computeStats(vehicle, placed) {
  let weight = 0, volume = 0, usedLength = 0, ldm = 0, cgx = 0,cgy=0,cgz=0;
  for (const p of placed) {
    weight += p.weight;
    volume += p.l * p.w * p.h / 1e9;
    usedLength = Math.max(usedLength, p.x + p.l);
    if (p.z === 0) ldm += (p.l / 1000) * (p.w / 1000) / 2.4;
    const point=typeof boxMassCenter==='function'?boxMassCenter(p):[p.x+p.l/2,p.y+p.w/2,p.z+p.h/2];cgx += p.weight * point[0];
    cgy+=p.weight*point[1];cgz+=p.weight*point[2];
  }
  const vehVolume = vehicle.L * vehicle.W * vehicle.H / 1e9;
  return {
    count: placed.length,
    weight,
    volume: +volume.toFixed(2),
    vehVolume: +vehVolume.toFixed(2),
    volumeUse: vehVolume ? +(100 * volume / vehVolume).toFixed(1) : 0,
    weightUse: vehicle.maxPayload && isFinite(vehicle.maxPayload)
      ? +(100 * weight / vehicle.maxPayload).toFixed(1) : 0,
    usedLength,
    freeLength: vehicle.L - usedLength,
    freeWeight: isFinite(vehicle.maxPayload) ? vehicle.maxPayload - weight : Infinity,
    ldm: +ldm.toFixed(2),
    ldmMax: +(usedLength / 1000).toFixed(2),
    cgX: weight ? Math.round(cgx / weight) : 0,
    cgY:weight?Math.round(cgy/weight):vehicle.W/2,cgZ:weight?Math.round(cgz/weight):0,
  };
}

/* Упаковка по секциям транспорта: остаток из секции N идёт в секцию N+1. */
function packVehicle(vehicle, items) {
  const _getSections = typeof getSections !== 'undefined' ? getSections : require('./models.js').getSections;
  const sections = _getSections(vehicle);
  let rem = items;
  const secResults = [];
  let lastUnplaced = [];
  for (const s of sections) {
    const r = pack(s, rem);
    secResults.push({ section: s, placed: r.placed, stats: r.stats });
    rem = r.unplaced.map(u => ({ ...u.item, qty: u.qty }));
    lastUnplaced = r.unplaced;
  }
  // причины — по последней секции (куда груз пытался попасть в последнюю очередь)
  const unplaced = lastUnplaced.map(u => ({
    item: u.item, qty: u.qty, byWeight: u.byWeight || 0, bySpace: u.bySpace || 0 }));

  const agg = { count: 0, weight: 0, volume: 0, vehVolume: 0,
                usedLength: 0, freeLength: 0, ldm: 0, ldmMax: 0 };
  for (const r of secResults) {
    agg.count += r.stats.count;
    agg.weight += r.stats.weight;
    agg.volume += r.stats.volume;
    agg.vehVolume += r.stats.vehVolume;
    agg.usedLength += r.stats.usedLength;
    agg.freeLength += r.stats.freeLength;
    agg.ldm += r.stats.ldm;
    agg.ldmMax += r.stats.ldmMax;
  }
  agg.volume = +agg.volume.toFixed(2);
  agg.vehVolume = +agg.vehVolume.toFixed(2);
  agg.ldm = +agg.ldm.toFixed(2);
  agg.ldmMax = +agg.ldmMax.toFixed(2);
  agg.volumeUse = agg.vehVolume ? +(100 * agg.volume / agg.vehVolume).toFixed(1) : 0;
  const payload = vehicle.maxPayload || sections.reduce((s, x) => s + x.maxPayload, 0);
  agg.weightUse = payload && isFinite(payload) ? +(100 * agg.weight / payload).toFixed(1) : 0;
  agg.freeWeight = isFinite(payload) ? payload - agg.weight : Infinity;

  return { sections: secResults, unplaced, stats: agg };
}

if (typeof module !== 'undefined') module.exports = { pack, computeStats, packVehicle, packUniform };
