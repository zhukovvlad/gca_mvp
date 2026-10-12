/**
 * Раздел с широкой страницей (`container-wide`, см. `index.css`): «Семьи и
 * контексты». Маршрут задан здесь один раз — шапка приложения берёт по нему тот
 * же контейнер, что и страница, чтобы края шапки и содержимого совпадали.
 */
export const WIDE_PAGE_ROUTE = "/families";

/** Путь раздела с широкой страницей: сам маршрут и его подпути. */
export function isWidePagePath(pathname: string): boolean {
  return pathname === WIDE_PAGE_ROUTE || pathname.startsWith(`${WIDE_PAGE_ROUTE}/`);
}
