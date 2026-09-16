"""Генератор макета листа «Изменения КП» на данных стенда gca_dev.

Числа НЕ выдуманы: всё, что печатает макет, посчитано запросом к стенду по
трассе одного участника — макет гейта 1, живёт рядом со спекой.

Все числа, на которые опирается дизайн, воспроизводит `evidence.py` — он считает
их СВОИМ SQL и намеренно не импортирует этот модуль: сверщик, делящий предикат
с генератором, доказывает согласованность, а не правильность. Сходимость листа
с базой проверяет `check_convergence.py`, свойства колонки «Что двигалось» —
`measure_routes.py`.

Правила, которые лист обязан держать:

* **Три множества строк разведены.** Присутствие — независимо от денег; СУММА —
  по конечным `total_cost_total`; ЦЕНА — правилом матрицы
  `Σ(unit_cost_total × suggested_quantity) / Σ suggested_quantity` по строкам,
  где цена И вес пригодны. Деление свёрнутой суммы на свёрнутый объём занижало
  цену вдвое на ячейке с нерасценённой строкой.
* **Предикаты цены и веса — дословные, из миграции `0016`.** Самосверка `x = x`
  не исключает `NaN`, а `> 0` пропускает `NaN` и `Infinity`: PostgreSQL считает
  `'NaN'::numeric = 'NaN'::numeric` и `'NaN'::numeric > 0` ИСТИНОЙ.
* **Пустая сумма — состояние, а не ноль.** Строки нет → нулевой вклад в статью;
  строка есть без конечной суммы → сумма недоступна, и Δ с таким концом тоже;
  конечный ноль → число.
* **Концы «первый → последний» — крайние ЭТАПЫ**, а не первое и последнее
  появление строки: иначе Δ у 596 строк из 1 365 считалась бы не от первого
  этапа и не складывалась бы в движение подытога статьи.
* **Соседние этапы — соседние ПО ШКАЛЕ.** Пропуск промежуточного этапа нельзя
  склеивать: иначе шаг Э1→Э3 объявляется существующим.
* **Ключ допработ — `(lots.lot_key, chapter_ref_raw)`**, как в действующем
  раскрытии: `lots.id` свой у каждой сметы раунда.
* **Область данных плоская.** Заголовков статей внутри таблицы нет — иначе
  автофильтр и сортировка перемешали бы их со строками работ.
* Непривязанная позиция получает явный синтетический ключ, а не теряется.

Ключ строки — каталожная позиция ВНУТРИ статьи; подписи «исчезла» и «появилась»
считаются по работе целиком, иначе переезд между статьями выглядит уступкой.
Подпись о статье строко-локальна: источник говорит «ушла в 2.5», получатель —
«пришла из 2.3».
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from decimal import Decimal
from html import escape

import psycopg

from evidence import collect

# Скрипт печатает кириллицу и обязан работать от буквальной команды
# `python gen_mockup.py`, без внешнего PYTHONIOENCODING.
sys.stdout.reconfigure(encoding="utf-8")

DSN = "postgresql://postgres@localhost:5459/gca_dev"
TENDER = 3
PARTICIPANT = 'ООО "АНТТЕК"'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mockup.html")

#: Предикат цены — ДОСЛОВНО из `backend/alembic/versions/2026_09_09_0016-price_predicate.py`.
#: Каждое исключение обязательно: `NaN > 0` и `Infinity > 0` в PostgreSQL истинны.
PRICE_OK = """pi.unit_cost_total > 0
          AND pi.unit_cost_total <> 'NaN'::numeric
          AND pi.unit_cost_total <> 'Infinity'::numeric
          AND pi.unit_cost_total <> '-Infinity'::numeric"""

#: Предикат ВЕСА — той же формы и по той же причине: `'NaN'::numeric > 0` и
#: `'Infinity'::numeric > 0` в PostgreSQL истинны, поэтому одного `> 0` мало.
#: Матрица (AGENTS.md §6) требует от веса конечности и положительности.
WEIGHT_OK = """pi.suggested_quantity > 0
          AND pi.suggested_quantity <> 'NaN'::numeric
          AND pi.suggested_quantity <> 'Infinity'::numeric
          AND pi.suggested_quantity <> '-Infinity'::numeric"""

#: Конечность денежной величины — то же правило, что у `v_category_totals` и у
#: попозиционного раскрытия: нефинитная строка СЧИТАЕТСЯ в счётчике, но не
#: входит в сумму.
def finite(expr: str) -> str:
    return (f"{expr} IS NOT NULL AND {expr} <> 'NaN'::numeric "
            f"AND {expr} <> 'Infinity'::numeric AND {expr} <> '-Infinity'::numeric")


#: Точность денег: два знака. Тождество состава проверяется на ИТОГЕ ГРУППЫ,
#: приведённом к этой точности, и допуска не имеет.
#:
#: Построчный допуск «не больше копейки» был ошибкой: он копится. Двадцать шесть
#: строк — двадцать шесть копеек, а между двумя концами Δ вдвое больше, и лист
#: печатает деньги с двумя знаками, то есть расхождение НАБЛЮДАЕМО. Обещать
#: тождество и допускать накопление нельзя одновременно.
MONEY_PRECISION = Decimal("0.01")

TENDER_SQL = """
SELECT t.tender_number, t.title, o.title AS object_title, o.address
FROM tenders t
LEFT JOIN objects o ON o.id = t.object_id
WHERE t.id = %s
"""

STAGES_SQL = """
SELECT r.stage_no, r.label, r.held_on
FROM offer_packages pk
JOIN contractors c ON c.id = pk.contractor_id
JOIN offers o ON o.package_id = pk.id
JOIN tender_rounds r ON r.id = o.round_id
JOIN estimates e ON e.offer_id = o.id
WHERE pk.tender_id = %s AND c.title = %s
ORDER BY r.stage_no
"""

GROUPS_SQL = f"""
SELECT r.stage_no,
       ch.work_category_id AS article_id,
       wc.code AS article_code,
       wc.title AS article_title,
       pi.catalog_position_id AS work_id,
       cp.standard_job_title AS work_title,
       cp.kind AS catalog_kind,
       u.code AS unit,
       -- присутствие: все строки, независимо от пригодности денег
       count(*) AS rows_all,
       -- сумма: только конечные величины
       count(*) FILTER (WHERE {finite('pi.total_cost_total')}) AS rows_with_amount,
       sum(pi.total_cost_total) FILTER (WHERE {finite('pi.total_cost_total')}) AS amount,
       sum(pi.total_cost_works) FILTER (WHERE {finite('pi.total_cost_works')}) AS c_works,
       sum(pi.total_cost_materials) FILTER (WHERE {finite('pi.total_cost_materials')}) AS c_materials,
       sum(pi.total_cost_indirect_costs) FILTER (WHERE {finite('pi.total_cost_indirect_costs')}) AS c_indirect,
       -- ПОСТРОЧНОЕ условие — только полнота данных: у строки есть конечный итог
       -- и все три конечные составляющие. Равенство здесь НЕ проверяется: оно
       -- утверждается про итог группы и считается на нём (`mix_complete`),
       -- иначе построчный допуск копился бы по строкам группы.
       count(*) FILTER (WHERE {finite('pi.total_cost_total')}
                          AND {finite('pi.total_cost_works')}
                          AND {finite('pi.total_cost_materials')}
                          AND {finite('pi.total_cost_indirect_costs')}) AS rows_with_mix,
       -- объём присутствия (для показа) и объём пригодных строк (для цены)
       sum(pi.suggested_quantity) FILTER (WHERE {finite('pi.suggested_quantity')}) AS qty,
       count(*) FILTER (WHERE {PRICE_OK} AND {WEIGHT_OK}) AS rows_priced,
       sum(pi.unit_cost_total * pi.suggested_quantity)
         FILTER (WHERE {PRICE_OK} AND {WEIGHT_OK}) AS price_num,
       sum(pi.suggested_quantity)
         FILTER (WHERE {PRICE_OK} AND {WEIGHT_OK}) AS price_den,
       sum(pi.unit_cost_works * pi.suggested_quantity)
         FILTER (WHERE {PRICE_OK} AND {WEIGHT_OK}) AS unit_works_num,
       sum(pi.unit_cost_materials * pi.suggested_quantity)
         FILTER (WHERE {PRICE_OK} AND {WEIGHT_OK}) AS unit_materials_num,
       sum(pi.unit_cost_indirect_costs * pi.suggested_quantity)
         FILTER (WHERE {PRICE_OK} AND {WEIGHT_OK}) AS unit_indirect_num,
       string_agg(pi.item_number_in_proposal, ', ' ORDER BY pi.item_number_in_proposal) AS numbers
FROM offer_packages pk
JOIN contractors c ON c.id = pk.contractor_id
JOIN offers o ON o.package_id = pk.id
JOIN tender_rounds r ON r.id = o.round_id
JOIN estimates e ON e.offer_id = o.id
JOIN lots l ON l.estimate_id = e.id
JOIN proposals p ON p.lot_id = l.id
JOIN position_items pi ON pi.proposal_id = p.id AND pi.is_chapter = false
LEFT JOIN position_items ch ON ch.id = pi.chapter_item_id
LEFT JOIN work_categories wc ON wc.id = ch.work_category_id
LEFT JOIN catalog_positions cp ON cp.id = pi.catalog_position_id
LEFT JOIN units_of_measure u ON u.id = pi.unit_id
WHERE pk.tender_id = %s AND c.title = %s
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
"""

#: Вторая ветвь свода. Ключ — `lot_key` плюс ссылка: ссылка «3.2.2» в разных
#: лотах законно означает разные работы, а `lots.id` свой у каждой сметы раунда.
ADDITIONAL_SQL = f"""
SELECT r.stage_no,
       aw.work_category_id AS article_id,
       wc.code AS article_code,
       wc.title AS article_title,
       l.lot_key,
       aw.chapter_ref_raw,
       min(aw.title) AS work_title,
       sum(aw.total_amount) FILTER (WHERE {finite('aw.total_amount')}) AS amount,
       count(*) AS rows_all,
       count(*) FILTER (WHERE {finite('aw.total_amount')}) AS rows_with_amount
FROM offer_packages pk
JOIN contractors c ON c.id = pk.contractor_id
JOIN offers o ON o.package_id = pk.id
JOIN tender_rounds r ON r.id = o.round_id
JOIN estimates e ON e.offer_id = o.id
JOIN lots l ON l.estimate_id = e.id
JOIN proposals p ON p.lot_id = l.id
JOIN estimate_additional_works aw ON aw.proposal_id = p.id
LEFT JOIN work_categories wc ON wc.id = aw.work_category_id
WHERE pk.tender_id = %s AND c.title = %s
GROUP BY 1, 2, 3, 4, 5, 6
"""

NBSP = " "
COMPONENTS = ("works", "materials", "indirect")
UNALLOCATED = ("—", "Нераспределённое")

#: Виды каталожной строки, которые НЕ являются работой. Их деньги свод считает
#: наравне с работами, поэтому из книги они не выбрасываются, а собираются в
#: названную диагностическую группу на статью.
#: Ветви, у которых на листе есть ТОЛЬКО сумма. Объём, цена за единицу, состав и
#: номера строк им неприменимы по смыслу: допработа несёт в файле одну сумму, а
#: диагностическая группа собирает разнородные строки, у которых общей единицы
#: нет. Прежняя редакция объявляла это в спеке, но печатала им всё как работам.
MONEY_ONLY_KINDS = ("additional", "nonwork", "unmatched")

NON_WORK_KINDS = {
    "HEADER": "Строки, размеченные как заголовок раздела",
    "LOT_HEADER": "Строки, размеченные как заголовок лота",
    "TRASH": "Строки, размеченные как мусор",
}
BRIEF_LIMIT = 2


def money(value) -> str:
    if value is None:
        return "—"
    return f"{Decimal(value).quantize(Decimal('1')):,}".replace(",", NBSP)


def quantity(value) -> str:
    if value is None:
        return "—"
    return f"{Decimal(value).normalize():f}"


def brief(codes) -> str:
    ordered = sorted(codes)
    if len(ordered) <= BRIEF_LIMIT:
        return ", ".join(ordered)
    return f"{', '.join(ordered[:BRIEF_LIMIT])} и ещё {len(ordered) - BRIEF_LIMIT}"


def load():
    with psycopg.connect(DSN) as conn:
        header = conn.execute(TENDER_SQL, (TENDER,)).fetchone()
        stages = conn.execute(STAGES_SQL, (TENDER, PARTICIPANT)).fetchall()
        positions = conn.execute(GROUPS_SQL, (TENDER, PARTICIPANT)).fetchall()
        additional = conn.execute(ADDITIONAL_SQL, (TENDER, PARTICIPANT)).fetchall()
    return header, stages, positions, additional


def blank(kind: str) -> dict:
    """Ячейка. Технические поля есть у ВСЕХ ветвей, гасятся только при выводе.

    Прежняя редакция не заводила `qty`, `price_*` и состав денежным ветвям — и
    `build()` падал на них `KeyError: 'qty'`, потому что агрегировал безусловно.
    На стенде это недостижимо (каталог целиком в `TO_REVIEW`), и поймал дефект
    синтетический вход `check_diagnostic_branches.py`. Скрывать величину надо
    там, где её показывают, а не там, где её складывают.
    """
    cell = {"kind": kind, "amount": Decimal(0), "rows_all": 0, "rows_with_amount": 0,
            "rows_with_mix": 0, "rows_priced": 0, "numbers": [],
            "qty": Decimal(0), "price_num": Decimal(0), "price_den": Decimal(0)}
    for name in COMPONENTS:
        cell[name] = Decimal(0)
        cell[f"unit_{name}_num"] = Decimal(0)
    return cell


def mix_complete(cell) -> bool:
    """Полон ли состав группы — два независимых условия, оба обязательны.

    1. **Данные полны построчно:** у каждой строки, вошедшей в сумму, есть все
       три конечные составляющие (счётчик из SQL). Компоненты объявлены
       `nullable`, и пропуск одного делает разложение частичным.
    2. **Итог группы сходится ТОЧНО** на точности показа: сумма трёх
       агрегатов, приведённая к копейке, равна приведённому к копейке итогу.
       Импорт читает четыре величины независимо, `CHECK`-а на их согласие в
       схеме нет.

    Допуска у второго условия нет намеренно. Построчный допуск «не больше
    копейки» копится: двадцать шесть строк дают двадцать шесть копеек, а между
    концами Δ вдвое больше — и это видно на листе, который печатает деньги с
    двумя знаками. Обещать тождество и допускать накопление одновременно
    нельзя; выбрано тождество.
    """
    if cell is None or cell["rows_with_mix"] != cell["rows_with_amount"]:
        return False
    parts = sum((cell[name] for name in COMPONENTS), Decimal(0))
    return (parts.quantize(MONEY_PRECISION)
            == cell["amount"].quantize(MONEY_PRECISION))


def dec(value) -> Decimal:
    return Decimal(value) if value is not None else Decimal(0)


def amount_of(cell):
    """Сумма ячейки. `None` — состояние «конечной суммы нет», а не ноль.

    Три различных состояния, которые прежняя редакция смешивала подменой
    `dec(None) -> 0`: строки нет вовсе (ячейка `None`); строка есть, но ни одной
    конечной `total_cost_total` (здесь `None`); сумма действительно ноль (число).
    На стенде вторая ветвь недостижима — `сумма NULL: 0`, — и в реализации её
    обязан предъявить тестовый вход.
    """
    if cell is None or cell["rows_with_amount"] == 0:
        return None
    return cell["amount"]


def unit_price(cell):
    """Цена группы — правило матрицы. `None` значит «пригодной цены нет»."""
    den = cell.get("price_den") or 0
    return (cell["price_num"] / den) if den else None


def per_unit_mix(cell):
    """Состав на единицу по тому же входящему множеству, что и цена."""
    den = cell.get("price_den") or 0
    if not den:
        return None
    return tuple(cell[f"unit_{name}_num"] / den for name in COMPONENTS)


def build(stages, positions, additional):
    idx_of = {stage[0]: index for index, stage in enumerate(stages)}
    width = len(stages)
    cells = defaultdict(lambda: [None] * width)
    meta: dict = {}
    articles_by_stage = defaultdict(lambda: defaultdict(set))

    for row in positions:
        (stage_no, article_id, code, article_title, work_id, work_title, catalog_kind,
         unit, rows_all, rows_with_amount, amount, c_works, c_materials, c_indirect,
         rows_with_mix, qty, rows_priced, price_num, price_den,
         unit_works_num, unit_materials_num, unit_indirect_num, numbers) = row
        article = (code, article_title) if article_id is not None else UNALLOCATED
        # Ни одна строка не выбрасывается: `v_category_totals` фильтра по виду
        # каталожной записи НЕ имеет (миграция 0012, `WHERE pi.is_chapter =
        # false`), поэтому книга обязана сохранить каждый рубль свода. Неработы
        # и непривязанные строки уходят в НАЗВАННЫЕ диагностические группы, а не
        # пропадают.
        if work_id is None:
            key = ("unmatched", article, "unmatched")
            title = "Непривязанные строки сметы"
        elif catalog_kind in NON_WORK_KINDS:
            key = ("nonwork", article, catalog_kind)
            title = NON_WORK_KINDS[catalog_kind]
        else:
            key = ("work", article, work_id)
            title = work_title
        index = idx_of[stage_no]
        cell = cells[key][index] or blank(key[0])
        cell["rows_all"] += rows_all
        cell["rows_with_amount"] += rows_with_amount
        cell["rows_with_mix"] += rows_with_mix
        cell["rows_priced"] += rows_priced
        cell["amount"] += dec(amount)
        cell["qty"] += dec(qty)
        cell["price_num"] += dec(price_num)
        cell["price_den"] += dec(price_den)
        for name, total, unit_num in (("works", c_works, unit_works_num),
                                      ("materials", c_materials, unit_materials_num),
                                      ("indirect", c_indirect, unit_indirect_num)):
            cell[name] += dec(total)
            cell[f"unit_{name}_num"] += dec(unit_num)
        if numbers:
            cell["numbers"].append(numbers)
        cells[key][index] = cell
        meta.setdefault(key, {"work": title, "unit": unit or "—", "article": article,
                              "kind": key[0], "catalog_kind": catalog_kind})
        if work_id is not None:
            articles_by_stage[work_id][stage_no].add(article[0])

    for row in additional:
        (stage_no, article_id, code, article_title, lot_key, chapter_ref,
         work_title, amount, rows_all, rows_with_amount) = row
        article = (code, article_title) if article_id is not None else UNALLOCATED
        key = ("additional", article, (lot_key, chapter_ref))
        index = idx_of[stage_no]
        cell = cells[key][index] or blank("additional")
        cell["amount"] += dec(amount)
        cell["rows_all"] += rows_all
        cell["rows_with_amount"] += rows_with_amount
        cells[key][index] = cell
        meta.setdefault(key, {"work": f"Допработы: {work_title}", "unit": "—",
                              "article": article, "kind": "additional",
                              "catalog_kind": None})
    return cells, meta, articles_by_stage


def route(key, cells, meta, articles_by_stage, stages):
    """Маршрут по шагам. Подпись о статье — строко-локальная."""
    series = cells[key]
    info = meta[key]
    own_article = key[1][0]
    by_stage = articles_by_stage.get(key[2], {}) if info["kind"] == "work" else {}
    steps = []
    for i in range(1, len(series)):
        prev, cur = series[i - 1], series[i]
        s_prev, s_cur = stages[i - 1][0], stages[i][0]
        words = []

        if info["kind"] == "work":
            set_prev, set_cur = by_stage.get(s_prev, set()), by_stage.get(s_cur, set())
            here_prev, here_cur = own_article in set_prev, own_article in set_cur
            if here_prev and not here_cur:
                words.append("исчезла из КП" if not set_cur
                             else "ушла в " + brief(set_cur - set_prev or set_cur))
            elif here_cur and not here_prev:
                words.append("появилась в КП" if not set_prev
                             else "пришла из " + brief(set_prev - set_cur or set_prev))
            elif here_prev and here_cur:
                added, removed = set_cur - set_prev, set_prev - set_cur
                if added:
                    words.append("часть ушла в " + brief(added))
                if removed:
                    words.append("часть пришла из " + brief(removed))
        elif prev is not None and cur is None:
            words.append("исчезла из КП")
        elif prev is None and cur is not None:
            # Любой переход «нет → есть» структурен. Прежняя редакция требовала,
            # чтобы строка встречалась ЕЩЁ РАНЬШЕ, и оставляла шаг Э1→Э2 без
            # причины у всякой строки, появившейся на втором этапе.
            words.append("появилась в КП")

        if prev is not None and cur is not None:
            prev_amount, cur_amount = amount_of(prev), amount_of(cur)
            if prev_amount is None or cur_amount is None:
                if prev_amount is not cur_amount:
                    words.append("сумма недоступна")
            elif prev_amount != cur_amount:
                words.append("сумма")
            if info["kind"] not in MONEY_ONLY_KINDS:
                if prev["qty"] != cur["qty"]:
                    words.append("объём")
                if unit_price(prev) != unit_price(cur):
                    words.append("цена")
                if per_unit_mix(prev) != per_unit_mix(cur):
                    words.append("состав")
        if words:
            steps.append(f"Э{s_prev}→Э{s_cur}: " + ", ".join(words))
    return steps


def completeness(series, stages) -> str:
    """Подпись полноты: сумма и цена считаются по РАЗНЫМ множествам строк."""
    notes = []
    for index, cell in enumerate(series):
        if cell is None:
            continue
        parts = []
        if cell["rows_with_amount"] < cell["rows_all"]:
            parts.append(f"сумма {cell['rows_with_amount']}/{cell['rows_all']}")
        if cell["kind"] not in MONEY_ONLY_KINDS and cell["rows_priced"] < cell["rows_all"]:
            parts.append(f"цена {cell['rows_priced']}/{cell['rows_all']}")
        if parts:
            notes.append(f"Э{stages[index][0]}: " + ", ".join(parts))
    return "; ".join(notes)


def cell_html(cell) -> str:
    if cell is None:
        # Отсутствие строки на этапе — нулевой вклад в ЭТУ статью, а не пустота.
        return '<td class="dash">—</td>' * 4
    if cell["kind"] in MONEY_ONLY_KINDS:
        value = amount_of(cell)
        amount_td = ('<td class="num state">суммы нет</td>' if value is None
                     else f'<td class="num strong">{money(value)}</td>')
        return ('<td class="dash">—</td><td class="dash">—</td><td class="dash">—</td>'
                + amount_td)
    numbers = escape(", ".join(cell["numbers"]))
    price = unit_price(cell)
    price_td = ('<td class="num state">цены нет</td>' if price is None
                else f'<td class="num">{money(price)}</td>')
    partial = cell["rows_with_amount"] < cell["rows_all"] or cell["rows_priced"] < cell["rows_all"]
    value = amount_of(cell)
    amount_td = ('<td class="num state">суммы нет</td>' if value is None
                 else f'<td class="num strong{" partial" if partial else ""}">'
                      f'{money(value)}</td>')
    return (f'<td class="num small">{numbers}</td>'
            f'<td class="num">{quantity(cell["qty"])}</td>{price_td}{amount_td}')


def render_rows(keys, cells, meta, routes, stages):
    out = []
    for key in keys:
        info = meta[key]
        series = cells[key]
        row_class = {"additional": ' class="aw"', "unmatched": ' class="um"',
                     "nonwork": ' class="um"'}.get(info["kind"], "")
        code, title = info["article"]
        tds = [f'<td class="code">{escape(code)}</td>',
               f'<td class="art">{escape(title)}</td>',
               f'<td class="t">{escape(info["work"])}</td>',
               f'<td class="u">{escape(info["unit"])}</td>']
        tds += [cell_html(cell) for cell in series]

        # Концы — КРАЙНИЕ ЭТАПЫ. Отсутствие строки значит нулевой вклад в эту
        # статью, поэтому абсолютная Δ считается от нуля и к нулю.
        first, last = series[0], series[-1]
        # Отсутствие строки — нулевой вклад в ЭТУ статью. Присутствие без
        # конечной суммы — НЕ ноль: Δ с таким концом недоступна с причиной.
        start = Decimal(0) if first is None else amount_of(first)
        end = Decimal(0) if last is None else amount_of(last)
        if start is None or end is None:
            tds.append('<td class="num state" colspan="2">Δ недоступна: суммы нет</td>')
        else:
            delta = end - start
            tone = "dn" if delta < 0 else "up" if delta > 0 else ""
            tds.append(f'<td class="num strong {tone}">{money(delta)}</td>')
            # Процент недоступен при отсутствующем или неположительном начале.
            pct = (f'{(end / start - 1) * 100:+.1f}'.replace(".", ",") + f"{NBSP}%"
                   if first is not None and start > 0 else "—")
            tds.append(f'<td class="num">{pct}</td>')
        # Разложение обещает тождество с Δ суммы, поэтому печатается ТОЛЬКО когда
        # состав полон на обоих концах. Отсутствующий конец полон по построению:
        # его вклад — ноль по всем трём составляющим.
        if info["kind"] in MONEY_ONLY_KINDS:
            tds += ['<td class="num mix">—</td>'] * len(COMPONENTS)
        elif not all(mix_complete(end_cell) for end_cell in (first, last)
                     if end_cell is not None):
            tds.append('<td class="num state mix" colspan="3">состав неполон</td>')
        else:
            for name in COMPONENTS:
                diff = ((last[name] if last else Decimal(0))
                        - (first[name] if first else Decimal(0)))
                tds.append(f'<td class="num mix">{money(diff) if diff != 0 else "—"}</td>')

        tds.append(f'<td class="full">{completeness(series, stages)}</td>')
        steps = routes[key]
        tds.append(f'<td class="why">'
                   f'{"<br>".join(escape(s) for s in steps) if steps else "без изменений"}</td>')
        out.append(f"<tr{row_class}>" + "".join(tds) + "</tr>")
    return "\n".join(out)


def measurements_html(data: dict) -> str:
    """Блок замеров: всё, на что ссылается спека, одной таблицей.

    Считает `evidence.py` своим SQL; здесь только разметка. Ни одно число не
    вписывается руками — иначе спека и макет разъедутся, как это уже было на
    попозиционном раскрытии.
    """
    edge = " · ".join(f"{escape(name)}: <b>{value}</b>"
                      for name, value in data["edge"].items())
    catalog = " · ".join(f"{kind}: <b>{count}</b>" for kind, count in data["catalog"])
    split = data["split"]
    rows = []
    for trace in data["traces"]:
        moves = "<br>".join(
            f"Э{a}→Э{b}: {count} из {total} ({round(100 * count / total)}%)"
            for (a, b), count, total in trace["moves"]
        ) or "нет"
        worst = (f"{money(trace['worst_price'][1])} → {money(trace['worst_price'][2])}"
                 if trace["worst_price"] else "—")
        rows.append(f"""<tr>
<td class="t">тендер {trace['tender']}<br>{escape(trace['participant'])}</td>
<td class="num">{len(trace['stages'])}</td>
<td class="num">{trace['works']}</td>
<td class="num">{trace['rows']}</td>
<td class="num">{trace['gone_article']} / {trace['gone_work']}<br>
    <b>{trace['gone_article'] - trace['gone_work']}</b> ложных</td>
<td class="num">{trace['pairs']}</td>
<td class="num">{trace['mix_abs']} / {trace['mix_unit']}</td>
<td class="num">{trace['late']} / {trace['early']}</td>
<td class="num">{trace['partial_cells']}<br><span class="small">{worst}</span></td>
<td class="why">{moves}</td>
<td class="num">{trace['numbers_kept']} из {trace['numbers_pairs']}</td>
<td class="num">{trace['aw_rows']} строк<br>{trace['aw_by_lot_id']} / {trace['aw_by_lot_key']}</td>
</tr>""")
    return f"""<div class="facts">
Каталог стенда: {catalog} — фильтр <code>kind='POSITION'</code> оставил бы книгу пустой<br>
Краевые значения, обе трассы: {edge}<br>
Работы в нескольких статьях (последний этап): <b>{split['split_works']}</b> из
{split['works']}, на них {money(split['money_split'])} из {money(split['money_all'])}
(<b>{round(100 * split['money_split'] / split['money_all'])}%</b> денег)
</div>
<div class="scroll">
<table>
<tr><th>Трасса</th><th>Эта­пов</th><th>Работ</th><th>Строк<br>листа</th>
    <th>Исчезновений<br>статья / работа</th><th>Пар<br>соседних</th>
    <th>Состав двинулся<br>абсолют / на единицу</th>
    <th>Строк с поздним<br>началом / ранним концом</th>
    <th>Ячеек с частичной ценой<br>худшее расхождение</th>
    <th>Переклассификация</th><th>Номер строки<br>устоял</th>
    <th>Допработы<br>lots.id / lot_key</th></tr>
{"".join(rows)}
</table>
</div>"""


def main() -> None:
    header, stages, positions, additional = load()
    tender_number, tender_title, object_title, object_address = header
    cells, meta, articles_by_stage = build(stages, positions, additional)
    width = len(stages)
    routes = {key: route(key, cells, meta, articles_by_stage, stages) for key in cells}

    def size_of(key):
        series = cells[key]
        last = next((c for c in reversed(series) if c is not None), None)
        return abs(amount_of(last) or Decimal(0)) if last else Decimal(0)

    by_article = defaultdict(list)
    for key in cells:
        by_article[meta[key]["article"]].append(key)

    # Показать разрез, где видно всё разом: допработы, непригодные цены,
    # частичные свёртки и переезды.
    interesting = {
        article: sum(1 for key in keys
                     if meta[key]["kind"] != "work"
                     or routes[key] and any("ушла" in s or "пришла" in s for s in routes[key])
                     or any(c and c["rows_priced"] < c["rows_all"] for c in cells[key]))
        for article, keys in by_article.items()
    }
    top = [a for a, _ in sorted(interesting.items(), key=lambda kv: -kv[1])[:3]]
    shown = []
    for article in top:
        shown += sorted(by_article[article], key=size_of, reverse=True)[:8]

    aw_keys = sorted([k for k in cells if meta[k]["kind"] == "additional"],
                     key=size_of, reverse=True)
    moved = [k for k in cells if any("ушла в" in s or "пришла из" in s for s in routes[k])]
    sample_work = next((k[2] for k in moved if meta[k]["kind"] == "work"), None)
    both_sides = sorted([k for k in cells if k[2] == sample_work], key=lambda k: k[1][0])
    late_start = [k for k in cells if cells[k][0] is None and cells[k][-1] is not None][:5]
    no_price = [k for k in cells
                if any(c and c["kind"] != "additional" and unit_price(c) is None
                       for c in cells[k])][:5]
    partial_rows = [k for k in cells
                    if any(c and 0 < c["rows_priced"] < c["rows_all"] for c in cells[k])][:5]

    head_stage = "".join(
        f'<th colspan="4" class="grp">Этап {no}'
        f'{" · " + held.strftime("%d.%m.%Y") if held else ""}</th>'
        for no, _label, held in stages
    )
    head_sub = "".join(
        "<th>№ строк</th><th>Объём</th><th>Цена за ед.</th><th>Сумма</th>" for _ in stages
    )
    head_tail = ('<th>Δ сумма</th><th>Δ %</th><th class="mix">Δ работы</th>'
                 '<th class="mix">Δ материалы</th><th class="mix">Δ косв.</th>')

    def table(keys, caption=""):
        return f"""{caption}
<div class="scroll">
<table>
<tr><th rowspan="2">Статья</th><th rowspan="2">Наименование статьи</th>
    <th rowspan="2">Наименование работы</th><th rowspan="2">Ед.</th>{head_stage}
    <th colspan="5" class="grp">Итог торга: Э{stages[0][0]} → Э{stages[-1][0]}</th>
    <th rowspan="2">Полнота</th><th rowspan="2">Что двигалось</th></tr>
<tr>{head_sub}{head_tail}</tr>
{render_rows(keys, cells, meta, routes, stages)}
</table>
</div>"""

    # Подытоги — ОТДЕЛЬНЫМ блоком, не строками внутри области автофильтра.
    subtotals = []
    for article in sorted(by_article, key=lambda a: a[0]):
        sums = []
        for index in range(width):
            total = sum((amount_of(cells[k][index]) or Decimal(0)
                         for k in by_article[article]), Decimal(0))
            sums.append(total)
        subtotals.append((article, sums, len(by_article[article])))

    subtotal_rows = "\n".join(
        f'<tr><td class="code">{escape(code)}</td><td class="art">{escape(title)}</td>'
        f'<td class="num small">{count}</td>'
        + "".join(f'<td class="num">{money(value)}</td>' for value in sums)
        + f'<td class="num strong">{money(sums[-1] - sums[0])}</td></tr>'
        for (code, title), sums, count in subtotals
    )
    grand = [sum((s[index] for _a, s, _c in subtotals), Decimal(0)) for index in range(width)]

    # Переклассификация считается по СОСЕДНИМ ПО ШКАЛЕ этапам, а не по
    # «следующим присутствующим»: пропуск промежуточного этапа склеивал бы
    # Э1 с Э3 и печатал несуществующий шаг. Оба множества статей обязаны быть
    # непусты — появление работы после пустого этапа не переклассификация.
    stage_numbers = [stage[0] for stage in stages]
    moves_per_step = defaultdict(set)
    for work_id, by_stage in articles_by_stage.items():
        for a, b in zip(stage_numbers, stage_numbers[1:]):
            set_a, set_b = by_stage.get(a, set()), by_stage.get(b, set())
            if set_a and set_b and set_a != set_b:
                moves_per_step[(a, b)].add(work_id)
    works_total = len(articles_by_stage)
    mass_note = " · ".join(
        f"Э{a}→Э{b}: <b>{len(w)}</b> из {works_total} ({round(100 * len(w) / works_total)}%)"
        for (a, b), w in sorted(moves_per_step.items())
    ) or "переездов нет"

    kinds = defaultdict(int)
    for info in meta.values():
        if info["catalog_kind"]:
            kinds[info["catalog_kind"]] += 1
    no_price_cells = sum(1 for k in cells for c in cells[k]
                         if c and c["kind"] != "additional" and unit_price(c) is None)
    partial_cells = sum(1 for k in cells for c in cells[k]
                        if c and c["rows_priced"] < c["rows_all"])

    html = f"""<!doctype html>
<meta charset="utf-8">
<title>Макет листа «Изменения КП»</title>
<style>
 body {{ font: 13px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif; margin: 24px;
        color: #1a1a1a; background: #fafafa; }}
 h1 {{ font-size: 20px; margin: 0 0 4px; }}
 h2 {{ font-size: 15px; margin: 28px 0 8px; }}
 .lead {{ color: #555; max-width: 94ch; }}
 .facts {{ background: #fff; border: 1px solid #e3e3e3; border-radius: 6px;
          padding: 12px 16px; margin: 16px 0; max-width: 94ch; }}
 .scroll {{ overflow-x: auto; background: #fff; border: 1px solid #e3e3e3; border-radius: 6px; }}
 table {{ border-collapse: collapse; font-size: 12px; white-space: nowrap; }}
 th, td {{ border: 1px solid #e0e0e0; padding: 4px 8px; vertical-align: top; }}
 th {{ background: #f0f0f0; font-weight: 600; text-align: center; }}
 th.grp {{ background: #e4ebf2; }}
 td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
 td.code {{ font-weight: 600; }}
 td.art {{ max-width: 210px; white-space: normal; color: #445; }}
 td.t {{ max-width: 300px; white-space: normal; }}
 td.u {{ text-align: center; color: #666; }}
 td.small {{ font-size: 11px; color: #666; max-width: 110px; white-space: normal; }}
 td.strong {{ font-weight: 600; }}
 td.partial {{ background: #fff4e0; }}
 td.dash {{ text-align: center; color: #bbb; }}
 td.state {{ color: #8a4b00; background: #fff8ec; font-size: 11px; }}
 td.mix {{ background: #fbf7ee; }}
 th.mix {{ background: #f5eede; }}
 td.full {{ font-size: 11px; color: #8a4b00; background: #fffaf0; white-space: normal;
            max-width: 150px; }}
 td.why {{ font-size: 11px; color: #5a4300; background: #fffdf5; white-space: normal;
           max-width: 230px; }}
 tr.aw td {{ background: #f6f4fb; }}
 tr.um td {{ background: #fdeeee; }}
 .up {{ color: #b00020; }}
 .dn {{ color: #0a6b3d; }}
</style>

<h1>Лист «Изменения КП» — макет гейта 1, редакция 3</h1>
<p class="lead"><b>Тендер {escape(tender_number or "")} · {escape(tender_title or "")}</b><br>
Объект: {escape(object_title or "—")}, {escape(object_address or "—")}<br>
Участник: {escape(PARTICIPANT)} · этапов торга: {width}</p>
<p class="lead">Все числа посчитаны запросом к стенду <code>gca_dev</code> этим же скриптом.
Деньги валовые, как в файле — у всех этапов одна известная ставка НДС.
Ценовой уровень номинальный.</p>

<div class="facts">
<b>{len(cells)}</b> строк листа · допработ <b>{len(aw_keys)}</b> ·
вид каталожной строки: {", ".join(f"{k} — <b>{v}</b>" for k, v in kinds.items())}<br>
Ячеек без пригодной цены: <b>{no_price_cells}</b> · с неполной свёрткой: <b>{partial_cells}</b><br>
<b>Классификатор уточнялся между этапами</b> — работ сменили статью: {mass_note}.
Массовость названа здесь один раз, чтобы её не вылавливали из двухсот строк.
</div>

<h2>Область данных — плоская</h2>
<p class="lead">Заголовков статей внутри таблицы НЕТ: автофильтр и сортировка перемешали бы их
со строками работ. Статья живёт колонками в каждой строке, а подытоги стоят
отдельным блоком ниже.</p>
{table(shown)}

<h2>Концы считаются по крайним этапам, а не по появлению строки</h2>
<p class="lead">Строки ниже появляются в своей статье позже первого этапа. Начало у них —
нулевой вклад в ЭТУ статью, поэтому Δ суммы считается от нуля, а процент
недоступен: делить не на что. Так суммы Δ складываются в движение подытога статьи.</p>
{table(late_start)}

<h2>Переезд по классификатору — обе стороны</h2>
<p class="lead">Первые строки — источник и получатели одного переезда: подписи у них разные,
каждая про свою строку.</p>
{table(both_sides)}

<h2>Допработы — вторая ветвь свода</h2>
<p class="lead">Ключ — <code>lot_key</code> плюс ссылка на раздел. По <code>lots.id</code> их
получалось 17 вместо 13: идентификатор лота свой у каждой сметы раунда, и одна
допработа рассыпалась на цепочку ложных появлений. Единицы, объёма и состава у
них нет — файл несёт только сумму, поэтому тождество «три Δ складываются в Δ суммы»
на них не распространяется.</p>
{table(aw_keys)}

<h2>Цены нет — и это не ноль</h2>
<p class="lead">Ноль в цене за единицу по действующему правилу значит «цены нет»: таких ячеек
на трассе {no_price_cells}. В ячейке стоят слова, а не число — складывать там нечего.
Присутствие работы при этом сохраняется, строка из листа не пропадает.</p>
{table(no_price)}

<h2>Полнота — сумма и цена считаются по разным множествам строк</h2>
<p class="lead">Одна работа на этапе — часто несколько строк сметы. Колонка «Полнота» называет,
сколько из них дали сумму и сколько дали цену: это РАЗНЫЕ множества, и подпись
их не смешивает. Сама ячейка суммы остаётся числом — второй ярус внутри неё
сделал бы её текстовой, и `СУММ` по колонке молча дал бы ноль.</p>
{table(partial_rows)}

<h2>Подытоги статей — отдельный блок</h2>
<div class="scroll">
<table>
<tr><th>Статья</th><th>Наименование</th><th>Строк</th>
    {"".join(f"<th>Этап {no}</th>" for no, *_ in stages)}<th>Δ Э{stages[0][0]}→Э{stages[-1][0]}</th></tr>
{subtotal_rows}
<tr><td class="code">—</td><td class="art"><b>ИТОГО</b></td>
    <td class="num small">{len(cells)}</td>
    {"".join(f'<td class="num strong">{money(v)}</td>' for v in grand)}
    <td class="num strong">{money(grand[-1] - grand[0])}</td></tr>
</table>
</div>

<h2 id="measurements">Замеры, на которые опирается дизайн</h2>
<p class="lead">Блок печатается тем же прогоном, что и таблицы выше, а считает его
<code>evidence.py</code> — СВОИМ SQL, не импортируя генератор. Спека цитирует этот блок,
а не пересказ: числа, переписанные в текст руками, на этом проекте уже расходились
с макетом.</p>
{measurements_html(collect())}
"""

    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write(html)
    print(f"written: {OUT}")
    print(f"rows={len(cells)} additional={len(aw_keys)} no_price_cells={no_price_cells} "
          f"partial={partial_cells}")


if __name__ == "__main__":
    main()
