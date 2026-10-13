/* PackOnTruck — упаковочный лист.
   Открывает печатную форму (браузер → «Сохранить как PDF»): полная поддержка
   кириллицы без встраивания шрифтов. Включает 3D-скриншот, виды сверху/сбоку
   и таблицу размещения по каждой секции грузового объёма, итоги. */

function drawView(canvas, section, placed, colors, mode, opts = {}) {
  const ctx = canvas.getContext('2d');
  const isTop = mode === 'top';
  const vw = section.L, vh = isTop ? section.W : section.H;
  const ml = opts.cab ? 62 : 10, mr = opts.arrow ? 70 : 10; // поля под кабину и стрелку
  const scale = Math.min((canvas.width - ml - mr) / vw, (canvas.height - 20) / vh);
  const ox = ml, oy = 10;
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height);

  // схематичная кабина слева (перед грузовым отсеком)
  if (opts.cab) {
    const cx = ox - 50, cw = 40;
    let cy, ch;
    if (isTop) { ch = vh * scale * 0.8; cy = oy + (vh * scale - ch) / 2; }
    else { ch = Math.min(2600, vh) * scale; cy = oy + vh * scale - ch; }
    ctx.fillStyle = '#b0bec5';
    ctx.strokeStyle = '#333'; ctx.lineWidth = 1.2;
    ctx.fillRect(cx, cy, cw, ch);
    ctx.strokeRect(cx, cy, cw, ch);
    // лобовая часть
    ctx.fillStyle = '#78909c';
    ctx.fillRect(cx, cy, 8, ch);
  }

  ctx.strokeStyle = '#333'; ctx.lineWidth = 1.5;
  ctx.strokeRect(ox, oy, vw * scale, vh * scale);

  // стрелка стороны погрузки (с заднего борта, справа)
  if (opts.arrow) {
    const ax = ox + vw * scale, ay = oy + vh * scale / 2;
    ctx.strokeStyle = '#c0392b'; ctx.fillStyle = '#c0392b'; ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(ax + 56, ay); ctx.lineTo(ax + 14, ay);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(ax + 6, ay); ctx.lineTo(ax + 18, ay - 7); ctx.lineTo(ax + 18, ay + 7);
    ctx.closePath(); ctx.fill();
    ctx.font = 'bold 11px sans-serif';
    ctx.save();
    ctx.translate(ax + 34, ay - 8);
    ctx.textAlign = 'center';
    ctx.fillText(t('loading'), 0, 0);
    ctx.restore();
  }

  const boxes = [...placed].sort((a, b) => isTop ? a.z - b.z : a.y - b.y);
  for (const p of boxes) {
    const ci = p.item.ci != null ? p.item.ci : p.itemIndex;
    const x = ox + p.x * scale;
    const y = isTop ? oy + p.y * scale : oy + (section.H - p.z - p.h) * scale;
    const w = p.l * scale, h = (isTop ? p.w : p.h) * scale;
    ctx.fillStyle = colors[ci % colors.length];
    ctx.globalAlpha = 0.85;
    ctx.fillRect(x, y, w, h);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = '#222'; ctx.lineWidth = 0.7;
    ctx.strokeRect(x, y, w, h);
    if (w > 26 && h > 12) {
      ctx.fillStyle = '#111';
      ctx.font = '10px sans-serif';
      ctx.fillText(String(ci + 1), x + 3, y + 11);
    }
  }
}

function secName(section, i) {
  if (section.name) return section.name[LANG] || section.name.ru;
  return `${t('section')} ${i + 1}`;
}

function openPackingList(vehicle, result, colors, screenshotPng) {
  const { sections, unplaced, stats } = result;
  const multi = sections.length > 1;

  // блоки по секциям: виды + таблица размещения
  let secHtml = '';
  sections.forEach((sr, i) => {
    const s = sr.section;
    const top = document.createElement('canvas'); top.width = 1000; top.height = 220;
    const side = document.createElement('canvas'); side.width = 1000; side.height = 240;
    const cab = vehicle.group === 'truck' && i === 0; // кабина — только у первой секции автомашины
    drawView(top, s, sr.placed, colors, 'top', { cab, arrow: true });
    drawView(side, s, sr.placed, colors, 'side', { cab, arrow: true });

    const rows = sr.placed
      .slice().sort((a, b) => a.x - b.x || a.y - b.y || a.z - b.z)
      .map((p, n) => {
        const ci = p.item.ci != null ? p.item.ci : p.itemIndex;
        return `<tr>
      <td>${n + 1}</td>
      <td><span class="sw" style="background:${colors[ci % colors.length]}"></span>
          ${esc(p.item.name || t('pos') + ' ' + (ci + 1))} #${p.unitNo}</td>
      <td>${p.l}×${p.w}×${p.h}</td><td>${p.weight}</td>
      <td>${p.x}</td><td>${p.y}</td><td>${p.z}</td></tr>`;
      }).join('');

    const axle = typeof axleLoads === 'function' ? axleLoads(s, sr.stats) : null;
    let axleHtml = axle ? `
      <tr><td>${t('axleFront')}</td><td>${axle.front} / ${axle.frontLimit} ${t('kg')}
        ${axle.frontOver ? `<b class="warn">${t('over')}</b>` : ''}</td></tr>
      <tr><td>${t('axleRear')}</td><td>${axle.rear} / ${axle.rearLimit} ${t('kg')}
        ${axle.rearOver ? `<b class="warn">${t('over')}</b>` : ''}</td></tr>` : '';

    const gross=grossAxleLoads(s,sr.stats);
    axleHtml=gross.available?gross.axles.map((a,index)=>`<tr><td>${index+1}. ${esc(a.label)} · полная нагрузка</td><td>${Math.round(a.kg)} / ${a.limitKg??'лимит не задан'} кг ${a.over?'⚠ превышение':''}</td></tr>`).join(''):`<tr><td>Нагрузки осей</td><td>${gross.errors.map(esc).join(' · ')}</td></tr>`;
    secHtml += `
${multi ? `<h2 class="sec">${esc(secName(s, i))} — ${s.L}×${s.W}×${s.H} ${t('mm')}, ${s.maxPayload} ${t('kg')}</h2>` : ''}
<h2>${t('topView')}</h2><img src="${top.toDataURL()}">
<h2>${t('sideView')}</h2><img src="${side.toDataURL()}">
<table class="section-stats" style="margin:6px 0">
 <tr><td>${t('placedN')}</td><td>${sr.stats.count} ${t('pcs')}</td></tr>
 <tr><td>${t('weight')}</td><td>${sr.stats.weight} ${t('kg')} (${sr.stats.weightUse}%)</td></tr>
 <tr><td>${t('ldm')} / ${t('ldmMax')}</td><td>${sr.stats.ldm} / ${sr.stats.ldmMax}</td></tr>
 <tr><td>${t('usedLen')} / ${t('freeLen')}</td><td>${sr.stats.usedLength} / ${sr.stats.freeLength} ${t('mm')}</td></tr>
 <tr><td>Фактический центр массы X / Y / Z</td><td>${sr.stats.cgX} / ${sr.stats.cgY} / ${sr.stats.cgZ} ${t('mm')}</td></tr>
 ${axleHtml}
</table>
${axle && axle.unstable ? `<p class="warn">${t('unstable')}</p>` : ''}
<table><tr><th>${t('posN')}</th><th>${t('pos')}</th><th>${t('size')}</th>
<th>${t('weightKg')}</th><th>${t('coordsX')}</th><th>${t('coordsY')}</th><th>${t('coordsZ')}</th></tr>
${rows}</table>`;
  });

  const unpl = unplaced.length
    ? `<p class="warn">${t('unplaced')}: ${unplaced.map(u => {
        const why = [];
        if (u.byWeight) why.push(`${u.byWeight} ${t('pcs')} ${t('reasonWeight')}`);
        if (u.bySpace) why.push(`${u.bySpace} ${t('pcs')} ${t('reasonSpace')}`);
        const name = esc(u.item.name || t('pos') + ' ' + ((u.item.ci != null ? u.item.ci : 0) + 1));
        return `${name} — ${u.qty} ${t('pcs')}${why.length ? ' (' + why.join('; ') + ')' : ''}`;
      }).join('; ')}</p>`
    : '';

  const vname = vehicle.name ? (vehicle.name[LANG] || vehicle.name.ru) : `${vehicle.L}×${vehicle.W}×${vehicle.H}`;
  const outputHtml=`<!DOCTYPE html><html lang="${LANG}"><head><meta charset="utf-8">
<title>${t('packingList')}</title>
<style>
 body{font-family:'Segoe UI',Arial,sans-serif;margin:24px;color:#1b1f24;font-size:13px}
 h1{font-size:20px;margin:0 0 4px} h2{font-size:14px;margin:18px 0 6px}
 h2.sec{font-size:16px;border-top:2px solid #2c3e50;padding-top:10px;margin-top:26px}
 table{border-collapse:collapse;width:100%} td,th{border:1px solid #999;padding:3px 7px;text-align:left}
 .section-stats{break-inside:avoid} tr{break-inside:avoid} thead{display:table-header-group}
 th{background:#eef1f5} .meta td{border:none;padding:1px 14px 1px 0}
 .warn{color:#c0392b;font-weight:600} img,canvas{max-width:100%}
 .sw{display:inline-block;width:10px;height:10px;margin-right:5px;border:1px solid #444}
 @media print{button{display:none}}
 button{padding:8px 18px;font-size:14px;margin:14px 0;cursor:pointer}
</style></head><body>
<h1>${t('packingList')}</h1>
<table class="meta"><tr><td>${t('date')}: ${new Date().toLocaleDateString(LANG === 'ru' ? 'ru-RU' : 'en-GB')}</td>
<td>${t('vehicle')}: ${esc(vname)}</td></tr></table>
<button id="print-packing">${t('print')}</button>
${screenshotPng ? `<h2>${t('view3d')}</h2><img src="${screenshotPng}" style="max-height:330px">` : ''}
<h2>${t('totals')}</h2>
<table>
 <tr><td>${t('placedN')}</td><td>${stats.count} ${t('pcs')}</td></tr>
 <tr><td>${t('weight')}</td><td>${stats.weight} ${t('kg')} (${stats.weightUse}%)</td></tr>
 <tr><td>${t('volume')}</td><td>${stats.volume} ${t('m3')} / ${stats.vehVolume} ${t('m3')} (${stats.volumeUse}%)</td></tr>
 <tr><td>${t('ldm')} / ${t('ldmMax')}</td><td>${stats.ldm} / ${stats.ldmMax}</td></tr>
 <tr><td>${t('freeW')}</td><td>${isFinite(stats.freeWeight) ? stats.freeWeight + ' ' + t('kg') : '—'}</td></tr>
</table>
${unpl}
${secHtml}
<p>Расчётная схема погрузки. Нагрузки зависят от введённых параметров техники и выбранных долей групп.</p>
</body></html>`;
  const native=window.parent.NexDesktop;
  if(native?.exportPdf)return native.exportPdf(outputHtml,'Погрузка'+(typeof shipmentOrder!=='undefined'&&shipmentOrder?' №'+shipmentOrder.number:'')).catch(error=>alert(error.message));
  const w=window.open('','_blank');if(!w){alert('Разрешите открытие печатной формы');return}w.document.write(outputHtml);w.document.close();w.document.getElementById('print-packing').addEventListener('click',()=>w.print());
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
