/* PackOnTruck — осевые нагрузки (упрощённая модель рычага).
   Две опоры грузовой площади: front (седло/передняя ось) и rear (ось/тележка),
   координаты — мм от переднего борта грузового пола (front может быть < 0). */

function axleLoads(vehicle, stats) {
  if (!vehicle.axles || !stats.weight) return null;
  const { front, rear, frontLimit, rearLimit } = vehicle.axles;
  const span = rear - front;
  if (span <= 0) return null;
  const rearLoad = stats.weight * (stats.cgX - front) / span;
  const frontLoad = stats.weight - rearLoad;
  return {
    front: Math.round(frontLoad),
    rear: Math.round(rearLoad),
    frontLimit, rearLimit,
    frontOver: frontLimit ? frontLoad > frontLimit : false,
    rearOver: rearLimit ? rearLoad > rearLimit : false,
    // отрицательная нагрузка = груз целиком за опорой, риск опрокидывания
    unstable: frontLoad < 0 || rearLoad < 0,
  };
}

if (typeof module !== 'undefined') module.exports = { axleLoads };
