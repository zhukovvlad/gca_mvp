/** Нетерминальные статусы документа: обработка ещё идёт, данные изменятся. */
export const NON_TERMINAL_STATUSES: ReadonlySet<string> = new Set(["pending", "processing"]);

const POLL_MS = 2500;

type DocLike = { status?: string };

/**
 * Колбэк для refetchInterval (react-query v5): 2500 мс, пока в данных квери
 * есть документ в нетерминальном статусе, иначе false — polling останавливается (S1-5).
 * Данные нормализуются: list-квери отдаёт массив, detail — одиночный объект.
 */
export function processingRefetchInterval(query: { state: { data?: unknown } }): number | false {
  const data = query.state.data;
  const docs: DocLike[] = Array.isArray(data) ? data : data ? [data as DocLike] : [];
  return docs.some((d) => NON_TERMINAL_STATUSES.has(d?.status ?? "")) ? POLL_MS : false;
}

/** Документ в обработке: мутации запрещены (совпадает с 409-контрактом бэка S1). */
export function isDocBusy(status: string | undefined): boolean {
  return NON_TERMINAL_STATUSES.has(status ?? "");
}
