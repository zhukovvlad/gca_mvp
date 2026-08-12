"""HTTP-слой разноса (спека разноса §2.7, §4.3)."""
from __future__ import annotations

import logging

import pytest
import sqlalchemy as sa

from models import EstimateCategoryOverride, EstimateRawData, Lot, PositionItem, Proposal, UserRole

pytestmark = pytest.mark.integration


def _url(estimate_id, position_item_id) -> str:
    return f"/api/v1/estimates/{estimate_id}/category-overrides/{position_item_id}"


def test_member_may_assign(member_client, imported_estimate, top_unassigned_chapter, category_id):
    r = member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id},
    )
    assert r.status_code == 200
    body = r.json()
    # Полный набор ключей, не один: паспорт ответа объявлен ОДИН раз (спека
    # §2.7), а Задача 7 строит TS-типы фронта ровно по этим трём ключам —
    # пропажа любого из них (или лишний ключ) должна ронять тест здесь, а не
    # обнаруживаться на фронте.
    assert set(body) == {"chapters_updated", "additional_works_updated", "chapters_manual"}
    assert body["chapters_updated"] > 1


def test_repeating_an_identical_put_is_a_no_op(
    member_client, db_session, factories, imported_estimate, top_unassigned_chapter, category_id
):
    """Спека §2.2: аудит не переписывается одинаковым запросом.

    Ревью нашло ложную предпосылку в первой версии этого теста: PostgreSQL
    `now()` внутри транзакции — это время НАЧАЛА транзакции, а не момента
    вызова, и оба запроса здесь идут в ОДНОЙ тестовой транзакции. Значит
    `sa.func.now()` дал бы одно и то же значение хоть с гвардом, хоть без —
    сравнение `after == before` не било бы вообще ни при каком поведении кода.
    По той же причине `assigned_by` не мог сдвинуться сам по себе: и первый,
    и второй запрос шли от ОДНОГО пользователя `member_client`.

    Лечится тем же приёмом, что уже есть в `test_changing_the_article_moves_
    the_audit` ниже: `assigned_at` сдвигается в прошлое ЯВНЫМ `UPDATE` и
    коммитится ДО повторного запроса — тогда «не изменилось» проверяет
    конкретное, заведомо отличное от `now()` значение. `assigned_by` сделан
    дискриминирующим отдельно: второй запрос идёт от ДРУГОГО пользователя
    (`member_client.set_user`) — если бы гвард исчез, автор решения переехал
    бы на второго, и `after.assigned_by == before.assigned_by` стало бы `False`.
    """
    original_author_id = member_client.user.id

    first = member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id, "note": "раз"},
    )
    assert first.status_code == 200

    db_session.execute(
        sa.text(
            "UPDATE estimate_category_overrides SET assigned_at = now() - interval '1 day' "
            "WHERE position_item_id = :rid"
        ),
        {"rid": top_unassigned_chapter.id},
    )
    db_session.commit()
    before = db_session.execute(
        sa.select(EstimateCategoryOverride.assigned_at, EstimateCategoryOverride.assigned_by).where(
            EstimateCategoryOverride.position_item_id == top_unassigned_chapter.id
        )
    ).one()

    other_user = factories.UserFactory.create(role=UserRole.member)
    member_client.set_user(other_user)

    again = member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id, "note": "раз"},
    )
    assert again.status_code == 200
    after = db_session.execute(
        sa.select(EstimateCategoryOverride.assigned_at, EstimateCategoryOverride.assigned_by).where(
            EstimateCategoryOverride.position_item_id == top_unassigned_chapter.id
        )
    ).one()
    assert after == before
    assert after.assigned_by != other_user.id
    # Не только «не другой» — а именно ТОТ, кто поставил решение первым.
    assert after.assigned_by == original_author_id


def test_changing_the_article_moves_the_audit(
    member_client, db_session, imported_estimate, top_unassigned_chapter, category_id, other_category_id
):
    member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id},
    )
    # Время сдвигается НАЗАД заведомо далеко, а не читается как есть: два запроса
    # внутри одного теста укладываются в разрешение `now()`, и `>=` прошло бы даже
    # при неизменённом времени — то есть стерегло бы ровно ничего.
    db_session.execute(
        sa.text(
            "UPDATE estimate_category_overrides SET assigned_at = now() - interval '1 day' "
            "WHERE position_item_id = :rid"
        ),
        {"rid": top_unassigned_chapter.id},
    )
    db_session.commit()
    before = db_session.execute(
        sa.select(EstimateCategoryOverride.assigned_at).where(
            EstimateCategoryOverride.position_item_id == top_unassigned_chapter.id
        )
    ).scalar_one()

    member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": other_category_id},
    )
    after = db_session.execute(
        sa.select(
            EstimateCategoryOverride.assigned_at, EstimateCategoryOverride.work_category_id
        ).where(EstimateCategoryOverride.position_item_id == top_unassigned_chapter.id)
    ).one()
    assert after.work_category_id == other_category_id
    assert after.assigned_at > before


def test_a_missing_category_is_404(member_client, imported_estimate, top_unassigned_chapter):
    r = member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": 10**9},
    )
    assert r.status_code == 404


def test_a_note_over_2000_chars_is_422(
    member_client, imported_estimate, top_unassigned_chapter, category_id
):
    """`OverrideRequest.note` объявлен `max_length=2000` — снять границу, и
    ничего в файле этого не заметило бы. Строка строится программно, а не
    вклеивается буквально: 2001 символ в исходнике был бы шумом без
    содержания."""
    too_long_note = "п" * 2001
    r = member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id, "note": too_long_note},
    )
    assert r.status_code == 422


def test_deleting_a_decision_that_is_not_there_is_200(
    member_client, imported_estimate, top_unassigned_chapter
):
    """Снятие идемпотентно (см. сервисный тест): состояние уже такое, какого хотел
    вызывающий, и сообщать ему об ошибке не о чем."""
    r = member_client.delete(_url(imported_estimate.id, top_unassigned_chapter.id))
    assert r.status_code == 200


def test_delete_removes_the_decision(
    member_client, db_session, imported_estimate, top_unassigned_chapter, category_id
):
    """Ревью: версия «только `200`» была буквально неотличима от `test_
    deleting_a_decision_that_is_not_there_is_200` — заменить `clear_override`
    на голый `return` пустого результата, и оба теста остались бы зелёными.
    Настоящая заявка имени теста — решение реально снимается, и §2.3 роутера
    («разделы, ставшие нераспределёнными, получают NULL») пересчитывается —
    проверяется здесь ЯВНО, а не только статусом ответа.
    """
    member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id},
    )
    r = member_client.delete(_url(imported_estimate.id, top_unassigned_chapter.id))
    assert r.status_code == 200

    decision = db_session.execute(
        sa.select(EstimateCategoryOverride.position_item_id).where(
            EstimateCategoryOverride.position_item_id == top_unassigned_chapter.id
        )
    ).scalar_one_or_none()
    assert decision is None

    chapter = db_session.execute(
        sa.select(PositionItem.work_category_id, PositionItem.category_source).where(
            PositionItem.id == top_unassigned_chapter.id
        )
    ).one()
    assert chapter.work_category_id is None
    assert chapter.category_source is None


def test_a_non_chapter_target_is_422(member_client, imported_estimate, any_position_row, category_id):
    """Ревью: тело запроса тут валидно, поэтому `422` сейчас приходит от
    сервисного `not_a_chapter` — но `HTTP_422_UNPROCESSABLE_CONTENT` отдаёт и
    FastAPI сам, если переименовать поле тела. Статус один и тот же у двух
    разных причин — различает их только текст: сервисное сообщение говорит
    именно про раздел, а не про формат запроса.
    """
    r = member_client.put(
        _url(imported_estimate.id, any_position_row.id), json={"work_category_id": category_id}
    )
    assert r.status_code == 422
    assert "не является разделом" in r.json()["detail"]


def test_a_missing_estimate_is_404(member_client, top_unassigned_chapter, category_id):
    r = member_client.put(_url(10**9, top_unassigned_chapter.id), json={"work_category_id": category_id})
    assert r.status_code == 404


def test_a_disabled_structure_is_409(
    member_client, estimate_with_broken_numbering, broken_chapter, category_id
):
    r = member_client.put(
        _url(estimate_with_broken_numbering.id, broken_chapter.id),
        json={"work_category_id": category_id},
    )
    assert r.status_code == 409


def test_anonymous_caller_is_401(
    anon_client, imported_estimate, top_unassigned_chapter, category_id
):
    """Коррекция брифа: анонимный клиент (без переопределённого
    `get_current_user`) не проходит аутентификацию, зарегистрированную в
    main.py для этого роутера, — независимо от роли.

    ОБЯЗАТЕЛЬНО обе ручки, не только `PUT`. `put_override` держит
    `current_user: User = Depends(get_current_user)` прямо в своей сигнатуре —
    он аутентифицирует себя ДАЖЕ если регистрацию `dependencies=_auth_dep` в
    `main.py` убрать, потому что параметр всё равно требует зависимость.
    `delete_override`, наоборот, `current_user` не принимает вовсе (`clear_
    override` не нуждается в авторе) — её защита держится ЦЕЛИКОМ на
    регистрации в `main.py`, и ничего в самой функции не заметило бы, если
    регистрацию снять. Тест на одном `PUT` эту вторую защиту не стережёт.
    """
    r = anon_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id},
    )
    assert r.status_code == 401

    r = anon_client.delete(_url(imported_estimate.id, top_unassigned_chapter.id))
    assert r.status_code == 401


def test_a_refused_put_rolls_back(
    member_client, db_session, estimate_with_broken_numbering, broken_chapter, category_id
):
    """Пункт (a), перенесённый из ревью Задачи 3: на отказе сервис сам не
    коммитит и не откатывает (эту транзакцию ведёт вызывающий) — `set_override`
    уже успел `flush`-нуть строку решения ДО того, как `apply_overrides`
    обнаружил отключённую структуру и поднял `structure_disabled`. Без
    `db.rollback()` в `_apply` эта строка осталась бы флешнутой (видимой по
    тому же соединению) несмотря на `409` в ответе.

    `db_session.commit()` ЗДЕСЬ обязателен, а не для порядка: фикстуры сметы
    строят её через тот же `db_session`, только `flush`-ем, без `commit`.
    Сессия в тестах привязана к соединению режимом `create_savepoint`
    (`tests/conftest.py`), поэтому без этого коммита весь тест жил бы в ОДНОМ
    савпоинте с созданием сметы — и `db.rollback()` внутри роутера откатил бы
    не только решение, но и саму смету с разделом, роняя тест `ObjectDeletedError`
    вместо проверки того, что стережём. Коммит здесь фиксирует границу
    савпоинта РОВНО перед действием, чей откат проверяется.
    """
    db_session.commit()

    r = member_client.put(
        _url(estimate_with_broken_numbering.id, broken_chapter.id),
        json={"work_category_id": category_id},
    )
    assert r.status_code == 409

    # Читаем НЕ из identity map: выборка отдельных колонок, а не готовых
    # ORM-объектов, обходит закешированные в сессии экземпляры и идёт в БД.
    decision = db_session.execute(
        sa.select(EstimateCategoryOverride.position_item_id).where(
            EstimateCategoryOverride.position_item_id == broken_chapter.id
        )
    ).scalar_one_or_none()
    assert decision is None

    # ЧЕСТНОЕ ПРИМЕЧАНИЕ: эта проверка НЕ отличает откат от его отсутствия для
    # ДАННОГО входа. `structure_disabled` в `apply_overrides` поднимается ДО
    # вызова `_materialize_chapters` для единственного лота этой сметы, то
    # есть `work_category_id` раздела не тронут ни на каком пути — что с
    # `db.rollback()`, что без него значение было бы тем же `None`. Строка
    # оставлена ради буквы задачи («ни один раздел не изменился»), но реальное
    # покрытие этого теста даёт ТОЛЬКО проверка `decision is None` выше.
    chapter_category = db_session.execute(
        sa.select(PositionItem.work_category_id).where(PositionItem.id == broken_chapter.id)
    ).scalar_one()
    assert chapter_category is None


def test_mapping_broken_is_500_not_409(
    member_client_no_raise, db_session, imported_estimate, top_unassigned_chapter, category_id
):
    """`mapping_broken` намеренно ОТСУТСТВУЕТ в `_STATUS` (см. комментарий
    роутера): расхождение разобранной копии файла со строками сметы —
    поломка НАШИХ данных, а не конфликт действия пользователя, и `409`
    предложил бы повторить обречённую попытку. Код обязан дойти до `500`.

    Воспроизведено тем же приёмом, что и сервисный тест `test_a_broken_
    bijection_is_refused_loudly` (test_category_override_apply.py): строка-
    ПОЗИЦИЯ (не раздел — иначе упёрлись бы в RESTRICT `fk_position_items_
    chapter` раньше) удаляется из БД в обход домена, так что `raw_data`
    ссылается на ключ, которому в БД уже нет строки — `_require_bijection`
    обязана поднять `mapping_broken` при пересчёте.

    Требование спеки — «`500` И логи» (комментарий `_apply`: «должна дойти до
    `500` и до логов»), поэтому статус-кода мало — лог-запись стережётся тоже.
    Проверка идёт НЕ через `caplog`: `setup_logging()` (`logging_config.py`)
    делает `root.handlers.clear()`, а хендлер `caplog` навешен именно на ROOT
    (весь тестовый процесс на это напарывался — см. `test_unit_resolution.py::
    captured_warnings`, тот же грабл документирован там). Если `main` в этом
    процессе импортируется впервые прямо во время этого теста (изолированный
    запуск одного теста), `setup_logging()` сработает внутри фазы теста и
    снимет хендлер `caplog` — проверка тогда молча увидела бы пустой список
    независимо от того, залогировал ли код что-нибудь. Хендлер ниже навешен
    НАПРЯМУЮ на логгер роутера (`routers.category_overrides`), очистка
    `root.handlers` его не касается.
    """
    victim = db_session.execute(
        sa.select(PositionItem.id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == imported_estimate.id, PositionItem.is_chapter.is_(False))
        .limit(1)
    ).scalar_one()
    db_session.execute(sa.text("DELETE FROM position_items WHERE id = :rid"), {"rid": victim})
    db_session.flush()

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    captured: list[logging.LogRecord] = []
    router_log = logging.getLogger("routers.category_overrides")
    handler = _Collector(level=logging.ERROR)
    router_log.addHandler(handler)
    try:
        r = member_client_no_raise.put(
            _url(imported_estimate.id, top_unassigned_chapter.id),
            json={"work_category_id": category_id},
        )
    finally:
        router_log.removeHandler(handler)

    assert r.status_code == 500
    # `any`, не `all`: `all()` на пустом списке истинен по построению — такая
    # проверка не заметила бы, что запись не долетела вовсе (см. предупреждение
    # ревью про «снесённый caplog»).
    assert any(rec.levelno == logging.ERROR for rec in captured)


def test_missing_raw_data_is_404(
    member_client, db_session, imported_estimate, top_unassigned_chapter, category_id
):
    """Пункт (b), перенесённый из ревью Задачи 3: у `apply_overrides` есть
    четвёртый `not_found` — смета есть, а `estimate_raw_data` нет (у импорта
    такое недостижимо, он вставляет обе строки в одной транзакции, но роутер
    не должен исходить из этого и обязан ответить так же, как на любой другой
    `not_found`)."""
    db_session.execute(
        sa.delete(EstimateRawData).where(EstimateRawData.estimate_id == imported_estimate.id)
    )
    db_session.flush()

    r = member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id},
    )
    assert r.status_code == 404
    assert "разобранной копии" in r.json()["detail"]
