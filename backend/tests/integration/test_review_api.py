"""Эндпоинты ручного матчинга (фаза 5, §7.2) поверх готового `services/review.py`.

Сериализация решений и инварианты кэша проверены фазой 4
(`test_review_concurrency.py`) — здесь HTTP-слой: выборки экрана, коды ответов,
пакетное действие и права.

Что именно проверяется по решениям фазы 5:

* §6.3 — осиротевшая TO_REVIEW-строка в очередь не попадает;
* §6.4 — поиск цели находит по лемме, а не только по подстроке;
* §6.5 — серверная пагинация, сортировка по весу, пакетная разметка;
* §3 — `member` вправе разбирать очередь.
"""
from __future__ import annotations

import typing

import pytest
import sqlalchemy as sa

from models import (
    CatalogKind,
    CatalogPosition,
    MatchingCache,
    MatchSource,
    PositionItem,
    UnitOfMeasure,
    UserRole,
)
from parser.sanitize_text import normalize_job_title_with_lemmatization
from routers.review import ManualKind
from services.review import MANUAL_KINDS

pytestmark = pytest.mark.integration


@pytest.fixture
def member(client):
    client.auth_state["role"] = UserRole.member
    yield client
    client.auth_state["role"] = UserRole.admin


def _catalog(factories, title: str, *, kind: str, unit_id: int | None = None) -> CatalogPosition:
    """Каталожная строка с НАСТОЯЩЕЙ нормализацией.

    Заглушка из фабрики (`.strip().lower()`) для поиска по леммам не годится:
    именно расхождение словоформы и леммы этот поиск и должен переживать.
    """
    return factories.CatalogPositionFactory.create(
        standard_job_title=title,
        normalized_job_title=normalize_job_title_with_lemmatization(title),
        kind=kind,
        unit_id=unit_id,
    )


def _with_positions(factories, catalog: CatalogPosition, titles: list[str]) -> None:
    """Позиции сметы, ссылающиеся на каталожную строку."""
    proposal = factories.ProposalFactory.create()
    for title in titles:
        factories.PositionItemFactory.create(
            proposal=proposal, catalog_position=catalog, job_title_in_proposal=title
        )


def _unit_id(db_session, code: str) -> int:
    return db_session.query(UnitOfMeasure).filter_by(code=code).one().id


# ---------------------------------------------------------------------------
#  Очередь
# ---------------------------------------------------------------------------

def test_queue_returns_counts_and_sample_titles(client, factories):
    row = _catalog(factories, "Неведомая работа", kind=CatalogKind.TO_REVIEW.value)
    _with_positions(factories, row, ["Стяжка пола 50мм", "Стяжка пола 50 мм", "Стяжка пола 50мм"])

    items = client.get("/api/v1/review/queue").json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == row.id
    assert items[0]["position_count"] == 3
    # Примеры — различные наименования, не три копии одного.
    assert sorted(items[0]["sample_titles"]) == ["Стяжка пола 50 мм", "Стяжка пола 50мм"]


def test_queue_hides_orphaned_rows_without_references(client, factories):
    """Решение §6.3: долг фазы 4 — строка, осиротевшая после replace.

    Решать по ней нечего: она не описывает работу ни в одной смете. Строка при
    этом остаётся в каталоге и держит свою нормализованную пару — следующий
    импорт той же работы переиспользует её штатным get-or-create.
    """
    orphan = _catalog(factories, "Осиротевшая работа", kind=CatalogKind.TO_REVIEW.value)
    referenced = _catalog(factories, "Живая работа", kind=CatalogKind.TO_REVIEW.value)
    _with_positions(factories, referenced, ["Живая позиция"])

    items = client.get("/api/v1/review/queue").json()["items"]
    assert [item["id"] for item in items] == [referenced.id]

    # Строка не удалена — она по-прежнему в каталоге.
    assert client.get("/api/v1/review/queue").json()["total"] == 1
    assert orphan.id is not None


def test_queue_ignores_rows_of_other_kinds(client, factories):
    for kind in (CatalogKind.POSITION, CatalogKind.HEADER, CatalogKind.TRASH):
        row = _catalog(factories, f"Строка {kind.value}", kind=kind.value)
        _with_positions(factories, row, ["Позиция"])

    assert client.get("/api/v1/review/queue").json()["total"] == 0


def test_queue_sorts_heaviest_first_by_default(client, factories):
    """§6.5: сначала работы с наибольшим числом ссылок — их разбор даёт
    наибольший прирост метрики §10."""
    light = _catalog(factories, "Работа лёгкая", kind=CatalogKind.TO_REVIEW.value)
    heavy = _catalog(factories, "Работа тяжёлая", kind=CatalogKind.TO_REVIEW.value)
    _with_positions(factories, light, ["одна"])
    _with_positions(factories, heavy, ["одна", "две", "три"])

    items = client.get("/api/v1/review/queue").json()["items"]
    assert [item["id"] for item in items] == [heavy.id, light.id]
    assert [item["position_count"] for item in items] == [3, 1]


def test_queue_can_sort_by_title(client, factories):
    second = _catalog(factories, "Ясная работа", kind=CatalogKind.TO_REVIEW.value)
    first = _catalog(factories, "Ажурная работа", kind=CatalogKind.TO_REVIEW.value)
    _with_positions(factories, second, ["одна"])
    _with_positions(factories, first, ["одна"])

    items = client.get("/api/v1/review/queue", params={"sort": "title"}).json()["items"]
    assert [item["id"] for item in items] == [first.id, second.id]


def test_queue_unknown_sort_gives_422(client):
    response = client.get("/api/v1/review/queue", params={"sort": "по-настроению"})
    assert response.status_code == 422
    assert "Неизвестная сортировка" in response.json()["detail"]


def test_queue_filters_by_title_substring(client, factories):
    target = _catalog(factories, "Кладка кирпичная", kind=CatalogKind.TO_REVIEW.value)
    other = _catalog(factories, "Стяжка пола", kind=CatalogKind.TO_REVIEW.value)
    _with_positions(factories, target, ["одна"])
    _with_positions(factories, other, ["одна"])

    found = client.get("/api/v1/review/queue", params={"q": "кладка"}).json()
    assert [item["id"] for item in found["items"]] == [target.id]


def test_queue_filters_by_unit_and_by_absence_of_unit(client, factories, db_session):
    m2 = _unit_id(db_session, "M2")
    with_unit = _catalog(factories, "Работа в метрах", kind=CatalogKind.TO_REVIEW.value, unit_id=m2)
    without_unit = _catalog(factories, "Работа без единицы", kind=CatalogKind.TO_REVIEW.value)
    _with_positions(factories, with_unit, ["одна"])
    _with_positions(factories, without_unit, ["одна"])

    by_unit = client.get("/api/v1/review/queue", params={"unit_id": m2}).json()
    assert [item["id"] for item in by_unit["items"]] == [with_unit.id]
    assert by_unit["items"][0]["unit_code"] == "M2"

    no_unit = client.get("/api/v1/review/queue", params={"without_unit": True}).json()
    assert [item["id"] for item in no_unit["items"]] == [without_unit.id]


def test_queue_total_counts_all_rows_not_just_the_page(client, factories):
    for n in range(5):
        row = _catalog(factories, f"Работа {n}", kind=CatalogKind.TO_REVIEW.value)
        _with_positions(factories, row, ["одна"])

    page = client.get("/api/v1/review/queue", params={"page": 1, "page_size": 2}).json()
    assert page["total"] == 5
    assert len(page["items"]) == 2


# ---------------------------------------------------------------------------
#  Поиск цели слияния (§6.4)
# ---------------------------------------------------------------------------

def test_targets_found_by_substring_of_human_title(client, factories):
    target = _catalog(factories, "Монтаж кабелей силовых", kind=CatalogKind.POSITION.value)
    found = client.get("/api/v1/review/targets", params={"q": "кабел"}).json()
    assert [item["id"] for item in found] == [target.id]


def test_targets_found_by_lemma_of_a_different_word_form(client, factories):
    """Ровно то, чего не умеет FTS в текущей обвязке (замер — §1.4 отчёта).

    Каталог хранит «кирпичная», оператор печатает «кирпичной»: подстрока не
    совпадает, а леммы совпадают — `normalized_job_title` уже лемматизирован той
    же функцией, которой лемматизируется запрос.

    Пара словоформ выбрана замером, а не на слух: та же модель, например, НЕ
    сводит «кабеля» к «кабель» (хотя «кабелей» сводит), и тест на ней доказывал
    бы обратное тому, что заявлено.
    """
    query = "кирпичной"
    target = _catalog(factories, "Кладка кирпичная", kind=CatalogKind.POSITION.value)

    # Две предпосылки, без которых тест ничего не значит: подстрокой не найти,
    # а леммы совпадают.
    assert query not in target.standard_job_title.lower()
    assert normalize_job_title_with_lemmatization(query) in target.normalized_job_title

    found = client.get("/api/v1/review/targets", params={"q": query}).json()
    assert [item["id"] for item in found] == [target.id]


def test_targets_exclude_non_position_rows(client, factories):
    _catalog(factories, "Кладка на проверке", kind=CatalogKind.TO_REVIEW.value)
    _catalog(factories, "Кладка заголовок", kind=CatalogKind.HEADER.value)
    _catalog(factories, "Кладка мусор", kind=CatalogKind.TRASH.value)
    good = _catalog(factories, "Кладка кирпичная", kind=CatalogKind.POSITION.value)

    found = client.get("/api/v1/review/targets", params={"q": "кладка"}).json()
    assert [item["id"] for item in found] == [good.id]


def test_targets_put_same_unit_first_but_do_not_hide_others(client, factories, db_session):
    """Единица — подсказка, а не фильтр: слить в другую единицу оператор вправе."""
    m2 = _unit_id(db_session, "M2")
    m3 = _unit_id(db_session, "M3")
    other_unit = _catalog(factories, "Кладка А", kind=CatalogKind.POSITION.value, unit_id=m3)
    same_unit = _catalog(factories, "Кладка Я", kind=CatalogKind.POSITION.value, unit_id=m2)

    found = client.get("/api/v1/review/targets", params={"q": "кладка", "unit_id": m2}).json()
    assert [item["id"] for item in found] == [same_unit.id, other_unit.id]


def test_targets_prefer_prefix_match_then_shorter_title(client, factories):
    prefix = _catalog(factories, "Кладка кирпичная", kind=CatalogKind.POSITION.value)
    inner = _catalog(factories, "Ремонтная кладка кирпичная стен", kind=CatalogKind.POSITION.value)

    found = client.get("/api/v1/review/targets", params={"q": "Кладка"}).json()
    assert [item["id"] for item in found] == [prefix.id, inner.id]


def test_targets_require_non_empty_query(client):
    assert client.get("/api/v1/review/targets", params={"q": ""}).status_code == 422


# ---------------------------------------------------------------------------
#  Слияние
# ---------------------------------------------------------------------------

def test_merge_moves_all_positions_deletes_row_and_writes_manual_cache(
    client, factories, db_session
):
    """§5: решение применяется ко ВСЕМ позициям, строка удаляется, кэш вечный."""
    source = _catalog(factories, "Стяжка неведомая", kind=CatalogKind.TO_REVIEW.value)
    target = _catalog(factories, "Стяжка цементная", kind=CatalogKind.POSITION.value)
    _with_positions(factories, source, ["одна", "две", "три"])

    response = client.post(f"/api/v1/review/{source.id}/merge", json={"target_id": target.id})
    assert response.status_code == 200
    assert response.json()["moved_positions"] == 3
    assert response.json()["target"]["id"] == target.id

    moved = db_session.execute(
        sa.select(sa.func.count()).select_from(PositionItem).where(
            PositionItem.catalog_position_id == target.id
        )
    ).scalar_one()
    assert moved == 3
    assert db_session.get(CatalogPosition, source.id) is None

    cache = db_session.execute(
        sa.select(MatchingCache).where(MatchingCache.catalog_position_id == target.id)
    ).scalar_one()
    assert cache.source == MatchSource.manual.value
    assert cache.expires_at is None


def test_merge_leaves_no_cache_pointing_at_to_review(client, factories, db_session):
    """Инвариант §5 — условие безопасности DELETE при слиянии."""
    source = _catalog(factories, "Работа неведомая", kind=CatalogKind.TO_REVIEW.value)
    target = _catalog(factories, "Работа известная", kind=CatalogKind.POSITION.value)
    _with_positions(factories, source, ["одна"])

    client.post(f"/api/v1/review/{source.id}/merge", json={"target_id": target.id})

    dangling = db_session.execute(
        sa.select(sa.func.count())
        .select_from(MatchingCache)
        .join(CatalogPosition, CatalogPosition.id == MatchingCache.catalog_position_id)
        .where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
    ).scalar_one()
    assert dangling == 0


def test_merge_with_itself_gives_422(client, factories):
    row = _catalog(factories, "Сама с собой", kind=CatalogKind.TO_REVIEW.value)
    response = client.post(f"/api/v1/review/{row.id}/merge", json={"target_id": row.id})
    assert response.status_code == 422
    assert "с собой" in response.json()["detail"]


def test_merge_into_non_position_target_gives_409(client, factories):
    source = _catalog(factories, "Источник", kind=CatalogKind.TO_REVIEW.value)
    target = _catalog(factories, "Заголовок", kind=CatalogKind.HEADER.value)
    _with_positions(factories, source, ["одна"])

    response = client.post(f"/api/v1/review/{source.id}/merge", json={"target_id": target.id})
    assert response.status_code == 409
    assert "kind=HEADER" in response.json()["detail"]


def test_merge_of_already_resolved_row_gives_409(client, factories):
    """Строку уже разобрал другой оператор — конфликт состояния, не 404."""
    source = _catalog(factories, "Уже утверждена", kind=CatalogKind.POSITION.value)
    target = _catalog(factories, "Цель", kind=CatalogKind.POSITION.value)

    response = client.post(f"/api/v1/review/{source.id}/merge", json={"target_id": target.id})
    assert response.status_code == 409


def test_merge_of_missing_row_gives_404(client, factories):
    target = _catalog(factories, "Цель", kind=CatalogKind.POSITION.value)
    response = client.post("/api/v1/review/999999/merge", json={"target_id": target.id})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
#  Разметка kind
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", MANUAL_KINDS)
def test_set_kind_changes_row_without_deleting_it(client, factories, db_session, kind):
    row = _catalog(factories, f"Строка для {kind}", kind=CatalogKind.TO_REVIEW.value)
    _with_positions(factories, row, ["одна"])

    response = client.post(f"/api/v1/review/{row.id}/kind", json={"kind": kind})
    assert response.status_code == 200
    assert response.json()["kind"] == kind

    db_session.expire_all()
    assert db_session.get(CatalogPosition, row.id).kind == kind

    cache = db_session.execute(
        sa.select(MatchingCache).where(MatchingCache.catalog_position_id == row.id)
    ).scalar_one()
    assert cache.source == MatchSource.manual.value
    assert cache.expires_at is None


def test_set_kind_rejects_lot_header(client, factories):
    """`LOT_HEADER` — разметка структуры файла, оператору она не принадлежит (§5)."""
    row = _catalog(factories, "Строка", kind=CatalogKind.TO_REVIEW.value)
    response = client.post(f"/api/v1/review/{row.id}/kind", json={"kind": "LOT_HEADER"})
    assert response.status_code == 422


def test_set_kind_on_already_resolved_row_gives_409(client, factories):
    row = _catalog(factories, "Уже мусор", kind=CatalogKind.TRASH.value)
    response = client.post(f"/api/v1/review/{row.id}/kind", json={"kind": "TRASH"})
    assert response.status_code == 409


def test_set_kind_of_missing_row_gives_404(client):
    assert client.post("/api/v1/review/999999/kind", json={"kind": "TRASH"}).status_code == 404


def test_manual_kind_literal_matches_service(client):
    """Литерал роутера и `MANUAL_KINDS` сервиса обязаны совпадать.

    Литерал нужен Pydantic, чтобы отдать 422 до вызова сервиса, — то есть
    дублирование неизбежно. Расхождение ловится здесь, а не на экране.
    """
    assert typing.get_args(ManualKind) == MANUAL_KINDS


# ---------------------------------------------------------------------------
#  Пакетная разметка (§6.5)
# ---------------------------------------------------------------------------

def test_batch_kind_applies_to_every_row(client, factories, db_session):
    rows = []
    for n in range(5):
        row = _catalog(factories, f"Мусорная строка {n}", kind=CatalogKind.TO_REVIEW.value)
        _with_positions(factories, row, ["одна"])
        rows.append(row)

    response = client.post(
        "/api/v1/review/batch-kind", json={"ids": [r.id for r in rows], "kind": "TRASH"}
    )
    assert response.status_code == 200
    body = response.json()
    assert sorted(body["applied"]) == sorted(r.id for r in rows)
    assert body["skipped"] == []

    db_session.expire_all()
    assert all(
        db_session.get(CatalogPosition, r.id).kind == CatalogKind.TRASH.value for r in rows
    )


def test_batch_kind_skips_unapplicable_rows_without_failing_the_batch(
    client, factories, db_session
):
    """Строку уже разобрали — пакет не падает, а объясняет по строке."""
    good = _catalog(factories, "Ещё в очереди", kind=CatalogKind.TO_REVIEW.value)
    already = _catalog(factories, "Уже утверждена", kind=CatalogKind.POSITION.value)
    _with_positions(factories, good, ["одна"])

    body = client.post(
        "/api/v1/review/batch-kind",
        json={"ids": [good.id, already.id, 999_999], "kind": "TRASH"},
    ).json()

    assert body["applied"] == [good.id]
    skipped = {item["id"]: item["reason"] for item in body["skipped"]}
    assert set(skipped) == {already.id, 999_999}
    assert "kind=POSITION" in skipped[already.id]
    assert "не найдена" in skipped[999_999]

    # Пропуск одной строки не откатил применённое к другой.
    db_session.expire_all()
    assert db_session.get(CatalogPosition, good.id).kind == CatalogKind.TRASH.value


def test_batch_kind_processes_ids_in_ascending_order(client, factories):
    """Единый порядок захвата блокировок — против взаимной блокировки пакетов.

    Тест фиксирует именно порядок обработки (он наблюдаем в `applied`), а не
    отсутствие взаимной блокировки: доказать её отсутствие тестом нельзя, а
    сорванный порядок — можно.
    """
    rows = []
    for n in range(4):
        row = _catalog(factories, f"Строка {n}", kind=CatalogKind.TO_REVIEW.value)
        _with_positions(factories, row, ["одна"])
        rows.append(row)

    ids = [r.id for r in rows]
    shuffled = [ids[2], ids[0], ids[3], ids[1]]
    body = client.post(
        "/api/v1/review/batch-kind", json={"ids": shuffled, "kind": "TRASH"}
    ).json()
    assert body["applied"] == sorted(ids)


def test_batch_kind_deduplicates_repeated_ids(client, factories):
    row = _catalog(factories, "Строка", kind=CatalogKind.TO_REVIEW.value)
    _with_positions(factories, row, ["одна"])

    body = client.post(
        "/api/v1/review/batch-kind", json={"ids": [row.id, row.id, row.id], "kind": "TRASH"}
    ).json()
    # Без дедупликации второй проход по той же строке дал бы 409 в skipped.
    assert body["applied"] == [row.id]
    assert body["skipped"] == []


def test_batch_kind_rejects_empty_and_oversized_batches(client):
    assert client.post(
        "/api/v1/review/batch-kind", json={"ids": [], "kind": "TRASH"}
    ).status_code == 422
    assert client.post(
        "/api/v1/review/batch-kind", json={"ids": list(range(1, 202)), "kind": "TRASH"}
    ).status_code == 422


# ---------------------------------------------------------------------------
#  Права: ручной матчинг принадлежит и `member` (§3)
# ---------------------------------------------------------------------------

def test_member_can_work_the_queue(member, factories):
    source = _catalog(factories, "Работа неведомая", kind=CatalogKind.TO_REVIEW.value)
    target = _catalog(factories, "Работа известная", kind=CatalogKind.POSITION.value)
    marked = _catalog(factories, "Мусор", kind=CatalogKind.TO_REVIEW.value)
    _with_positions(factories, source, ["одна"])
    _with_positions(factories, marked, ["одна"])

    assert member.get("/api/v1/review/queue").status_code == 200
    assert member.get("/api/v1/review/targets", params={"q": "работа"}).status_code == 200
    assert member.post(
        f"/api/v1/review/{marked.id}/kind", json={"kind": "TRASH"}
    ).status_code == 200
    assert member.post(
        f"/api/v1/review/{source.id}/merge", json={"target_id": target.id}
    ).status_code == 200
