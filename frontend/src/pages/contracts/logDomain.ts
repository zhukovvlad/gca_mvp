/** Границы оси — степени десяти вокруг точек: «каждое деление в 10 раз больше предыдущего». */
export function logDomain(values: number[]): { lo: number; hi: number; ticks: number[] } {
  const positive = values.filter((v) => Number.isFinite(v) && v > 0);
  const min = Math.min(...positive);
  const max = Math.max(...positive);
  const first = Math.floor(Math.log10(min));
  let last = Math.ceil(Math.log10(max));
  if (last === first) last = first + 1;
  const ticks: number[] = [];
  for (let power = first; power <= last; power += 1) ticks.push(10 ** power);
  return { lo: 10 ** first, hi: 10 ** last, ticks };
}
