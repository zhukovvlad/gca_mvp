"""Общие представления «где строка»: путь по разделам сметы и статья классификатора.

Вынесены из `crud/our_price.py`, чтобы `crud/suspicious_lines.py` брал их, не импортируя экран
«КП против нашей цены» (зависимости идут в одну сторону: `our_price → suspicious_lines →
line_views`, цикла нет; решение 7 плана «Подозрительные строки»). Тела — без изменений.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from crud.our_price_load import CategoryMeta
from services.context_routing import ChapterStep


def path_view(steps: Sequence[ChapterStep]) -> list[dict]:
    """Путь по разделам сметы от её корня: ключ, название, номер раздела в файле."""
    return [{"chapter_item_id": s.chapter_item_id, "title": s.title, "number": s.number} for s in steps]


def category_chain(tree: Mapping[int, CategoryMeta], category_id: int) -> list[CategoryMeta]:
    """Статьи от корня до `category_id` включительно."""
    chain = [tree[category_id]]
    while chain[-1].parent_id is not None:
        chain.append(tree[chain[-1].parent_id])
    chain.reverse()
    return chain


def article_ref(tree: Mapping[int, CategoryMeta], category_id: int | None) -> dict | None:
    """Статья со своей цепочкой от корня (`chain` кончается самой статьёй); без статьи — `None`."""
    if category_id is None:
        return None
    chain = category_chain(tree, category_id)
    node = chain[-1]
    return {
        "id": node.id,
        "code": node.code,
        "title": node.title,
        "chain": [{"id": step.id, "code": step.code, "title": step.title} for step in chain],
    }
