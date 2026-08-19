"""Справочник рядов индексов инфляции: CRUD и правка одной транзакцией
(спека `2026-08-18-inflation-adjustment-design.md` §2.10, §2.12; план, задача 5).

Ряд **общий и без версий**, поэтому «исправлен наполовину» здесь означает не
испорченную форму, а неверные числа у всех, кто в этот момент смотрит сравнение.
Отсюда два требования, которые и стерегут тесты этого файла: запись — одна
транзакция на всё окно правки, а `updated_at` двигается тогда и только тогда,
когда что-то действительно изменилось.

**Метки времени проверяются на `committing_db`, а не на `db_session`.** Замер
2026-08-18 на `gca_test`: `now()` внутри одной транзакции возвращает ОДНО И ТО ЖЕ
значение до и после savepoint-commit'а (различается только `clock_timestamp()`).
В транзакционной фикстуре поэтому «сдвинулась» недоказуемо, а «не сдвинулась» —
вакуозно зелено (§12, инсайт про ложные предпосылки).
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import inflation_series as crud
from crud.common import DomainError
from models import InflationIndexValue, InflationSeries

pytestmark = pytest.mark.integration

SERIES_NAME = "Росстат, ИПЦ, декабрь к декабрю"
OTHER_NAME = "Внутренняя оценка ПЭО"


def year(year_: int, coefficient: str, source: str = "бюллетень 01.2026", *, forecast=False):
    """Год ряда — ВСЕГДА тремя полями: это описание года целиком, а не подкрутка."""
    return {
        "year": year_,
        "coefficient": Decimal(coefficient),
        "source": source,
        "is_forecast": forecast,
    }


#: Отличает «годы не заданы, возьми умолчание» от «годов НЕТ ни одного»: `or`
#: здесь недопустим — пустой список фальшив, и тест атомарности начинался бы с
#: ряда, в котором год уже есть, то есть проверял бы не то, что написано.
_DEFAULT_YEARS = object()


def make_series(
    db, *, name=SERIES_NAME, note="официальная публикация, по РФ", years=_DEFAULT_YEARS
) -> dict:
    if years is _DEFAULT_YEARS:
        years = [year(2025, "1.083")]
    return crud.create_series(db, name=name, note=note, values=list(years))


def stored_years(db, series_id: int) -> dict[int, tuple]:
    """Тройки годов ПРЯМО ИЗ БАЗЫ, минуя возвращаемые словари."""
    rows = db.execute(
        sa.select(
            InflationIndexValue.year,
            InflationIndexValue.coefficient,
            InflationIndexValue.source,
            InflationIndexValue.is_forecast,
        ).where(InflationIndexValue.series_id == series_id)
    ).all()
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


# ---------------------------------------------------------------------------
#  Создание и чтение
# ---------------------------------------------------------------------------

def test_create_series_writes_name_note_and_years(db_session):
    created = make_series(db_session, years=[year(2024, "1.075"), year(2026, "1.060", forecast=True)])

    assert created["name"] == SERIES_NAME
    assert created["note"] == "официальная публикация, по РФ"
    assert created["is_active"] is True
    # Охват годов показывается в списке рядов (§2.12), поэтому он и в словаре.
    assert (created["year_from"], created["year_to"]) == (2024, 2026)

    values = crud.list_values(db_session, created["id"])
    assert [item["year"] for item in values] == [2024, 2026]
    assert values[1]["is_forecast"] is True


def test_create_series_rejects_blank_name(db_session):
    with pytest.raises(DomainError) as exc:
        make_series(db_session, name="   ")
    assert exc.value.status_code == 422


def test_duplicate_series_name_is_a_conflict(db_session):
    make_series(db_session)
    with pytest.raises(DomainError) as exc:
        make_series(db_session, name=SERIES_NAME)
    assert exc.value.status_code == 409


def test_unknown_series_is_404(db_session):
    with pytest.raises(DomainError) as exc:
        crud.get_series_dict(db_session, 10**9)
    assert exc.value.status_code == 404

    with pytest.raises(DomainError) as exc:
        crud.list_values(db_session, 10**9)
    assert exc.value.status_code == 404


def test_archived_series_is_hidden_from_the_list_but_readable_by_id(db_session):
    """Архивный ряд не предлагается для нового выбора, но старая ссылка обязана
    работать (§2.10) — два разных утверждения, оба проверяются."""
    active = make_series(db_session)
    archived = make_series(db_session, name=OTHER_NAME, years=[year(2025, "1.120")])
    crud.update_series(db_session, archived["id"], is_active=False)

    default_ids = [item["id"] for item in crud.list_series(db_session)]
    assert default_ids == [active["id"]]

    with_archived = [item["id"] for item in crud.list_series(db_session, include_archived=True)]
    assert sorted(with_archived) == sorted([active["id"], archived["id"]])

    assert crud.get_series_dict(db_session, archived["id"])["is_active"] is False
    assert [item["year"] for item in crud.list_values(db_session, archived["id"])] == [2025]


# ---------------------------------------------------------------------------
#  Форма года и повтор года
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing", ["coefficient", "source", "is_forecast"])
def test_incomplete_year_is_rejected(db_session, missing):
    """Год задаётся ВСЕМИ ТРЕМЯ полями: неполный отвергается (DoD 27).

    Иначе `updated_at` года двигался бы правкой, которая описывает год не
    целиком, а лист утверждал бы, что коэффициент исправлен.
    """
    series = make_series(db_session)
    incomplete = year(2026, "1.060")
    del incomplete[missing]

    with pytest.raises(DomainError) as exc:
        crud.update_series(db_session, series["id"], values=[incomplete])
    assert exc.value.status_code == 422
    assert 2026 not in stored_years(db_session, series["id"])


@pytest.mark.parametrize("coefficient", ["0", "-1"])
def test_non_positive_coefficient_is_rejected_by_the_domain(db_session, coefficient):
    """`coefficient > 0` проверяет ДОМЕН, а не схема запроса (Global Constraints).

    В схеме `gt=0` запрещён явно: он увёл бы отказ из CRUD и обессмыслил тест
    атомарности ниже.
    """
    series = make_series(db_session)
    with pytest.raises(DomainError) as exc:
        crud.update_series(db_session, series["id"], values=[year(2026, coefficient)])
    assert exc.value.status_code == 422
    assert 2026 not in stored_years(db_session, series["id"])


def test_duplicate_year_in_the_payload_is_rejected_before_any_write(db_session):
    """Повтор года — `422` с кодом и перечнем, ДО записи (DoD 29).

    Проверяется тем, что в базе после отказа не изменилось НИЧЕГО: иначе защита
    свелась бы к конфликту уникального индекса, то есть к ошибке базы вместо
    внятного ответа, и зависела бы от порядка — победил бы первый или последний.
    """
    series = make_series(db_session, years=[year(2025, "1.083")])
    before = stored_years(db_session, series["id"])

    with pytest.raises(DomainError) as exc:
        crud.update_series(
            db_session,
            series["id"],
            name="Новое название",
            values=[year(2026, "1.060"), year(2026, "1.070"), year(2027, "1.040")],
        )

    assert exc.value.status_code == 422
    assert exc.value.code == "duplicate_year"
    assert exc.value.context == {"years": [2026]}

    db_session.expire_all()
    assert crud.get_series_dict(db_session, series["id"])["name"] == SERIES_NAME
    assert stored_years(db_session, series["id"]) == before


def test_years_not_listed_in_the_body_remain(db_session):
    """`PATCH`, а не `PUT`: `DELETE` запрещён, и умолчание не удаляет годы (DoD 27)."""
    series = make_series(db_session, years=[year(2024, "1.075"), year(2025, "1.083")])

    crud.update_series(db_session, series["id"], values=[year(2026, "1.060")])

    assert sorted(stored_years(db_session, series["id"])) == [2024, 2025, 2026]


def test_existing_year_is_updated_in_place(db_session):
    series = make_series(db_session, years=[year(2025, "1.083")])

    crud.update_series(
        db_session, series["id"],
        values=[year(2025, "1.0915", source="уточнение бюллетеня 03.2026", forecast=True)],
    )

    stored = stored_years(db_session, series["id"])[2025]
    assert stored[0] == Decimal("1.0915")
    assert stored[1] == "уточнение бюллетеня 03.2026"
    assert stored[2] is True


# ---------------------------------------------------------------------------
#  Архивный ряд: заморожен, но обратим (DoD 28)
# ---------------------------------------------------------------------------

def _archived(db) -> dict:
    series = make_series(db)
    crud.update_series(db, series["id"], is_active=False)
    return series


def test_unfreeze_alone_returns_the_series_to_active(db_session):
    """Без этого случая архивация была бы НЕОБРАТИМОЙ (DoD 28)."""
    series = _archived(db_session)

    updated = crud.update_series(db_session, series["id"], is_active=True)

    assert updated["is_active"] is True


def test_unfreeze_together_with_edits_is_a_conflict(db_session):
    """Совмещённая расконсервация запрещена намеренно: иначе «заморожен»
    проверялось бы внутри той же транзакции, которая размораживает, и правило
    перестало бы быть проверяемым."""
    series = _archived(db_session)

    with pytest.raises(DomainError) as exc:
        crud.update_series(db_session, series["id"], is_active=True, name="Другое имя")
    assert exc.value.status_code == 409

    with pytest.raises(DomainError) as exc:
        crud.update_series(db_session, series["id"], is_active=True, values=[year(2026, "1.060")])
    assert exc.value.status_code == 409

    db_session.expire_all()
    assert crud.get_series_dict(db_session, series["id"])["is_active"] is False


def test_unfreeze_with_an_empty_values_list_is_a_conflict(db_session):
    """`values: []` — ПЕРЕДАННОЕ поле, а не его отсутствие (спека §2.12, DoD 28).

    По смыслу правок в пустом списке ноль, но по букве спеки поле в теле есть, и
    расконсервация обязана идти ОТДЕЛЬНЫМ запросом. Правило закреплено тестом
    именно потому, что первая редакция реализации трактовала пустой список как
    отсутствие поля: такое решение переоткрывает гейт 2 и docstring-ом не
    принимается.

    Парой к этому тесту идёт `test_unfreeze_alone_returns_the_series_to_active`:
    там `values` не передаётся вовсе, и ряд размораживается. Два утверждения на
    одно различение — иначе непонятно, что именно ловит `409`.
    """
    series = _archived(db_session)

    with pytest.raises(DomainError) as exc:
        crud.update_series(db_session, series["id"], is_active=True, values=[])
    assert exc.value.status_code == 409

    db_session.expire_all()
    assert crud.get_series_dict(db_session, series["id"])["is_active"] is False


def test_unfreeze_with_values_none_is_the_same_as_not_sending_it(db_session):
    """`values: null` от опущенного поля не отличается — обычная семантика `PATCH`."""
    series = _archived(db_session)

    assert crud.update_series(
        db_session, series["id"], is_active=True, values=None
    )["is_active"] is True


@pytest.mark.parametrize(
    "patch",
    [
        {"name": "Другое имя"},
        {"note": "другое примечание"},
        {"values": [{"year": 2026, "coefficient": Decimal("1.060"),
                     "source": "бюллетень", "is_forecast": True}]},
        {"is_active": False},
    ],
)
def test_any_edit_of_an_archived_series_is_a_conflict(db_session, patch):
    series = _archived(db_session)

    with pytest.raises(DomainError) as exc:
        crud.update_series(db_session, series["id"], **patch)
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
#  Атомарность правки (DoD 26): шаг 3 плана
# ---------------------------------------------------------------------------

def test_patch_with_a_broken_second_year_applies_nothing(db_session):
    """Первый год валиден, второй нет — не применяется НИ ОДНОГО, и `name` тоже.

    **Почему именно пустой `source`, а не `coefficient = "-1"`:** отказ обязан
    случиться ВНУТРИ CRUD, после того как первый год уже применён. Правило
    «непусто после `btrim`» естественным образом не выражается в pydantic
    (`min_length=1` пропускает пробел), тогда как положительность коэффициента
    кто-нибудь однажды продублирует в схеме `gt=0` — и тест станет вакуозным, не
    изменив ни строчки в самом тесте.

    Состояние читается ПОСЛЕ `expire_all()`: identity map вернул бы объект из
    памяти сессии, и тест проверил бы кэш, а не базу.
    """
    series = make_series(db_session, years=[])
    assert stored_years(db_session, series["id"]) == {}

    with pytest.raises(DomainError) as exc:
        crud.update_series(
            db_session,
            series["id"],
            name="Ряд с исправленным названием",
            values=[
                year(2025, "1.0830", source="бюллетень 01.2026"),
                year(2026, "1.060", source="   "),
            ],
        )

    assert exc.value.status_code == 422
    # Кода у отказа нет — это обычный доменный отказ, а не одно из кодированных
    # состояний фичи.
    assert exc.value.code is None
    #
    # УТВЕРЖДЕНИЯ «`detail` — строка, а не список pydantic» ЗДЕСЬ НЕТ НАМЕРЕННО.
    # На этом уровне схема запроса не исполняется вовсе, поэтому `isinstance(...,
    # str)` остался бы зелёным при любой будущей схеме — то есть читался бы как
    # живая защита, будучи мёртвой (§12, инсайт про снятие защиты, слой 7).
    # Настоящая проверка возможна только через HTTP и живёт в задаче 6:
    # `PATCH` с пустым `source` обязан вернуть 422 со СТРОКОВЫМ `detail`, а не
    # список ошибок pydantic. Найдено внешним ревью.

    db_session.expire_all()
    assert crud.get_series_dict(db_session, series["id"])["name"] == SERIES_NAME
    assert stored_years(db_session, series["id"]) == {}


# ---------------------------------------------------------------------------
#  Метки времени (DoD 37): только на committing_db
# ---------------------------------------------------------------------------

def _stamps(db, series_id: int) -> tuple:
    """Метка ряда и метки его годов, прочитанные заново из базы."""
    db.expire_all()
    series_stamp = db.execute(
        sa.select(InflationSeries.updated_at).where(InflationSeries.id == series_id)
    ).scalar_one()
    year_stamps = dict(
        db.execute(
            sa.select(InflationIndexValue.year, InflationIndexValue.updated_at)
            .where(InflationIndexValue.series_id == series_id)
        ).all()
    )
    return series_stamp, year_stamps


def test_updated_at_moves_only_when_something_actually_changed(committing_db):
    """Оба направления (DoD 37).

    Сдвиг без изменений соврал бы на полосе уровней, что ряд правили; отсутствие
    сдвига при изменении лишило бы компромисс §2.10 («ссылка не гарантирует
    исторического результата») единственного носителя на поверхности.

    Фикстура именно `committing_db`: каждый вызов CRUD коммитит, то есть идёт
    своей транзакцией, и `now()` между ними РАЗНАЯ. В `db_session` обе половины
    утверждения были бы недоказуемы.
    """
    series = make_series(
        committing_db, years=[year(2024, "1.075"), year(2025, "1.083")]
    )
    series_id = series["id"]
    base_series, base_years = _stamps(committing_db, series_id)

    # 1. Запрос, повторяющий текущее состояние ЦЕЛИКОМ, не двигает ничего.
    crud.update_series(
        committing_db, series_id,
        name=SERIES_NAME, note="официальная публикация, по РФ",
        values=[year(2024, "1.075"), year(2025, "1.083")],
    )
    same_series, same_years = _stamps(committing_db, series_id)
    assert same_series == base_series
    assert same_years == base_years

    # 2. Правка ОДНОГО года двигает и его метку, и метку ряда; сосед не тронут.
    crud.update_series(committing_db, series_id, values=[year(2025, "1.0915")])
    after_year, year_stamps = _stamps(committing_db, series_id)
    assert year_stamps[2025] > base_years[2025]
    assert year_stamps[2024] == base_years[2024]
    assert after_year > base_series

    # 3. Правка поля ряда двигает метку ряда, годы остаются на месте.
    crud.update_series(committing_db, series_id, note="уточнённое примечание")
    after_note, note_years = _stamps(committing_db, series_id)
    assert after_note > after_year
    assert note_years == year_stamps


# ---------------------------------------------------------------------------
#  HTTP: маршруты, права, форма чисел (план, задача 6)
# ---------------------------------------------------------------------------

URL = "/api/v1/inflation-series"


def _body(**over) -> dict:
    """Тело года В ФОРМЕ JSON: коэффициент СТРОКОЙ, как требует §3."""
    payload = {
        "year": 2025, "coefficient": "1.083",
        "source": "бюллетень 01.2026", "is_forecast": False,
    }
    payload.update(over)
    return payload


def test_member_reads_series_and_values(member_client, db_session):
    """`member` читает ряды и годы: без этого он не увидел бы даже названия ряда,
    которым приведены показанные ему числа (§2.10)."""
    series = make_series(db_session)

    listed = member_client.get(URL)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [series["id"]]

    values = member_client.get(f"{URL}/{series['id']}/values")
    assert values.status_code == 200
    assert [item["year"] for item in values.json()] == [2025]


def test_member_cannot_write_or_archive(member_client, db_session):
    """Запись и архивация — `admin` (DoD 30). Архивация проверяется ОТДЕЛЬНО:
    это `PATCH` без правки полей, и право на неё легко потерять из вида."""
    series = make_series(db_session)

    created = member_client.post(URL, json={"name": "Ряд от member", "values": []})
    assert created.status_code == 403

    patched = member_client.patch(f"{URL}/{series['id']}", json={"note": "правка"})
    assert patched.status_code == 403

    archived = member_client.patch(f"{URL}/{series['id']}", json={"is_active": False})
    assert archived.status_code == 403


def test_admin_creates_series_and_coefficient_comes_back_as_a_string(admin_client):
    """Создание ряда через API — обязательный путь: справочник создан пустым (§2.6)."""
    created = admin_client.post(
        URL,
        json={"name": SERIES_NAME, "note": "официальная публикация", "values": [_body()]},
    )
    assert created.status_code == 201
    series_id = created.json()["id"]

    values = admin_client.get(f"{URL}/{series_id}/values").json()
    # Прогон по конкретному входу, а не чтение кода: `Decimal` без `decimal_json`
    # уехал бы float-ом (§3, DoD 24).
    assert values[0]["coefficient"] == "1.083"
    assert isinstance(values[0]["coefficient"], str)


def test_float_coefficient_is_rejected_and_string_is_accepted(admin_client):
    """`float` на входе отвергается, строка принимается (DoD 24).

    Проверяется прогоном по конкретному входу: валидатор с ИМЕНЕМ, совпавшим с
    чужим, схлопнулся бы молча, и по коду это неотличимо (§11).
    """
    rejected_ = admin_client.post(
        URL, json={"name": "Ряд с float", "values": [_body(coefficient=1.083)]}
    )
    assert rejected_.status_code == 422

    accepted = admin_client.post(
        URL, json={"name": "Ряд со строкой", "values": [_body(coefficient="1.083")]}
    )
    assert accepted.status_code == 201


def test_archived_series_is_absent_from_the_list_and_present_with_the_flag(
    admin_client, db_session
):
    series = make_series(db_session)
    assert admin_client.patch(
        f"{URL}/{series['id']}", json={"is_active": False}
    ).status_code == 200

    assert admin_client.get(URL).json() == []
    with_archived = admin_client.get(URL, params={"include_archived": 1}).json()
    assert [item["id"] for item in with_archived] == [series["id"]]

    # Старая ссылка обязана работать (§2.10): значения архивного читаются.
    assert admin_client.get(f"{URL}/{series['id']}/values").status_code == 200


def test_unknown_series_over_http_is_404(admin_client):
    assert admin_client.get(f"{URL}/{10**9}/values").status_code == 404
    assert admin_client.patch(f"{URL}/{10**9}", json={"note": "x"}).status_code == 404


def test_duplicate_year_over_http_carries_the_structured_detail(admin_client):
    """Кодированный отказ доезжает до клиента ОБЪЕКТОМ (DoD 21, §2.12).

    Утверждается словарь целиком: вложенный `{"context": {...}}` прошёл бы
    проверку «`years` где-то есть», оставаясь другим контрактом.
    """
    response = admin_client.post(
        URL,
        json={
            "name": "Ряд с дублем года",
            "values": [_body(year=2025), _body(year=2025, coefficient="1.090")],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "duplicate_year",
        "message": "Год встречается в запросе дважды: 2025.",
        "years": [2025],
    }


def test_blank_source_over_http_is_a_string_detail_not_a_pydantic_list(
    admin_client, db_session
):
    """Перенесено из задачи 5, шаг 3 (уточнение плана 2026-08-19).

    На уровне CRUD это утверждение ВАКУОЗНО — схема запроса там не исполняется
    вовсе. Здесь она исполняется, поэтому уход доменного правила «источник непуст
    после `btrim`» в схему роняет тест: pydantic отвечает СПИСКОМ ошибок, а
    доменный отказ — строкой. Именно этим тест и сторожит границу «в схеме форма,
    в домене правило».
    """
    series = make_series(db_session, years=[])

    response = admin_client.patch(
        f"{URL}/{series['id']}",
        json={"name": "Ряд с исправленным названием",
              "values": [_body(year=2025), _body(year=2026, source="   ")]},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str), f"ожидалась строка доменного отказа, получено {detail!r}"

    # Та же атомарность, но уже через HTTP: ни года, ни нового названия.
    db_session.expire_all()
    assert crud.get_series_dict(db_session, series["id"])["name"] == SERIES_NAME
    assert stored_years(db_session, series["id"]) == {}


def test_delete_is_not_available_for_series_or_values(admin_client, db_session):
    """`DELETE` не заведён НИГДЕ — ни для рядов, ни для значений (§2.10, DoD 16).

    Утверждение проверяется запросом, а не отсутствием кода: «маршрута нет» и
    «маршрут есть, но мы его не заметили» по чтению неотличимы, а разница видна
    только ответу. Ошибочное значение исправляется правкой, ненужный ряд
    архивируется — и то и другое обратимо, тогда как удаление ряда унесло бы и
    воспроизводимость уже выгруженных файлов, которые на него ссылаются.

    Ожидается `405`: путь существует, метод не поддержан. `404` означал бы, что
    маршрута нет вовсе, и тест перестал бы отличать «удаление запрещено» от
    «опечатка в адресе».
    """
    series = make_series(db_session)

    assert admin_client.delete(f"{URL}/{series['id']}").status_code == 405
    assert admin_client.delete(f"{URL}/{series['id']}/values").status_code == 405
    assert admin_client.delete(URL).status_code == 405

    # Ряд на месте: отказ метода ничего не тронул.
    db_session.expire_all()
    assert crud.get_series_dict(db_session, series["id"])["is_active"] is True


def test_name_taken_race_answers_409_not_500(db_session, monkeypatch):
    """Гонка по занятому названию отвечает `409`, а не `500`.

    Синхронная проверка «название занято» неполна по построению — её собственный
    докстринг это и говорит: между проверкой и записью вклинивается параллельный
    запрос. Констрейнт гонку закрывает, но БЕЗ трансляции наружу уходит сырой
    `IntegrityError`, то есть человек получает `500` вместо внятного «название
    занято».

    Гонка воспроизводится подменой синхронной проверки на пустую: настоящий
    параллельный коммит в одной транзакции теста не поставить, а проверяется здесь
    не он, а то, что нарушение уникальности ПЕРЕВЕДЕНО. Переименование выпускает
    `UPDATE` только на коммите, поэтому дефект и жил ровно в том, что `commit`
    стоял ВНЕ транслятора (найдено финальным ревью ветки).
    """
    first = make_series(db_session, name=SERIES_NAME)
    second = make_series(db_session, name=OTHER_NAME, years=[year(2025, "1.120")])
    assert first["id"] != second["id"]

    monkeypatch.setattr(crud, "_require_name_available", lambda *args, **kwargs: None)

    with pytest.raises(DomainError) as exc:
        crud.update_series(db_session, second["id"], name=SERIES_NAME)

    assert exc.value.status_code == 409
    assert "название" in exc.value.detail.lower()

    # Ряд не переименован: откат транслятора вернул состояние.
    db_session.expire_all()
    assert crud.get_series_dict(db_session, second["id"])["name"] == OTHER_NAME


def test_name_taken_race_answers_409_also_when_years_come_along(db_session, monkeypatch):
    """То же с годами в теле: `flush` годов происходит раньше коммита, и без
    трансляции вокруг ВСЕГО окна ответ разошёлся бы между двумя формами запроса."""
    make_series(db_session, name=SERIES_NAME)
    second = make_series(db_session, name=OTHER_NAME, years=[year(2025, "1.120")])

    monkeypatch.setattr(crud, "_require_name_available", lambda *args, **kwargs: None)

    with pytest.raises(DomainError) as exc:
        crud.update_series(
            db_session, second["id"], name=SERIES_NAME, values=[year(2026, "1.060")]
        )
    assert exc.value.status_code == 409
