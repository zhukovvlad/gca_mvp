/**
 * Реестр статей справочника.
 *
 * Куда положить: frontend/src/pages/handbook/articles.ts
 *
 * Единственное место, куда нужно добавить запись, чтобы появилась новая статья:
 * она автоматически попадёт в список раздела /handbook и станет доступна по
 * своему slug на /handbook/:slug.
 */

import type { ComponentType } from "react";

import ConcreteAveragePrice from "./ConcreteAveragePrice";

export interface HandbookArticleMeta {
  /** Часть URL: /handbook/<slug> */
  slug: string;
  title: string;
  /** Короткое описание для карточки в списке */
  description: string;
  /** Группа в списке раздела */
  category: string;
  readingMinutes: number;
  /** ISO-дата последнего обновления */
  updated: string;
  Component: ComponentType;
}

export const HANDBOOK_ARTICLES: HandbookArticleMeta[] = [
  {
    slug: "concrete-average-price",
    title: "Расчёт средней стоимости бетона",
    description:
      "Как приложение получает реальную цену кубометра бетона с учётом доставки и присадок, разнесённых на каждую поставку.",
    category: "Расчёты и методология",
    readingMinutes: 8,
    updated: "2026-06-01",
    Component: ConcreteAveragePrice,
  },
];

export function getHandbookArticle(slug: string | undefined): HandbookArticleMeta | undefined {
  return HANDBOOK_ARTICLES.find((a) => a.slug === slug);
}

export interface HandbookCategoryGroup {
  category: string;
  items: HandbookArticleMeta[];
}

export function groupByCategory(articles: HandbookArticleMeta[]): HandbookCategoryGroup[] {
  const map = new Map<string, HandbookArticleMeta[]>();
  for (const a of articles) {
    const list = map.get(a.category) ?? [];
    list.push(a);
    map.set(a.category, list);
  }
  return Array.from(map, ([category, items]) => ({ category, items }));
}
