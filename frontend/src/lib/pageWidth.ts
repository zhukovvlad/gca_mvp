/**
 * Разделы с широкой страницей (`container-wide`, см. `index.css`): «Семьи и
 * контексты» и «Ручной матчинг». Маршруты заданы здесь один раз — шапка
 * приложения берёт по ним тот же контейнер, что и страница, чтобы края шапки и
 * содержимого совпадали.
 */
export const WIDE_PAGE_ROUTE = "/families";
export const REVIEW_PAGE_ROUTE = "/review";

const WIDE_PAGE_ROUTES = [WIDE_PAGE_ROUTE, REVIEW_PAGE_ROUTE];

/** Страница участника «Против нашей цены»: дерево статей и разделов с таблицей строк шире обычной страницы. */
const OFFER_PAGE_PATH = /^\/tenders\/\d+\/rounds\/\d+\/our-price\/offers\/\d+\/?$/;

/** Путь раздела с широкой страницей: сам маршрут и его подпути, а также страница участника «Против нашей цены». */
export function isWidePagePath(pathname: string): boolean {
  return (
    WIDE_PAGE_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`)) ||
    OFFER_PAGE_PATH.test(pathname)
  );
}
