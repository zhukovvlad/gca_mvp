/**
 * Разделы с широкой страницей (`container-wide`, см. `index.css`): «Семьи и
 * контексты» и «Ручной матчинг». Маршруты заданы здесь один раз — шапка
 * приложения берёт по ним тот же контейнер, что и страница, чтобы края шапки и
 * содержимого совпадали.
 */
export const WIDE_PAGE_ROUTE = "/families";
export const REVIEW_PAGE_ROUTE = "/review";

const WIDE_PAGE_ROUTES = [WIDE_PAGE_ROUTE, REVIEW_PAGE_ROUTE];

/** Путь раздела с широкой страницей: сам маршрут и его подпути. */
export function isWidePagePath(pathname: string): boolean {
  return WIDE_PAGE_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`));
}
