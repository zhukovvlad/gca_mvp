"""SQL-сторона предиката цены и предиката веса — сверка с `money.price` (спека
правила цены, docs/superpowers/specs/2026-09-09-price-predicate-design.md,
§2.2).

Прецедент такой парной сверки в проекте — `test_sql_net_weight_agrees_with_
python` (`test_analytics_api.py`): SQL-сторона собирается ИЗ САМОГО
продакшен-выражения (`_price_ok`/`_weight_ok`/`_not_finite`), а не
переписывается вручную строкой `sa.text(...)` — иначе сверка проверяла бы
третье, независимое выражение, и расхождение именно продакшен-кода с
`money.price` осталось бы незамеченным.

`TRUTH_TABLE` ниже — оракул фичи: та же пара «вход → ожидаемый ответ», что в
`tests/unit/test_price_predicate.py::TRUTH_TABLE` (восемь входов; число задано
длиной кортежа, не текстом). Это сознательное дублирование, не импорт — и
почти каждый тест ниже сверяет ОБА факта с этим литералом отдельно: SQL-ответ
равен оракулу, и Python-ответ (`is_price`/`is_weight`) равен ему же. Прежняя
редакция файла сравнивала SQL с `is_price`/`is_weight` напрямую — то есть
доказывала согласие двух площадок ДРУГ С ДРУГОМ, а не правильность каждой из
них (находка ревью задачи 1): согласованная, но одинаково неверная пара прошла
бы такую сверку тихо.

**Исключение — `TestPriceFilteredOutOfDeviationInputsView` (ревью задачи 4).**
После миграции 0016 сам VIEW фильтрует по предикату цены, и сверка `_price_ok`
над строками, которые этот же `WHERE` уже пропустил, стала бы тавтологией
(её докстрока объясняет почему). Класс проверяет ДРУГОЙ факт — что VIEW
фильтрует, а не что предикат согласен с оракулом, — и не вызывает ни
`_price_ok`, ни `is_price` внутри теста; оракул используется только чтобы
разметить, каким входам полагается выжить.

`NULL` в PostgreSQL трёхзначен: `NULL > 0` даёт не `FALSE`, а `NULL`. Сверка
«в лоб» через `WHERE _price_ok(column)` эту разницу не поймала бы —
`NULL`/`FALSE` там неотличимы, строка просто не выбирается в обоих случаях.
Поэтому здесь предикат СЕЛЕКТИТСЯ как булева колонка (а не фильтрует `WHERE`),
и пустой вход обязан читаться как явный `FALSE`. Эту гарантию даёт
`column.is_not(None)` внутри `_price_ok`/`_weight_ok` — голый `_not_finite` её
не несёт и не обязан (см. `TestNotFiniteOverDeviationInputsView`).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

import pytest
import sqlalchemy as sa

from crud.analytics import DEVIATION_INPUTS, _not_finite, _price_ok, _weight_ok
from models import PositionItem
from money.price import is_price, is_weight

pytestmark = pytest.mark.integration

#: Литеральный оракул фичи — «вход → ожидаемый ответ» правила «конечно и
#: больше нуля» (одна формула, четыре площадки: `is_price`, `is_weight`,
#: `_price_ok`, `_weight_ok`). Дублирует
#: `tests/unit/test_price_predicate.py::TRUTH_TABLE` дословно (см. её
#: докстроку про причину не импортировать и про то, почему синхронность двух
#: копий не нужно стеречь: каждая самодостаточна для своих площадок).
TRUTH_TABLE = (
    ("пусто", None, False),
    ("ноль", Decimal("0"), False),
    ("положительное целое", Decimal("100"), True),
    ("положительное дробное", Decimal("12.34"), True),
    ("отрицательное", Decimal("-100"), False),
    ("NaN", Decimal("NaN"), False),
    ("+Infinity", Decimal("Infinity"), False),
    ("-Infinity", Decimal("-Infinity"), False),
)

#: Оракул `_not_finite` — своя таблица, не производная от `TRUTH_TABLE`:
#: предикат отвечает на другой вопрос («не число», а не «пригодная цена»), и
#: его пустой вход — не `False`, а SQL `NULL` (Python `None`): OR трёх
#: сравнений с `NULL` сам не может дать ни `TRUE`, ни `FALSE`. `_not_finite`
#: не обязан закрывать эту трёхзначность сам — эту гарантию несёт снаружи
#: `column.is_not(None)`, внутри `_price_ok`/`_weight_ok`.
NOT_FINITE_TABLE = (
    ("пусто", None, None),
    ("ноль", Decimal("0"), False),
    ("положительное целое", Decimal("100"), False),
    ("положительное дробное", Decimal("12.34"), False),
    ("отрицательное", Decimal("-100"), False),
    ("NaN", Decimal("NaN"), True),
    ("+Infinity", Decimal("Infinity"), True),
    ("-Infinity", Decimal("-Infinity"), True),
)


def _priced_item(
    factories,
    *,
    unit_cost_total,
    quantity=Decimal("1"),
    suggested_quantity=Decimal("1"),
):
    """Полная цепочка договор → смета → лот → предложение → позиция — та же
    форма, что `test_deviations_view._priced_item`: без неё строка не дойдёт
    до `v_position_deviation_inputs` (JOIN на `proposals/lots/estimates/
    contracts/catalog_positions`, миграция 0012)."""
    contract = factories.ContractFactory.create()
    estimate = factories.EstimateFactory.create(contract=contract)
    lot = factories.LotFactory.create(estimate=estimate)
    proposal = factories.ProposalFactory.create(lot=lot, contractor=contract.contractor)
    position = factories.CatalogPositionFactory.create()
    return factories.PositionItemFactory.create(
        proposal=proposal,
        catalog_position=position,
        unit_cost_total=unit_cost_total,
        quantity=quantity,
        suggested_quantity=suggested_quantity,
        total_cost_total=None,
    )


def _weight_item(factories, value):
    """Позиция с заданным весом (`unit_cost_total` фиксирована на пригодной,
    чтобы строка не выпала из VIEW): `COALESCE(suggested_quantity, quantity)`
    даёт `NULL`, только когда ОБА пусты — иначе `quantity` подстраховал бы
    своим значением, и пустой вес было бы не собрать."""
    if value is None:
        return _priced_item(
            factories, unit_cost_total=Decimal("1"), quantity=None, suggested_quantity=None
        )
    return _priced_item(
        factories, unit_cost_total=Decimal("1"), quantity=Decimal("1"), suggested_quantity=value
    )


def _assert_boolean(value, expected: bool, *, where: str) -> None:
    """`sql_ok` обязан быть булевым значением, а не `NULL` — сама эта проверка
    закрывает ловушку трёхзначности: наивная реализация без `column.is_not(None)`
    отдала бы здесь `None`, и `in (True, False)` упал бы по названной причине,
    а не молча согласился с любым результатом."""
    assert value in (True, False), f"{where}: ожидался булев ответ, получено {value!r}"
    assert value is expected, where


def _same_value(stored: Decimal | None, expected: Decimal | None) -> bool:
    """Сверка «доставки»: заданное значение реально доехало до колонки таким,
    каким задумано, а не подменилось `NULL`/другим числом по дороге
    (коагуляция типа, дефолт фабрики, потерянное присваивание). Без неё шесть
    входов из восьми ожидают `sql_ok is False` — ровно то же самое, что
    вернулось бы, доедь до колонки `NULL` вместо заданного значения; только
    `Decimal("100")` и `Decimal("12.34")` (оба `True`) доказывают доставку
    результатом предиката САМИ ПО СЕБЕ, без этой отдельной проверки.

    `Decimal("NaN") == Decimal("NaN")` в Python — `False` (родное поведение
    `decimal`: NaN не равен даже себе), и голое `==` здесь молча провалило бы
    сверку именно на входе `NaN`; `is_nan()` сверяет его своим способом.
    """
    if expected is None or stored is None:
        return stored is expected
    if expected.is_nan():
        return stored.is_nan()
    return stored == expected


class TestPriceOkOverPositionItem:
    """`_price_ok` применим к колонке `PositionItem` — площадке, куда VIEW не
    достаёт: пустая цена сегодня исключена из `v_position_deviation_inputs`
    целиком условием `WHERE pi.unit_cost_total IS NOT NULL` (миграция 0012), и
    предъявить пустой вход можно только здесь.
    """

    def test_matches_the_oracle_on_every_input(self, db_session, factories):
        by_id = {
            _priced_item(factories, unit_cost_total=value).id: (name, value, expected)
            for name, value, expected in TRUTH_TABLE
        }
        db_session.flush()

        rows = db_session.execute(
            sa.select(
                PositionItem.id,
                PositionItem.unit_cost_total,
                _price_ok(PositionItem.unit_cost_total).label("sql_ok"),
            ).where(PositionItem.id.in_(list(by_id)))
        ).all()

        assert len(rows) == len(TRUTH_TABLE)
        for position_id, stored, sql_ok in rows:
            name, value, expected = by_id[position_id]
            assert _same_value(stored, value), f"доставка входа «{name}»: сохранилось {stored!r}"
            _assert_boolean(sql_ok, expected, where=f"SQL, вход «{name}»")
            assert is_price(value) is expected, f"Python, вход «{name}»"

    def test_null_price_gives_false_not_null(self, db_session, factories):
        """Пустой вход отдельным тестом, а не только строкой таблицы: это сама
        ловушка трёхзначности, а не один из восьми равноправных случаев."""
        item = _priced_item(factories, unit_cost_total=None)
        db_session.flush()

        sql_ok = db_session.execute(
            sa.select(_price_ok(PositionItem.unit_cost_total)).where(PositionItem.id == item.id)
        ).scalar_one()

        _assert_boolean(sql_ok, False, where="пустая цена")
        assert is_price(None) is False


class TestPriceFilteredOutOfDeviationInputsView:
    """VIEW отклонений (`DEVIATION_INPUTS.c.unit_cost_total`) сам фильтрует по
    предикату цены — факт о VIEW, а не о `_price_ok`: класс переименован
    (был `TestPriceOkOverDeviationInputsView`) и внутри теста больше НЕТ ни
    одного обращения к `_price_ok`/`is_price` — старые имя и первая строка
    докстроки обещали бы применение, которого нет (находка ревью задачи 4).
    Согласие `_price_ok` с оракулом на этой же колонке доказывал он ДО
    миграции 0016 (см. историю ниже) — сегодня эту роль полностью несёт
    `TestPriceOkOverPositionItem` (колонку `PositionItem.unit_cost_total`
    VIEW не фильтрует вовсе).

    **Правка задачи 4 плана (миграция 0016), находка её собственного ревью.**
    До миграции 0016 VIEW отсеивал только `unit_cost_total IS NULL`, и все
    семь непустых входов оракула доезжали до него; тест ниже сверял
    `_price_ok` НАД ВЫЖИВШИМИ строками с оракулом — это было содержательной
    проверкой согласия. После 0016 сам текст VIEW несёт в `WHERE` тот же
    предикат «конечно и больше нуля», и строки, где он ложен, из VIEW пропадают
    ФИЗИЧЕСКИ — их там больше нет вовсе, а не есть, но с `sql_ok = False`.
    Прежняя форма («выбери строки по id, сверь длину со всеми непустыми входами
    оракула, затем сверь _price_ok над каждой») после этого стала бы
    тавтологией: `_price_ok` над строкой, которая по построению `WHERE` этот же
    предикат уже прошла, обязана быть `True` всегда — тест перестал бы стеречь
    что-либо, кроме факта, что VIEW вообще существует. И утверждение о длине
    («ровно len(non_null_rows) строк») стало бы попросту ложным: набор упал бы
    с 7 до 2. Это ОЖИДАЕМО и является прямым следствием фичи, а не поводом
    подогнать число под вывод (задача 4, разбор её брифа) — смысл проверки
    меняется с «предикат согласен с оракулом» на «VIEW фильтрует по предикату»,
    и это разные факты: первый уже полностью доказан классом
    `TestPriceOkOverPositionItem` выше (там VIEW ничего не фильтрует, видны все
    восемь входов, включая пустой) — второй проверяется здесь.
    """

    def test_view_keeps_exactly_the_priced_positions(self, db_session, factories):
        """Множество позиций, переживших VIEW, равно множеству входов, где
        `is_price` истинна, — не шире (негодная цена не просочилась) и не уже
        (пригодная цена не потерялась). Сверка по ИМЕНАМ входов, а не только
        по числу: подмена состава при том же количестве строк осталась бы
        незамеченной сверкой одной длины."""
        non_null_rows = tuple(row for row in TRUTH_TABLE if row[1] is not None)
        by_id = {
            _priced_item(factories, unit_cost_total=value).id: (name, value, expected)
            for name, value, expected in non_null_rows
        }
        db_session.flush()

        surviving_ids = set(
            db_session.execute(
                sa.select(DEVIATION_INPUTS.c.position_item_id).where(
                    DEVIATION_INPUTS.c.position_item_id.in_(list(by_id))
                )
            ).scalars()
        )

        name_by_id = {pid: name for pid, (name, _value, _expected) in by_id.items()}
        expected_names = {name for _pid, (name, _value, expected) in by_id.items() if expected}
        surviving_names = {name_by_id[pid] for pid in surviving_ids}

        assert surviving_names == expected_names, (
            "VIEW обязан отдавать ровно позиции с конечной ценой больше нуля, "
            f"выжили: {sorted(surviving_names)}, ожидались: {sorted(expected_names)}"
        )


class TestWeightOkOverDeviationInputsView:
    """`_weight_ok` — вторая половина правила (спека §2.1). Вес, в отличие от
    цены, VIEW-ом не фильтруется вовсе (условие миграции 0012 — только на
    `unit_cost_total`), поэтому здесь достижимы ВСЕ входы оракула сразу,
    включая пустой — без обходного пути через `PositionItem`, который
    потребовался цене. Своей колонки «вес» у `PositionItem` нет: её собирает
    VIEW, а вторая площадка (`_cell_groups_cte`/`_cell_weights_cte`) появилась
    задачей 2 плана.
    """

    def test_matches_the_oracle_on_every_input(self, db_session, factories):
        by_id = {
            _weight_item(factories, value).id: (name, value, expected)
            for name, value, expected in TRUTH_TABLE
        }
        db_session.flush()

        rows = db_session.execute(
            sa.select(
                DEVIATION_INPUTS.c.position_item_id,
                DEVIATION_INPUTS.c.weight,
                _weight_ok(DEVIATION_INPUTS.c.weight).label("sql_ok"),
            ).where(DEVIATION_INPUTS.c.position_item_id.in_(list(by_id)))
        ).all()

        assert len(rows) == len(TRUTH_TABLE)
        for position_id, stored, sql_ok in rows:
            name, value, expected = by_id[position_id]
            assert _same_value(stored, value), f"доставка входа «{name}»: сохранилось {stored!r}"
            _assert_boolean(sql_ok, expected, where=f"SQL, вход «{name}»")
            assert is_weight(value) is expected, f"Python, вход «{name}»"


class TestNotFiniteOverDeviationInputsView:
    """`_not_finite` предъявлен САМ, отдельной выбираемой булевой колонкой, а
    не только опосредованно через `_price_ok`/`_weight_ok` (ревью задачи 1,
    находка «инертный член»): член `-Infinity` в перечислении `_not_finite` не
    роняет ни `_price_ok`, ни `_weight_ok`, потому что `'-Infinity'::numeric
    > 0` в PostgreSQL и так ложь — временное снятие члена оставляет
    `TRUTH_TABLE`-тесты выше зелёными и красит в красный только тест этого
    класса, ровно на строке «-Infinity». Заявление предиката шире одного
    знака: он о ВСЕХ трёх нефинитных значениях, и это обязано иметь
    собственный, а не заёмный вход.
    """

    def test_matches_the_oracle_on_every_input(self, db_session, factories):
        by_id = {
            _weight_item(factories, value).id: (name, value, expected)
            for name, value, expected in NOT_FINITE_TABLE
        }
        db_session.flush()

        rows = db_session.execute(
            sa.select(
                DEVIATION_INPUTS.c.position_item_id,
                DEVIATION_INPUTS.c.weight,
                _not_finite(DEVIATION_INPUTS.c.weight).label("sql_not_finite"),
            ).where(DEVIATION_INPUTS.c.position_item_id.in_(list(by_id)))
        ).all()

        assert len(rows) == len(NOT_FINITE_TABLE)
        for position_id, stored, sql_not_finite in rows:
            name, value, expected = by_id[position_id]
            assert _same_value(stored, value), f"доставка входа «{name}»: сохранилось {stored!r}"
            assert sql_not_finite == expected, (
                f"вход «{name}»: ожидался {expected!r}, получено {sql_not_finite!r}"
            )


class TestNaivePredicateIsNotEnough:
    """Вход, отличающий предикат от наивного «больше нуля» (спека §1.2) — на
    ОДНОЙ и той же хранимой колонке (`PositionItem.unit_cost_total`), не на
    синтетическом литерале: докстрока `_price_ok` заявляет истинность наивной
    формы на `NaN` И на `+Infinity` — оба предъявлены, не один.
    """

    @pytest.mark.parametrize(
        ("name", "value"), [("NaN", Decimal("NaN")), ("+Infinity", Decimal("Infinity"))]
    )
    def test_naive_comparison_disagrees_with_price_ok_on_stored_column(
        self, db_session, factories, name, value
    ):
        item = _priced_item(factories, unit_cost_total=value)
        db_session.flush()

        naive, robust = db_session.execute(
            sa.select(
                (PositionItem.unit_cost_total > 0).label("naive"),
                _price_ok(PositionItem.unit_cost_total).label("robust"),
            ).where(PositionItem.id == item.id)
        ).one()

        assert naive is True, f"вход «{name}»: наивная форма `column > 0` обязана быть True"
        assert robust is False, f"вход «{name}»: _price_ok обязана быть False"

    def test_bare_python_comparison_raises_on_nan(self):
        """Вторая половина расхождения площадок: там, где SQL молча даёт
        `True`, голый Python бросает исключение вместо ответа."""
        with pytest.raises(InvalidOperation):
            _ = Decimal("NaN") > 0
