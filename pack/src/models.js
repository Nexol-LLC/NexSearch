/* PackOnTruck — модели данных и встроенные шаблоны.
   Все размеры в мм, вес в кг. Ось X — вдоль длины (0 = передний борт),
   Y — по ширине, Z — высота. */

// Встроенные шаблоны грузовых площадей
const VEHICLE_TEMPLATES = [
  {id:'lowbed',group:'truck',body:'flatbed',name:{ru:'Трал — открытая площадка',en:'Lowbed — open platform'},L:13600,W:2500,H:4000,maxPayload:36000,axles:{front:1600,rear:9800,frontLimit:14000,rearLimit:30000}},
  // Фуры / тенты
  { id: 'gazelle',  group: 'truck', name: { ru: 'Газель (9 м³)', en: 'Gazelle (9 m³)' },
    L: 3000, W: 1900, H: 1600, maxPayload: 1500,
    axles: { front: -500, rear: 2200, frontLimit: 1800, rearLimit: 2500 } },
  { id: 'truck5t',  group: 'truck', name: { ru: 'Грузовик 5т (36 м³)', en: 'Truck 5t (36 m³)' },
    L: 6100, W: 2450, H: 2400, maxPayload: 5000,
    axles: { front: -1200, rear: 4200, frontLimit: 4000, rearLimit: 8000 } },
  { id: 'tent82',   group: 'truck', name: { ru: 'Фура тент 82 м³', en: 'Tilt trailer 82 m³' },
    L: 13600, W: 2450, H: 2450, maxPayload: 22000,
    axles: { front: 1600, rear: 9800, frontLimit: 12000, rearLimit: 24000 } },
  { id: 'tent92',   group: 'truck', name: { ru: 'Фура тент 92 м³', en: 'Tilt trailer 92 m³' },
    L: 13600, W: 2450, H: 2750, maxPayload: 22000,
    axles: { front: 1600, rear: 9800, frontLimit: 12000, rearLimit: 24000 } },
  { id: 'tent120',  group: 'truck', name: { ru: 'Автосцепка 120 м³ (2 секции)', en: 'Road train 120 m³ (2 sections)' },
    maxPayload: 22000,
    sections: [
      { name: { ru: 'Секция 1 — автомобиль', en: 'Section 1 — truck' },
        L: 7700, W: 2450, H: 3000, maxPayload: 11000,
        axles: { front: -1500, rear: 5500, frontLimit: 7500, rearLimit: 11500 } },
      { name: { ru: 'Секция 2 — прицеп', en: 'Section 2 — trailer' },
        L: 7700, W: 2450, H: 3000, maxPayload: 11000,
        axles: { front: 1300, rear: 6200, frontLimit: 9000, rearLimit: 9000 } },
    ] },
  // Морские контейнеры (внутренние размеры)
  { id: 'c20dc', group: 'container', name: { ru: 'Контейнер 20DC', en: 'Container 20DC' },
    L: 5898, W: 2352, H: 2393, maxPayload: 28200, axles: null },
  { id: 'c40dc', group: 'container', name: { ru: 'Контейнер 40DC', en: 'Container 40DC' },
    L: 12032, W: 2352, H: 2393, maxPayload: 26680, axles: null },
  { id: 'c40hc', group: 'container', name: { ru: 'Контейнер 40HC', en: 'Container 40HC' },
    L: 12032, W: 2352, H: 2691, maxPayload: 26460, axles: null },
  { id: 'c45hc', group: 'container', name: { ru: 'Контейнер 45HC', en: 'Container 45HC' },
    L: 13556, W: 2352, H: 2691, maxPayload: 27600, axles: null },
];

// Шаблоны типовых грузовых мест
const CARGO_PRESETS = [
  { id: 'eur',  name: { ru: 'Паллета EUR 1200×800',  en: 'EUR pallet 1200×800'  }, L: 1200, W: 800,  H: 1500, weight: 500 },
  { id: 'fin',  name: { ru: 'Паллета FIN 1200×1000', en: 'FIN pallet 1200×1000' }, L: 1200, W: 1000, H: 1500, weight: 600 },
  { id: 'box',  name: { ru: 'Коробка 600×400×400',   en: 'Box 600×400×400'      }, L: 600,  W: 400,  H: 400,  weight: 20 },
];

const CARGO_COLORS = ['#4e79a7','#f28e2b','#e15759','#76b7b2','#59a14f',
                      '#edc948','#b07aa1','#ff9da7','#9c755f','#bab0ac'];

/* Постоянный технологический зазор между грузовыми местами, мм.
   Действует по горизонтали (X/Y) во всей математике: автоукладка,
   коллизии, примагничивание, валидация. По вертикали (штабель) зазора
   нет — места стоят друг на друге вплотную. К стенкам зазор не требуется. */
const CARGO_GAP = 10;

/* Грузовое место (одна позиция = qty одинаковых мест).
   tiers — ярусность: 1 = не штабелируется, 2 = в 2 яруса, 3 = в 3 и т.д. */
function makeCargoItem(o) {
  let tiers = Math.floor(+o.tiers);
  if (!tiers || tiers < 1) {
    // обратная совместимость: старый флаг stackable=true — «без ограничения ярусов»
    tiers = (o.stackable === true || o.stackable === 1 || o.stackable === '1') ? 99 : 1;
  }
  return {
    name: o.name || '',cg:Array.isArray(o.cg)?o.cg:[.5,.5,.5],stop:Math.max(1,+o.stop||1),verticalRotation:!!(o.verticalRotation||o.vertical_rotation),
    group: String(o.group || '').trim(), // группа/заказчик — держится вместе при укладке
    L: +o.L || 0, W: +o.W || 0, H: +o.H || 0,
    weight: +o.weight || 0,
    qty: Math.max(0, Math.floor(o.qty == null ? 1 : +o.qty)),
    tiers,
    stackable: tiers > 1,           // производное, для внутренних проверок
    maxLoad: o.maxLoad === '' || o.maxLoad == null ? Infinity : +o.maxLoad,
    rotatable: o.rotatable !== false && o.rotatable !== 'false' && o.rotatable !== 0,
    color: o.color || null,
  };
}

function vehicleById(id) { return VEHICLE_TEMPLATES.find(v => v.id === id) || null; }

/* Грузовые объёмы транспорта: у односекционных — сам транспорт,
   у мультисекционных (автосцепка) — массив sections. */
function getSections(v) {
  if (v.sections) return v.sections.map((s, i) => ({ ...s, index: i }));
  return [{ id:v.id, trailer:v.trailer||null, name: v.name || null, L: v.L, W: v.W, H: v.H,
            maxPayload: v.maxPayload, axles: v.axles || null,oversize:v.oversize||null,floorSegments:v.floorSegments||null,blockedRegions:v.blockedRegions||[],runningGear:v.runningGear||null,packingStrategy:v.packingStrategy||'capacity',targetCg:v.targetCg||null,index:0 }];
}

if (typeof module !== 'undefined') {
  module.exports = { VEHICLE_TEMPLATES, CARGO_PRESETS, CARGO_COLORS, CARGO_GAP, makeCargoItem, vehicleById, getSections };
}
