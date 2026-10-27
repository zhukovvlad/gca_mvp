/** Целое положительное из адреса; всё остальное (включая пустую строку, `1e2`, `0x10`, ` 7`, `1.0`) — `null`. */
export function parseId(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const id = Number(raw);
  return id > 0 ? id : null;
}
