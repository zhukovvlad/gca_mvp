"""Сериализация решений Review **на HTTP-слое** (фаза 5, разбор внешнего ревью).

Фаза 4 доказала, что `services/review.py` сериализует решения через `FOR UPDATE`,
но проверяла это, вызывая сервис напрямую. Фаза 5 добавила сверху роутер, который
перед вызовом сервиса загружал строку через `db.get`, — и создала возможность
**обойти собственную блокировку**: объект попадает в identity map, а
`SELECT ... FOR UPDATE` возвращает его же с устаревшими атрибутами, не читая
свежие значения в сущность, поэтому проверка `kind` идёт по данным ДО блокировки.

Ровно тот случай, о котором предупреждает урок фазы 4: «снятие защиты доказывает
только тот путь, который тест исполняет». Тесты фазы 4 исполняли путь сервиса, а
возможность обхода появилась на пути роутера.

Тесты здесь работают на `committing_client` (нужны настоящие коммиты) и
детерминированы: конкурирующее решение коммитится не другим потоком, а хуком
ровно в тот момент, когда роутер уже проверил существование, но сервис ещё не
захватил блокировку. Это то самое окно, в котором терялось решение.

**Важная поправка, полученная снятием защиты.** HTTP-тесты ниже проходят и на
коде ДО исправления, и это не их слабость, а факт о самом дефекте: identity map
SQLAlchemy держит **слабые** ссылки, а роутер результат `db.get` отбрасывал
(`_require_exists(db, id)` без присваивания), поэтому refcounting CPython успевал
вытеснить объект до блокирующего SELECT. То есть на сданных путях фазы 5 потери
решения не происходило — но корректность держалась на времени сборки мусора, а не
на коде. Стоило кому-нибудь написать `row = _require_exists(...)`, и дефект стал бы
реальным: замер показал, что при удержанной ссылке и снятом `populate_existing`
два из трёх тестов ниже падают.

Поэтому механизм закреплён отдельным тестом
`test_lock_refreshes_a_row_already_loaded_in_the_session` — он удерживает ссылку
сам и падает от снятия `populate_existing` независимо от сборщика мусора.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import CatalogKind, CatalogPosition
from parser.sanitize_text import normalize_job_title_with_lemmatization
from services.review import ReviewError, set_kind

pytestmark = pytest.mark.integration


def _catalog(factories, title: str, kind: str) -> CatalogPosition:
    return factories.CatalogPositionFactory.create(
        standard_job_title=title,
        normalized_job_title=normalize_job_title_with_lemmatization(title),
        kind=kind,
    )


@pytest.fixture
def rival(committing_session_factory, monkeypatch):
    """Хук «другой оператор успел раньше».

    Возвращает функцию `arm(catalog_id, kind)`, которая подменяет
    `routers.review.set_kind` и `routers.review.merge_into_position` обёртками:
    перед настоящим вызовом отдельная сессия меняет `kind` строки и коммитит.

    Момент подмены выбран намеренно: обёртка срабатывает ПОСЛЕ проверки
    существования в роутере и ДО захвата `FOR UPDATE` в сервисе — то есть ровно в
    окне, где ORM-кэш роутера расходился с БД.
    """
    import routers.review as router_module

    def arm(catalog_id: int, kind: str) -> None:
        def commit_rival_decision() -> None:
            session = committing_session_factory()
            try:
                session.execute(
                    sa.update(CatalogPosition)
                    .where(CatalogPosition.id == catalog_id)
                    .values(kind=kind)
                )
                session.commit()
            finally:
                session.close()

        for name in ("set_kind", "merge_into_position"):
            original = getattr(router_module, name)

            def wrapper(*args, _original=original, **kwargs):
                commit_rival_decision()
                return _original(*args, **kwargs)

            monkeypatch.setattr(router_module, name, wrapper)

    return arm


def test_kind_decision_lost_to_a_rival_is_refused(
    committing_client, committing_factories, committing_db, rival
):
    """Строку разобрали, пока мы шли к блокировке → 409, а не молчаливая перезапись."""
    row = _catalog(committing_factories, "Спорная работа", CatalogKind.TO_REVIEW.value)
    committing_db.commit()
    rival(row.id, CatalogKind.POSITION.value)

    response = committing_client.post(f"/api/v1/review/{row.id}/kind", json={"kind": "TRASH"})

    assert response.status_code == 409, response.text
    assert "kind=POSITION" in response.json()["detail"]

    # И решение соперника осталось на месте — наше не применилось.
    committing_db.expire_all()
    assert (
        committing_db.execute(
            sa.select(CatalogPosition.kind).where(CatalogPosition.id == row.id)
        ).scalar_one()
        == CatalogKind.POSITION.value
    )


def test_merge_lost_to_a_rival_is_refused(
    committing_client, committing_factories, committing_db, rival
):
    """То же для слияния: источник уже утверждён как работа → 409."""
    source = _catalog(committing_factories, "Источник спорный", CatalogKind.TO_REVIEW.value)
    target = _catalog(committing_factories, "Цель слияния", CatalogKind.POSITION.value)
    proposal = committing_factories.ProposalFactory.create()
    committing_factories.PositionItemFactory.create(
        proposal=proposal, catalog_position=source
    )
    committing_db.commit()
    rival(source.id, CatalogKind.POSITION.value)

    response = committing_client.post(
        f"/api/v1/review/{source.id}/merge", json={"target_id": target.id}
    )

    assert response.status_code == 409, response.text
    # Строка не удалена: слияние отменено целиком.
    committing_db.expire_all()
    assert (
        committing_db.execute(
            sa.select(sa.func.count()).select_from(CatalogPosition).where(
                CatalogPosition.id == source.id
            )
        ).scalar_one()
        == 1
    )


def test_lock_refreshes_a_row_already_loaded_in_the_session(
    committing_session_factory, committing_factories, committing_db
):
    """`FOR UPDATE` обязан читать состояние в УЖЕ загруженную сущность.

    Тест держит ссылку на предзагруженный объект сам — иначе слабый identity map
    отдал бы его сборщику мусора, и проверить механизм было бы нельзя. Именно так
    и вышло с HTTP-тестами выше: они проходили и без исправления.

    Падает, если убрать `populate_existing=True` из `_lock_rows`.
    """
    row = _catalog(committing_factories, "Предзагруженная", CatalogKind.TO_REVIEW.value)
    committing_db.commit()

    session = committing_session_factory()
    try:
        held = session.get(CatalogPosition, row.id)   # сильная ссылка — не соберётся
        assert held.kind == CatalogKind.TO_REVIEW.value

        rival_session = committing_session_factory()
        try:
            rival_session.execute(
                sa.update(CatalogPosition)
                .where(CatalogPosition.id == row.id)
                .values(kind=CatalogKind.POSITION.value)
            )
            rival_session.commit()
        finally:
            rival_session.close()

        with pytest.raises(ReviewError, match="kind=POSITION"):
            set_kind(session, to_review_id=row.id, kind=CatalogKind.TRASH.value)
    finally:
        session.rollback()
        session.close()


def test_batch_decision_lost_to_a_rival_is_reported_as_skipped(
    committing_client, committing_factories, committing_db, rival
):
    """В пакете проигранная строка попадает в `skipped`, а не применяется молча."""
    row = _catalog(committing_factories, "Пакетная спорная", CatalogKind.TO_REVIEW.value)
    committing_db.commit()
    rival(row.id, CatalogKind.HEADER.value)

    body = committing_client.post(
        "/api/v1/review/batch-kind", json={"ids": [row.id], "kind": "TRASH"}
    ).json()

    assert body["applied"] == []
    assert len(body["skipped"]) == 1
    assert "kind=HEADER" in body["skipped"][0]["reason"]
