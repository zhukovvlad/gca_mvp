/**
 * Допустимые размеры страницы (спека `2026-09-25-families-screen-design.md`
 * §2.7: «На странице: 10 / 20 / 50 / 100»). Единственный источник этого
 * списка — `Pager` (умолчание `pageSizeOptions`) и
 * `usePersistedPageSize` (`pages/families/usePersistedPageSize.ts`, множество
 * допустимых сохранённых значений) импортируют отсюда, а не держат свою копию:
 * до этой правки список был продублирован в обоих файлах (находка ревью
 * Task 6, PF2).
 *
 * Отдельный модуль, а не экспорт из `Pager.tsx`: тот экспортирует компонент,
 * и вторая экспортируемая константа рядом с ним ломает `react-refresh/only-export-components`
 * (правило не выключено для `src/components/domain/**`, см. `eslint.config.js`).
 */
export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const;
