/* PackOnTruck — импорт Excel/CSV (SheetJS) и хранение своих шаблонов.
   Формат импорта (первая строка — заголовки, порядок колонок фиксированный):
   Наименование | Длина | Ширина | Высота | Вес | Кол-во | Ярусы(1..N) | Поворот(1/0)
   Последние 3 колонки необязательны. Подробнее: docs/import-format.md */

function importFile(file, done, fail) {
  const reader = new FileReader();
  reader.onload = e => {
    try {
      const wb = XLSX.read(e.target.result, { type: 'array' });
      const rows = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1 });
      const items = [];
      for (let i = 1; i < rows.length; i++) {
        const r = rows[i];
        if (!r || r.length < 5 || !+r[1]) continue;
        items.push(makeCargoItem({
          name: String(r[0] ?? ''), L: r[1], W: r[2], H: r[3],
          weight: r[4], qty: r[5] ?? 1,
          tiers: r[6] === undefined || r[6] === '' ? 1 : Math.max(1, +r[6] || 1),
          rotatable: r[7] === undefined ? true : !!+r[7],
          group: r[8] === undefined ? '' : String(r[8]),
        }));
      }
      if (!items.length) throw new Error('no rows');
      done(items);
    } catch (err) { fail(err); }
  };
  reader.onerror = fail;
  reader.readAsArrayBuffer(file);
}

/* Свои шаблоны транспорта — localStorage */
const LS_KEY = 'packontruck_custom_vehicles';

function loadCustomVehicles() {
  try { return JSON.parse(localStorage.getItem(LS_KEY)) || []; } catch { return []; }
}

function saveCustomVehicle(v) {
  const list = loadCustomVehicles();
  v.id = 'custom_' + Date.now();
  v.group = 'custom';
  list.push(v);
  localStorage.setItem(LS_KEY, JSON.stringify(list));
  return v;
}
