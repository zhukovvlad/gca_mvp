"""Фасет `available_rate_classes` и снимок `rate_class_id` в колонках (план
поправки на диаграмму сравнения, задача 4).

Фасет строится по выборке ДО сужения по классу ставки, но ПОСЛЕ остальных
фильтров (`q`, `object_id`, `contractor_id`) — иначе снятый чип класса не
вернуть: чипы обязаны видеть весь набор классов отфильтрованной выборки, а не
только тот, до которого её сузили (спека §4.4, DoD 8).

`baseline_selection` из `comparison_fixtures` здесь не годится: там у каждого
договора СВОЙ класс, и `count` всегда 1 — правило «count — число договоров
класса» им не проверить. Вместо неё — `contracts_sharing_a_rate_class`,
заведённая этой же задачей.
"""
from __future__ import annotations

import datetime as dt

import pytest

from crud import comparison as cmp
from tests import comparison_fixtures as fx

pytestmark = pytest.mark.integration


def test_facet_present_without_narrowing(db_session, factories):
    """Фасет присутствует в ответе БЕЗ какого-либо сужения (DoD 7)."""
    contract_ids, shared_id, other_id = fx.contracts_sharing_a_rate_class(db_session, factories)

    selection = cmp.resolve_selection(db_session, ids=contract_ids)
    agg = cmp.build_comparison(
        db_session, selection.contract_ids, facet_ids=selection.facet_ids, vat_mode="net"
    )

    assert "available_rate_classes" in agg
    ids_in_facet = {entry["id"] for entry in agg["available_rate_classes"]}
    assert ids_in_facet == {shared_id, other_id}


def test_facet_defaults_to_contract_ids_when_omitted(db_session, factories):
    """`build_comparison` без `facet_ids` считает фасет по самой выборке (§4.3)."""
    contract_ids, shared_id, other_id = fx.contracts_sharing_a_rate_class(db_session, factories)
    only_shared_class = contract_ids[:2]

    agg = cmp.build_comparison(db_session, only_shared_class, vat_mode="net")

    assert [entry["id"] for entry in agg["available_rate_classes"]] == [shared_id]
    assert agg["available_rate_classes"][0]["count"] == 2


def test_facet_carries_all_classes_when_narrowed_to_one(db_session, factories):
    """DoD 8: сужение до ОДНОГО класса не отбирает у фасета остальные.

    Ключевой тест фичи: без него чип снятого класса пропадает из ответа, и
    сужение становится необратимым — снять чип уже нечем.
    """
    contract_ids, shared_id, other_id = fx.contracts_sharing_a_rate_class(db_session, factories)

    selection = cmp.resolve_selection(db_session, ids=contract_ids, rate_class_id=shared_id)
    # Предпосылка: сужение действительно отсекло договор другого класса.
    assert set(selection.contract_ids) == set(contract_ids[:2])

    agg = cmp.build_comparison(
        db_session, selection.contract_ids, facet_ids=selection.facet_ids, vat_mode="net"
    )

    ids_in_facet = {entry["id"] for entry in agg["available_rate_classes"]}
    assert ids_in_facet == {shared_id, other_id}
    # А колонки при этом ДЕЙСТВИТЕЛЬНО сужены — фасет расширился, выборка нет.
    assert {column["contract_id"] for column in agg["columns"]} == set(contract_ids[:2])

    # `count` отсечённого класса — НЕНУЛЕВОЙ. Утверждения о множестве id для этого
    # недостаточно: начни `count` считаться по суженной выборке, id остался бы в
    # фасете с нулём, тест был бы зелёным, а чип нарисовался бы как «(0)» —
    # предложение снять фильтр, за которым нет ни одного договора.
    excluded = next(e for e in agg["available_rate_classes"] if e["id"] == other_id)
    assert excluded["count"] == 1


def test_facet_computed_after_other_filters(db_session, factories):
    """Фасет считается ПОСЛЕ `q`/`object_id`/`contractor_id` (DoD 9).

    При `all=1&contractor_id=N` в фасете обязаны быть только классы договоров
    ЭТОГО подрядчика, а не всех подрядчиков в базе.
    """
    contractor_x = factories.ContractorFactory.create()
    contractor_y = factories.ContractorFactory.create()
    class_a = factories.RateClassFactory.create(title="Класс А (фасет-фильтр)")
    class_b = factories.RateClassFactory.create(title="Класс Б (фасет-фильтр)")

    def make(contractor, rate_class, number):
        contract = factories.ContractFactory.create(
            contractor=contractor, rate_class=rate_class, contract_number=number,
        )
        estimate = factories.EstimateFactory.create(contract=contract)
        proposal = fx.make_proposal(factories, estimate=estimate)
        fx.seed_chapter_with_positions(
            db_session, factories, proposal=proposal, code="1", amounts=["100.00"]
        )
        return contract.id

    make(contractor_x, class_a, "ГП-ФФ-1")
    make(contractor_y, class_b, "ГП-ФФ-2")
    db_session.flush()

    selection = cmp.resolve_selection(db_session, use_filter=True, contractor_id=contractor_x.id)
    agg = cmp.build_comparison(
        db_session, selection.contract_ids, facet_ids=selection.facet_ids, vat_mode="net"
    )

    assert [entry["id"] for entry in agg["available_rate_classes"]] == [class_a.id]


def test_facet_count_matches_columns_of_that_class_when_narrowed_to_it(db_session, factories):
    """DoD 10: `count` при сужении РОВНО до класса равен числу его колонок."""
    contract_ids, shared_id, other_id = fx.contracts_sharing_a_rate_class(db_session, factories)

    selection = cmp.resolve_selection(db_session, use_filter=True, rate_class_id=shared_id)
    agg = cmp.build_comparison(
        db_session, selection.contract_ids, facet_ids=selection.facet_ids, vat_mode="net"
    )

    shared_entry = next(
        entry for entry in agg["available_rate_classes"] if entry["id"] == shared_id
    )
    assert shared_entry["count"] == 2
    assert len(agg["columns"]) == 2


def test_facet_ordered_by_title_stable_and_independent_of_columns(db_session, factories):
    """DoD 11: порядок фасета — по `title`, не по порядку создания и не по
    порядку колонок (`signed_date`), и он стабилен между запросами.

    Три класса заведены так, чтобы все три порядка — по алфавиту, по созданию,
    по `signed_date` — расходились между собой: иначе тест прошёл бы на
    совпадении, ничего не проверив (см. брифы задачи, §4.7 п.6).
    """
    # Порядок создания: В, А, Б — отличается от алфавита (А, Б, В).
    class_v = factories.RateClassFactory.create(title="В-класс (порядок)")
    class_a = factories.RateClassFactory.create(title="А-класс (порядок)")
    class_b = factories.RateClassFactory.create(title="Б-класс (порядок)")

    def make(rate_class, signed_date, number):
        contract = factories.ContractFactory.create(
            rate_class=rate_class, signed_date=signed_date, contract_number=number,
        )
        estimate = factories.EstimateFactory.create(contract=contract)
        proposal = fx.make_proposal(factories, estimate=estimate)
        fx.seed_chapter_with_positions(
            db_session, factories, proposal=proposal, code="1", amounts=["100.00"]
        )
        return contract.id

    # signed_date DESC даёт порядок колонок В, Б, А — тоже отличный от алфавита.
    id_v = make(class_v, dt.date(2025, 3, 1), "ГП-ФП-В")
    id_b = make(class_b, dt.date(2025, 2, 1), "ГП-ФП-Б")
    id_a = make(class_a, dt.date(2025, 1, 1), "ГП-ФП-А")
    db_session.flush()

    agg = cmp.build_comparison(db_session, [id_v, id_b, id_a], vat_mode="net")

    assert [column["contract_id"] for column in agg["columns"]] == [id_v, id_b, id_a]
    titles = [entry["title"] for entry in agg["available_rate_classes"]]
    assert titles == ["А-класс (порядок)", "Б-класс (порядок)", "В-класс (порядок)"]

    # Стабильность между запросами: повторный вызов даёт тот же порядок.
    agg2 = cmp.build_comparison(db_session, [id_v, id_b, id_a], vat_mode="net")
    assert [entry["title"] for entry in agg2["available_rate_classes"]] == titles


def test_rate_class_id_column_is_a_snapshot_from_contract(db_session, factories):
    """DoD 12: `rate_class_id` колонки — снимок из договора, а не текущий
    класс объекта.

    Проверено переклассификацией: класс объекта меняется ПОСЛЕ создания
    договора, а колонка обязана остаться в прежнем классе — том, в котором
    договор подписывали.
    """
    class_at_signing = factories.RateClassFactory.create(title="Класс на подписании")
    class_after_reclass = factories.RateClassFactory.create(title="Класс после переклассификации")

    obj = factories.ObjectFactory.create(rate_class_id=class_at_signing.id)
    contract = factories.ContractFactory.create(object=obj, rate_class=class_at_signing)
    estimate = factories.EstimateFactory.create(contract=contract)
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(
        db_session, factories, proposal=proposal, code="1", amounts=["100.00"]
    )
    db_session.flush()

    # Переклассификация ОБЪЕКТА — договор при этом не трогаем.
    obj.rate_class_id = class_after_reclass.id
    db_session.flush()

    agg = cmp.build_comparison(db_session, [contract.id], vat_mode="net")

    assert agg["columns"][0]["rate_class_id"] == class_at_signing.id


# ---------------------------------------------------------------------------
#  Дырки в покрытии, найденные независимым ревью задачи 4
# ---------------------------------------------------------------------------

def test_facet_ids_not_covering_selection_is_a_programming_error(db_session, factories):
    """Надмножество `facet_ids` держит КОД, а не соглашение.

    Заведено ревью: защита была в коде и отсутствовала в CI. Её можно было
    удалить, оставив `just ci` зелёным, — то есть защита, поставленная ровно
    против МОЛЧАЛИВОГО дефекта, ушла бы сама молча (AGENTS.md §12).

    Чем именно молчаливого: не покрой `facet_ids` выборку, договор выпал бы из
    `columns_meta`, а с ним из `ordered_ids`, — но роллапы для него всё равно
    прочитались бы, и ответ показал бы выборку МЕНЬШЕ запрошенной, ничего об этом
    не сказав. Структурно такой ответ правильный: не хватает колонки, которую
    никто не пересчитывает.

    На обоих HTTP-путях условие недостижимо (`resolve_selection` строит
    `facet_ids` надмножеством по построению), поэтому единственный его смысл —
    сторожить прямые вызовы, а без теста этот смысл не живёт.
    """
    contract_ids, _shared_id, _other_id = fx.contracts_sharing_a_rate_class(db_session, factories)

    with pytest.raises(RuntimeError):
        cmp.build_comparison(db_session, contract_ids, facet_ids=contract_ids[:1], vat_mode="net")

    # Обратный случай ОБЯЗАТЕЛЕН: без него тест зелёный и при безусловном
    # `raise RuntimeError` — то есть не отличал бы защиту от поломки.
    agg = cmp.build_comparison(
        db_session, contract_ids[:1], facet_ids=contract_ids, vat_mode="net"
    )
    assert [column["contract_id"] for column in agg["columns"]] == contract_ids[:1]


def test_empty_selection_still_carries_an_empty_facet(db_session):
    """DoD 7 «ВСЕГДА» — в том единственном случае, где «всегда» неочевидно.

    `_load_columns` имеет ранний `return []` на пустом входе; ровно из такой формы
    однажды вырастает ранний выход и из `build_comparison`, а с ним пропадёт ключ.
    """
    agg = cmp.build_comparison(db_session, [], vat_mode="net")

    assert "available_rate_classes" in agg
    assert agg["available_rate_classes"] == []


def test_narrowing_to_nothing_keeps_the_facet_of_the_wider_selection(db_session, factories):
    """Сужение, выбравшее НОЛЬ договоров, фасет не обнуляет.

    Это предельный случай обратимости, и он самый важный: именно здесь человек
    обязан иметь возможность снять чип. Пустые колонки при непустом фасете —
    законное состояние экрана, а не ошибка.
    """
    contract_ids, shared_id, other_id = fx.contracts_sharing_a_rate_class(db_session, factories)

    agg = cmp.build_comparison(db_session, [], facet_ids=contract_ids, vat_mode="net")

    assert agg["columns"] == []
    # Сверяется отображение id → count, а не позиции в списке: порядок фасета —
    # по `title`, и он к этому тесту отношения не имеет (его сторожит
    # `test_facet_ordered_by_title_stable_and_independent_of_columns`). Первая
    # редакция этого утверждения ждала `[2, 1]` и упала на `[1, 2]`, потому что
    # «Класс другой» алфавитно раньше «Класс общий», — ошибка была в ожидании.
    assert {e["id"]: e["count"] for e in agg["available_rate_classes"]} == {
        shared_id: 2, other_id: 1,
    }
