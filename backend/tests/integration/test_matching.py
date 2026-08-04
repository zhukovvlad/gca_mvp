"""Каскад матчинга (AGENTS.md §5, шаг 4) и ручные решения Review."""
from __future__ import annotations

import hashlib
import random
from datetime import timedelta

import psycopg
import pytest
import sqlalchemy as sa
from freezegun import freeze_time
from sqlalchemy.dialects import postgresql

from models import CatalogKind, CatalogPosition, MatchingCache, MatchSource, PositionItem
from parser.sanitize_text import normalize_job_title_with_lemmatization
from services.estimate_import import import_estimate
from services.matching import (
    AUTO_CACHE_TTL_DAYS,
    NORM_VERSION,
    cache_key,
    match_positions,
    norm_hash,
)
from services.review import ReviewError, merge_into_position, set_kind
from services.unit_resolution import UnitResolver
from tests.payloads import payload_for, position
from utils import utcnow_aware

pytestmark = pytest.mark.integration


@pytest.fixture
def resolver(db_session):
    return UnitResolver(db_session)


def unit_id(db_session, code: str) -> int:
    return db_session.execute(
        sa.text("SELECT id FROM units_of_measure WHERE code = :code"), {"code": code}
    ).scalar_one()


def norm(title: str) -> str:
    return normalize_job_title_with_lemmatization(title)


#: Обратный слэш отдельной константой: в исходнике теста он иначе тонет в
#: собственном экранировании, а именно на нём ломалось наивное `text::bytea`.
BACKSLASH = chr(92)


def _long_title() -> str:
    """Наименование, которое не влезает в btree ни в каком виде.

    Длины мало: индексный кортеж перед проверкой предела **сжимается** pglz, и
    название из повторяющейся фразы (даже на 9 КБ) укладывается в 2704 байта —
    такая фикстура молча перестала бы воспроизводить дефект. Поэтому текст
    псевдослучайный (фиксированное зерно — нормализация обязана быть
    детерминированной, §11), а сама предпосылка закреплена отдельным тестом
    `test_a_plain_btree_index_rejects_this_title`.

    Порядок величины взят с реального файла фазы 0: 5077 символов, 9260 байт,
    3424 байта после сжатия.
    """
    rnd = random.Random(20260804)
    letters = "абвгдежзийклмнопрстуфхцчшщыэюя"
    words = ["".join(rnd.choice(letters) for _ in range(rnd.randint(4, 12))) for _ in range(900)]
    return " ".join(words)


LONG_TITLE = _long_title()


def import_and_match(db_session, resolver, contract, positions, *, now=None, amendment_no=None):
    """Импорт + матчинг в одной транзакции — как это делает сессия B (§5)."""
    outcome = import_estimate(
        db_session,
        contract=contract,
        amendment_no=amendment_no,
        data=payload_for(contract, positions),
        parser_version="1.0.0",
        import_job_id=None,
        replace=False,
        unit_resolver=resolver,
    )
    match = match_positions(db_session, outcome.positions_to_match, now=now)
    return outcome, match


# ---------------------------------------------------------------------------
#  cache_key
# ---------------------------------------------------------------------------

class TestCacheKey:
    def test_unit_is_part_of_the_key(self):
        """Единица ОБЯЗАТЕЛЬНА в ключе (§4): иначе «кладка, м2» = «кладка, м3»."""
        assert cache_key("кладка", "M2") != cache_key("кладка", "M3")

    def test_no_unit_is_empty_string(self):
        assert cache_key("кладка", "") != cache_key("кладка", "M2")

    def test_norm_version_is_part_of_the_key(self):
        assert cache_key("кладка", "M2", 1) != cache_key("кладка", "M2", 2)

    def test_key_is_deterministic(self):
        assert cache_key("кладка", "M2") == cache_key("кладка", "M2")

    def test_key_is_sha256_hex(self):
        key = cache_key("кладка", "M2")
        assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)


# ---------------------------------------------------------------------------
#  Хэш названия в уникальном индексе (миграция 0003)
# ---------------------------------------------------------------------------

class TestNormHash:
    """`norm_hash` обязан быть верным представлением ПОЛНОГО названия.

    Хэш стоит в уникальном индексе, то есть решает, одна это работа или две.
    Ошибка тут не падает, а склеивает расценки, поэтому проверяется отдельно от
    каскада.
    """

    #: Входы, на которых наивное `text::bytea` ведёт себя неверно: одни падают
    #: `invalid input syntax for type bytea`, другие молча дают байты чужой
    #: строки («\\x41» и «\\101» — тот же байт, что «A»).
    NASTY = [
        "простая работа",
        "a" + BACKSLASH + "b",
        BACKSLASH + "x41",
        BACKSLASH + "101",
        "A",
        "перегородка " + BACKSLASH + " стена",
        "C:" + BACKSLASH + "temp" + BACKSLASH + "x",
        BACKSLASH,
        BACKSLASH + BACKSLASH,
        "конец" + BACKSLASH,
        "кладка м2 — «дом»\n перенос\tтабуляция",
        "".join(chr(i) for i in range(1, 128)),
        LONG_TITLE + BACKSLASH,
    ]

    def test_hash_equals_sha256_of_utf8_bytes(self, db_session):
        """Серверное выражение = `hashlib.sha256(title.encode('utf-8'))`.

        Рабочий код на это равенство не опирается (обе стороны сравнения считает
        сервер), но именно оно доказывает, что хэш берётся от самой строки, а не от
        разобранных escape-последовательностей.
        """
        for title in self.NASTY:
            got = db_session.execute(sa.select(norm_hash(sa.literal(title)))).scalar_one()
            assert bytes(got) == hashlib.sha256(title.encode("utf-8")).digest(), repr(title[:40])

    def test_different_titles_give_different_hashes(self, db_session):
        hashes = {
            bytes(db_session.execute(sa.select(norm_hash(sa.literal(t)))).scalar_one())
            for t in self.NASTY
        }
        assert len(hashes) == len(self.NASTY)

    def test_expression_does_not_depend_on_standard_conforming_strings(
        self, db_session, factories, resolver
    ):
        """Слэши записаны escape-строками, поэтому настройка сессии не важна.

        Обычный литерал `'\\'` означает один символ только при
        `standard_conforming_strings=on`. При `off` тот же арбитр `ON CONFLICT` —
        синтаксическая ошибка (замер на PG 16.14), то есть матчинг сломался бы на
        БД, где настройку выставили явно. `E'…'` разбирает escape-последовательности
        при любом значении и даёт то же дерево выражения, так что индекс
        сопоставляется по-прежнему.
        """
        db_session.execute(sa.text("SET LOCAL standard_conforming_strings = off"))
        contract = factories.ContractFactory.create()
        db_session.flush()

        _o, match = import_and_match(
            db_session,
            resolver,
            contract,
            [position(job_title="Работа при выключенной настройке", unit="м2")],
        )

        assert match.counters.to_review == 1
        row = db_session.execute(
            sa.select(CatalogPosition).where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
        ).scalar_one()
        assert row.standard_job_title == "Работа при выключенной настройке"

    def test_index_expression_matches_the_migration(self, db_session, factories):
        """Выражение в коде и в миграции 0003 — одно и то же.

        Проверка функциональная, а не текстовая: если выражения разойдутся,
        PostgreSQL не сможет применить индекс к запросу по `norm_hash` (и, что
        важнее, перестанет выводить его как арбитр `ON CONFLICT`).

        Смотреть только на имя индекса в плане нельзя: при `enable_seqscan=off`
        PostgreSQL всё равно возьмёт его, но условие уйдёт в `Filter` вместо
        `Index Cond` — то есть тест проходил бы и на разошедшихся выражениях.
        """
        factories.CatalogPositionFactory.create(
            standard_job_title="Работа", normalized_job_title=norm("Работа"), unit_id=None
        )
        db_session.flush()
        db_session.execute(sa.text("SET LOCAL enable_seqscan = off"))

        plan = "\n".join(
            row[0]
            for row in db_session.execute(
                sa.text(
                    "EXPLAIN SELECT id FROM catalog_positions "
                    "WHERE "
                    + str(
                        norm_hash(CatalogPosition.normalized_job_title).compile(
                            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
                        )
                    )
                    + " = :h AND COALESCE(unit_id, -1) = -1"
                ),
                {"h": hashlib.sha256(norm("Работа").encode("utf-8")).digest()},
            ).all()
        )
        assert "uq_catalog_positions_norm_hash_unit" in plan
        index_cond = next((line for line in plan.splitlines() if "Index Cond" in line), "")
        assert "sha256" in index_cond, plan


# ---------------------------------------------------------------------------
#  Ветка 2: точное совпадение
# ---------------------------------------------------------------------------

class TestExactBranch:
    def test_exact_match_binds_and_writes_cache(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        m2 = unit_id(db_session, "M2")
        target = factories.CatalogPositionFactory.create(
            standard_job_title="Устройство стяжки",
            normalized_job_title=norm("Устройство стяжки"),
            unit_id=m2,
            kind=CatalogKind.POSITION.value,
        )
        db_session.flush()

        _outcome, match = import_and_match(
            db_session, resolver, contract, [position(job_title="устройство стяжек", unit="м2")]
        )

        assert match.counters.matched_exact == 1
        assert match.counters.positions_total == 1
        item = db_session.execute(sa.select(PositionItem)).scalar_one()
        assert item.catalog_position_id == target.id

        cached = db_session.get(MatchingCache, cache_key(target.normalized_job_title, "M2"))
        assert cached is not None
        assert cached.source == MatchSource.auto.value
        assert cached.expires_at is not None
        assert cached.catalog_position_id == target.id
        assert cached.norm_version == NORM_VERSION
        assert cached.unit_text == "м2"

    def test_different_unit_is_not_a_match(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        factories.CatalogPositionFactory.create(
            standard_job_title="Устройство стяжки",
            normalized_job_title=norm("Устройство стяжки"),
            unit_id=unit_id(db_session, "M2"),
            kind=CatalogKind.POSITION.value,
        )
        db_session.flush()

        _outcome, match = import_and_match(
            db_session, resolver, contract, [position(job_title="Устройство стяжки", unit="м3")]
        )

        assert match.counters.matched_exact == 0
        assert match.counters.to_review == 1

    def test_non_position_kinds_are_not_matched_by_branch_two(
        self, db_session, factories, resolver
    ):
        """Ветка 2 ищет только среди POSITION; HEADER подхватит ветка 3."""
        contract = factories.ContractFactory.create()
        header = factories.CatalogPositionFactory.create(
            standard_job_title="Общестроительные работы",
            normalized_job_title=norm("Общестроительные работы"),
            unit_id=None,
            kind=CatalogKind.HEADER.value,
        )
        db_session.flush()

        _outcome, match = import_and_match(
            db_session, resolver, contract, [position(job_title="Общестроительные работы")]
        )

        assert match.counters.matched_nonposition == 1
        assert match.counters.to_review == 0
        item = db_session.execute(sa.select(PositionItem)).scalar_one()
        assert item.catalog_position_id == header.id
        # На HEADER кэш пишется (§5): следующая загрузка попадёт в ветку 1.
        assert db_session.get(MatchingCache, cache_key(header.normalized_job_title, "")) is not None


# ---------------------------------------------------------------------------
#  Ветка 1: кэш
# ---------------------------------------------------------------------------

class TestCacheBranch:
    def test_second_import_hits_the_cache(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        factories.CatalogPositionFactory.create(
            standard_job_title="Устройство стяжки",
            normalized_job_title=norm("Устройство стяжки"),
            unit_id=unit_id(db_session, "M2"),
            kind=CatalogKind.POSITION.value,
        )
        db_session.flush()
        rows = [position(job_title="Устройство стяжки", unit="м2")]

        _o1, first = import_and_match(db_session, resolver, contract, rows)
        _o2, second = import_and_match(db_session, resolver, contract, rows, amendment_no=1)

        assert (first.counters.matched_exact, first.counters.matched_cache) == (1, 0)
        assert (second.counters.matched_exact, second.counters.matched_cache) == (0, 1)

    def test_hit_extends_auto_ttl(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        factories.CatalogPositionFactory.create(
            standard_job_title="Работа",
            normalized_job_title=norm("Работа"),
            unit_id=None,
            kind=CatalogKind.POSITION.value,
        )
        db_session.flush()
        rows = [position(job_title="Работа")]
        start = utcnow_aware()

        import_and_match(db_session, resolver, contract, rows, now=start)
        key = cache_key(norm("Работа"), "")
        first_expiry = db_session.get(MatchingCache, key).expires_at

        later = start + timedelta(days=10)
        import_and_match(db_session, resolver, contract, rows, now=later, amendment_no=1)

        db_session.expire_all()
        assert db_session.get(MatchingCache, key).expires_at > first_expiry

    def test_expired_auto_entry_is_a_miss_and_gets_refreshed(
        self, db_session, factories, resolver
    ):
        contract = factories.ContractFactory.create()
        target = factories.CatalogPositionFactory.create(
            standard_job_title="Работа",
            normalized_job_title=norm("Работа"),
            unit_id=None,
            kind=CatalogKind.POSITION.value,
        )
        db_session.flush()
        rows = [position(job_title="Работа")]
        start = utcnow_aware()

        import_and_match(db_session, resolver, contract, rows, now=start)

        much_later = start + timedelta(days=AUTO_CACHE_TTL_DAYS + 1)
        _o, second = import_and_match(
            db_session, resolver, contract, rows, now=much_later, amendment_no=1
        )

        # Истёкшая запись — промах ветки 1, работу делает ветка 2 и освежает кэш.
        assert (second.counters.matched_cache, second.counters.matched_exact) == (0, 1)
        db_session.expire_all()
        refreshed = db_session.get(MatchingCache, cache_key(norm("Работа"), ""))
        assert refreshed.expires_at > much_later
        assert refreshed.catalog_position_id == target.id

    def test_cache_of_another_norm_version_is_ignored(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        other = factories.CatalogPositionFactory.create(
            standard_job_title="Совсем другая работа",
            normalized_job_title=norm("Совсем другая работа"),
            kind=CatalogKind.POSITION.value,
        )
        db_session.flush()
        # Запись с ЧУЖОЙ norm_version по нашему ключу — матчинг обязан её не увидеть.
        db_session.add(
            MatchingCache(
                cache_key=cache_key(norm("Работа"), ""),
                norm_version=NORM_VERSION + 1,
                job_title_text="Работа",
                unit_text=None,
                catalog_position_id=other.id,
                source=MatchSource.auto.value,
                expires_at=utcnow_aware() + timedelta(days=30),
            )
        )
        db_session.flush()

        _o, match = import_and_match(db_session, resolver, contract, [position(job_title="Работа")])

        assert match.counters.matched_cache == 0
        assert match.counters.to_review == 1


# ---------------------------------------------------------------------------
#  Ветка 3: get-or-create TO_REVIEW
# ---------------------------------------------------------------------------

class TestGetOrCreateBranch:
    def test_miss_creates_to_review_and_binds(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()

        _o, match = import_and_match(
            db_session, resolver, contract, [position(job_title="Невиданная работа", unit="м2")]
        )

        assert match.counters.to_review == 1
        item = db_session.execute(sa.select(PositionItem)).scalar_one()
        row = db_session.get(CatalogPosition, item.catalog_position_id)
        assert row.kind == CatalogKind.TO_REVIEW.value
        assert row.standard_job_title == "Невиданная работа"
        assert row.normalized_job_title == norm("Невиданная работа")
        assert row.unit_id == unit_id(db_session, "M2")

    def test_no_cache_entry_points_to_to_review(self, db_session, factories, resolver):
        """Инвариант §5 — условие безопасности DELETE при слиянии в Review."""
        contract = factories.ContractFactory.create()
        db_session.flush()

        import_and_match(
            db_session,
            resolver,
            contract,
            [
                position(job_title="Невиданная работа один", unit="м2"),
                position(job_title="Невиданная работа два", unit="шт"),
                position(job_title="Невиданная работа три"),
            ],
        )

        bad = db_session.execute(
            sa.select(sa.func.count())
            .select_from(MatchingCache)
            .join(CatalogPosition, CatalogPosition.id == MatchingCache.catalog_position_id)
            .where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
        ).scalar_one()
        assert bad == 0

    def test_two_misses_with_the_same_pair_give_one_row(self, db_session, factories, resolver):
        """Разные исходные строки, одна нормализованная пара → одна TO_REVIEW-строка."""
        contract = factories.ContractFactory.create()
        db_session.flush()

        _o, match = import_and_match(
            db_session,
            resolver,
            contract,
            [
                position(job_title="Монтаж кабеля силового", unit="м", number="1"),
                position(job_title="монтаж кабелей силовых", unit="м", number="2"),
            ],
        )

        assert match.counters.to_review == 2
        assert match.counters.positions_total == 2
        rows = db_session.execute(
            sa.select(CatalogPosition).where(
                CatalogPosition.kind == CatalogKind.TO_REVIEW.value
            )
        ).scalars().all()
        assert len(rows) == 1
        items = db_session.execute(sa.select(PositionItem)).scalars().all()
        assert {item.catalog_position_id for item in items} == {rows[0].id}

    def test_same_title_different_alias_of_one_unit_is_one_row(
        self, db_session, factories, resolver
    ):
        """«м2» и «кв.м» — один unit_id, значит одна идентичность работы."""
        contract = factories.ContractFactory.create()
        db_session.flush()

        import_and_match(
            db_session,
            resolver,
            contract,
            [
                position(job_title="Штукатурка стен", unit="м2", number="1"),
                position(job_title="Штукатурка стен", unit="кв.м", number="2"),
            ],
        )

        rows = db_session.execute(sa.select(CatalogPosition)).scalars().all()
        assert len(rows) == 1

    def test_existing_to_review_row_is_reused(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        existing = factories.CatalogPositionFactory.create(
            standard_job_title="Невиданная работа",
            normalized_job_title=norm("Невиданная работа"),
            unit_id=None,
            kind=CatalogKind.TO_REVIEW.value,
        )
        db_session.flush()

        _o, match = import_and_match(
            db_session, resolver, contract, [position(job_title="Невиданная работа")]
        )

        assert match.counters.to_review == 1
        item = db_session.execute(sa.select(PositionItem)).scalar_one()
        assert item.catalog_position_id == existing.id

    def test_batch_larger_than_the_prepare_threshold(self, db_session, factories, resolver):
        """Регрессия: арбитр ON CONFLICT должен быть литералом, а не параметром.

        psycopg3 готовит повторяющийся запрос после `prepare_threshold = 5`, и у
        подготовленного плана параметризованный `coalesce(unit_id, $N)` перестаёт
        совпадать с выражением индекса. Партия из 12 промахов проходит этот порог;
        на партии из 1–5 ошибка НЕ воспроизводится, поэтому размер здесь
        существенный.
        """
        contract = factories.ContractFactory.create()
        db_session.flush()
        rows = [
            position(job_title=f"Небывалая работа номер {i}", unit="м2", number=str(i))
            for i in range(12)
        ]

        _o, match = import_and_match(db_session, resolver, contract, rows)

        assert match.counters.to_review == 12
        created = db_session.execute(
            sa.select(sa.func.count())
            .select_from(CatalogPosition)
            .where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
        ).scalar_one()
        assert created == 12

    def test_a_plain_btree_index_rejects_this_title(self, db_session):
        """Предпосылка остальных тестов: значение действительно не индексируется.

        Проверка держит фикстуру честной: если `LONG_TITLE` когда-нибудь станет
        сжимаемым или коротким, упадёт этот тест, а не молча обесценятся три
        следующих. Ровно такой индекс — `ix_catalog_positions_standard_job_title`
        из миграции 0002 — и ронял импорт.
        """
        with pytest.raises(sa.exc.OperationalError) as excinfo, db_session.begin_nested():
            db_session.execute(sa.text("CREATE TEMP TABLE t_btree_limit (x text)"))
            db_session.execute(sa.text("CREATE INDEX ON t_btree_limit (x)"))
            db_session.execute(
                sa.text("INSERT INTO t_btree_limit (x) VALUES (:x)"), {"x": LONG_TITLE}
            )
        # Текст сообщения зависит от того, помогло ли сжатие индексного кортежа:
        # «index row size … exceeds btree version 4 maximum 2704» либо «index row
        # requires … bytes, maximum size is 8191». Класс ошибки один и тот же.
        assert isinstance(excinfo.value.orig, psycopg.errors.ProgramLimitExceeded)

    def test_long_title_beyond_the_btree_limit_is_matched(self, db_session, factories, resolver):
        """Регрессия: название длиннее предела btree не должно ронять импорт.

        В реальном файле фазы 0 нашлось наименование на 5077 символов (9260 байт):
        в ячейку попала целая спецификация. Обычный btree индексирует значения не
        длиннее 2704 байт, поэтому `INSERT` в каталог падал
        `ProgramLimitExceeded`, и смета не сохранялась целиком. Идентичность
        работы осталась полной парой (нормализованное название, единица) — в
        индексе от названия лежит `sha256`, поэтому длина ему безразлична.
        """
        contract = factories.ContractFactory.create()
        db_session.flush()
        title = LONG_TITLE
        assert len(title.encode("utf-8")) > 9000, "название должно превышать предел btree"

        _o, match = import_and_match(
            db_session, resolver, contract, [position(job_title=title, unit="м2")]
        )

        assert match.counters.to_review == 1
        row = db_session.execute(
            sa.select(CatalogPosition).where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
        ).scalar_one()
        # Ни название, ни его нормализованная форма не обрезаются.
        assert row.standard_job_title == title
        assert row.normalized_job_title == norm(title)

    def test_repeated_long_title_reuses_the_same_row(self, db_session, factories, resolver):
        """Повторный get-or-create длинного названия даёт ту же строку, не вторую.

        Счётчик остаётся `to_review`: кэш на TO_REVIEW не пишется (инвариант §5), а
        ветка 2 ищет только среди `POSITION`, поэтому вторая загрузка снова идёт
        ветвью 3 — и обязана найти существующую строку, а не создать вторую.
        """
        contract = factories.ContractFactory.create()
        db_session.flush()

        _o1, first = import_and_match(
            db_session, resolver, contract, [position(job_title=LONG_TITLE, unit="м2")]
        )
        created = db_session.execute(sa.select(CatalogPosition)).scalar_one()

        _o2, second = import_and_match(
            db_session,
            resolver,
            contract,
            [position(job_title=LONG_TITLE, unit="м2")],
            amendment_no=1,
        )

        assert first.counters.to_review == 1
        assert second.counters.to_review == 1
        rows = db_session.execute(sa.select(CatalogPosition)).scalars().all()
        assert [row.id for row in rows] == [created.id]
        items = db_session.execute(sa.select(PositionItem)).scalars().all()
        assert {item.catalog_position_id for item in items} == {created.id}

    def test_long_titles_differing_only_at_the_end_are_two_rows(
        self, db_session, factories, resolver
    ):
        """Хэш считается по ВСЕЙ строке, а не по префиксу.

        Обрезка нормализованного названия склеила бы две разные спецификации с
        одинаковым началом в одну каталожную строку — то есть в одну расценку.
        """
        contract = factories.ContractFactory.create()
        db_session.flush()

        import_and_match(
            db_session,
            resolver,
            contract,
            [
                position(job_title=LONG_TITLE + " вариант первый", unit="м2", number="1"),
                position(job_title=LONG_TITLE + " вариант второй", unit="м2", number="2"),
            ],
        )

        rows = db_session.execute(sa.select(CatalogPosition)).scalars().all()
        assert len(rows) == 2

    def test_long_title_with_different_units_gives_two_rows(
        self, db_session, factories, resolver
    ):
        """Единица входит в идентичность и на длинных названиях тоже (§4)."""
        contract = factories.ContractFactory.create()
        db_session.flush()

        import_and_match(
            db_session,
            resolver,
            contract,
            [
                position(job_title=LONG_TITLE, unit="м2", number="1"),
                position(job_title=LONG_TITLE, unit="м3", number="2"),
            ],
        )

        rows = db_session.execute(sa.select(CatalogPosition)).scalars().all()
        assert len(rows) == 2
        assert {row.unit_id for row in rows} == {
            unit_id(db_session, "M2"),
            unit_id(db_session, "M3"),
        }

    def test_trash_row_is_bound_as_nonposition(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        trash = factories.CatalogPositionFactory.create(
            standard_job_title="Итого по разделу",
            normalized_job_title=norm("Итого по разделу"),
            unit_id=None,
            kind=CatalogKind.TRASH.value,
        )
        db_session.flush()

        _o, match = import_and_match(
            db_session, resolver, contract, [position(job_title="Итого по разделу")]
        )

        assert match.counters.matched_nonposition == 1
        item = db_session.execute(sa.select(PositionItem)).scalar_one()
        assert item.catalog_position_id == trash.id


# ---------------------------------------------------------------------------
#  Счётчики и границы каскада
# ---------------------------------------------------------------------------

class TestCounters:
    def test_chapters_are_excluded_from_the_cascade(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()

        _o, match = import_and_match(
            db_session,
            resolver,
            contract,
            [
                position(job_title="Раздел 1", is_chapter=True, chapter_number="1"),
                position(job_title="Работа", unit="м2", number="2"),
            ],
        )

        assert match.counters.positions_total == 1
        chapter = db_session.execute(
            sa.select(PositionItem).where(PositionItem.is_chapter.is_(True))
        ).scalar_one()
        assert chapter.catalog_position_id is None

    def test_counters_sum_to_positions_total(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        factories.CatalogPositionFactory.create(
            standard_job_title="Известная работа",
            normalized_job_title=norm("Известная работа"),
            unit_id=None,
            kind=CatalogKind.POSITION.value,
        )
        factories.CatalogPositionFactory.create(
            standard_job_title="Заголовок раздела",
            normalized_job_title=norm("Заголовок раздела"),
            unit_id=None,
            kind=CatalogKind.HEADER.value,
        )
        db_session.flush()

        _o, match = import_and_match(
            db_session,
            resolver,
            contract,
            [
                position(job_title="Известная работа", number="1"),
                position(job_title="Заголовок раздела", number="2"),
                position(job_title="Никому не известная работа", number="3"),
            ],
        )

        c = match.counters
        assert c.positions_total == 3
        assert c.matched_cache + c.matched_exact + c.matched_nonposition + c.to_review == 3
        assert (c.matched_exact, c.matched_nonposition, c.to_review) == (1, 1, 1)

    def test_title_that_normalizes_to_nothing_is_not_admitted(
        self, db_session, factories, resolver
    ):
        contract = factories.ContractFactory.create()
        db_session.flush()

        _o, match = import_and_match(
            db_session,
            resolver,
            contract,
            [position(job_title="---", unit="шт", number="1"), position(job_title="Работа", number="2")],
        )

        assert match.counters.positions_total == 1
        assert any("нормализации" in w for w in match.warnings)

    def test_empty_position_list_is_a_noop(self, db_session):
        assert match_positions(db_session, []).counters.positions_total == 0


# ---------------------------------------------------------------------------
#  Ручные решения Review (§5) — сервис фазы 4, эндпоинты в фазе 5
# ---------------------------------------------------------------------------

class TestReviewMerge:
    def _to_review_with_positions(self, db_session, factories, resolver, contract):
        import_and_match(
            db_session, resolver, contract, [position(job_title="Невиданная работа", unit="м2")]
        )
        row = db_session.execute(
            sa.select(CatalogPosition).where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
        ).scalar_one()
        return row

    def test_merge_moves_positions_deletes_row_and_leaves_manual_cache(
        self, db_session, factories, resolver
    ):
        contract = factories.ContractFactory.create()
        db_session.flush()
        to_review = self._to_review_with_positions(db_session, factories, resolver, contract)
        to_review_id, normalized, unit = (
            to_review.id,
            to_review.normalized_job_title,
            to_review.unit_id,
        )
        target = factories.CatalogPositionFactory.create(
            standard_job_title="Стяжка",
            normalized_job_title=norm("Стяжка"),
            unit_id=unit,
            kind=CatalogKind.POSITION.value,
        )
        db_session.flush()

        moved = merge_into_position(db_session, to_review_id=to_review_id, target_id=target.id)

        assert moved == 1
        assert db_session.get(CatalogPosition, to_review_id) is None
        item = db_session.execute(sa.select(PositionItem)).scalar_one()
        assert item.catalog_position_id == target.id

        cached = db_session.get(MatchingCache, cache_key(normalized, "M2"))
        assert cached.source == MatchSource.manual.value
        assert cached.expires_at is None
        assert cached.catalog_position_id == target.id

    def test_merge_leaves_no_cache_entry_on_to_review(self, db_session, factories, resolver):
        """§9.4: слияние удаляет строку и не оставляет кэш-записей на TO_REVIEW."""
        contract = factories.ContractFactory.create()
        db_session.flush()
        to_review = self._to_review_with_positions(db_session, factories, resolver, contract)
        target = factories.CatalogPositionFactory.create(
            standard_job_title="Стяжка",
            normalized_job_title=norm("Стяжка"),
            unit_id=to_review.unit_id,
            kind=CatalogKind.POSITION.value,
        )
        db_session.flush()

        merge_into_position(db_session, to_review_id=to_review.id, target_id=target.id)

        bad = db_session.execute(
            sa.select(sa.func.count())
            .select_from(MatchingCache)
            .join(CatalogPosition, CatalogPosition.id == MatchingCache.catalog_position_id)
            .where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
        ).scalar_one()
        assert bad == 0

    def test_delete_frees_the_normalized_pair(self, db_session, factories, resolver):
        """Удаление обязательно: иначе строка перехватывала бы будущий get-or-create."""
        contract = factories.ContractFactory.create()
        db_session.flush()
        to_review = self._to_review_with_positions(db_session, factories, resolver, contract)
        target = factories.CatalogPositionFactory.create(
            standard_job_title="Стяжка",
            normalized_job_title=norm("Стяжка"),
            unit_id=to_review.unit_id,
            kind=CatalogKind.POSITION.value,
        )
        db_session.flush()
        merge_into_position(db_session, to_review_id=to_review.id, target_id=target.id)

        _o, match = import_and_match(
            db_session,
            resolver,
            contract,
            [position(job_title="Невиданная работа", unit="м2")],
            amendment_no=1,
        )

        # Ручное решение сработало как hit ветки 1.
        assert match.counters.matched_cache == 1
        item = db_session.execute(
            sa.select(PositionItem).order_by(PositionItem.id.desc()).limit(1)
        ).scalar_one()
        assert item.catalog_position_id == target.id

    def test_merge_rejects_wrong_kinds(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        to_review = self._to_review_with_positions(db_session, factories, resolver, contract)
        header = factories.CatalogPositionFactory.create(
            standard_job_title="Заголовок",
            normalized_job_title=norm("Заголовок"),
            kind=CatalogKind.HEADER.value,
        )
        db_session.flush()

        with pytest.raises(ReviewError, match="kind=HEADER"):
            merge_into_position(db_session, to_review_id=to_review.id, target_id=header.id)

    def test_merge_with_itself_is_rejected(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        to_review = self._to_review_with_positions(db_session, factories, resolver, contract)

        with pytest.raises(ReviewError, match="с собой"):
            merge_into_position(db_session, to_review_id=to_review.id, target_id=to_review.id)

    def test_merge_of_missing_row_is_rejected(self, db_session, factories):
        with pytest.raises(ReviewError, match="не найдена"):
            merge_into_position(db_session, to_review_id=10**9, target_id=10**9 + 1)


class TestReviewSetKind:
    def test_approve_as_position(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        import_and_match(
            db_session, resolver, contract, [position(job_title="Невиданная работа", unit="м2")]
        )
        row = db_session.execute(
            sa.select(CatalogPosition).where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
        ).scalar_one()

        set_kind(db_session, to_review_id=row.id, kind=CatalogKind.POSITION.value)

        db_session.expire_all()
        assert db_session.get(CatalogPosition, row.id).kind == CatalogKind.POSITION.value
        cached = db_session.get(MatchingCache, cache_key(row.normalized_job_title, "M2"))
        assert (cached.source, cached.expires_at) == (MatchSource.manual.value, None)

    @pytest.mark.parametrize("kind", [CatalogKind.HEADER.value, CatalogKind.TRASH.value])
    def test_mark_header_or_trash(self, db_session, factories, resolver, kind):
        contract = factories.ContractFactory.create()
        db_session.flush()
        import_and_match(db_session, resolver, contract, [position(job_title="Итого по разделу")])
        row = db_session.execute(
            sa.select(CatalogPosition).where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
        ).scalar_one()

        set_kind(db_session, to_review_id=row.id, kind=kind)

        db_session.expire_all()
        assert db_session.get(CatalogPosition, row.id).kind == kind

    def test_lot_header_cannot_be_set_manually(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        import_and_match(db_session, resolver, contract, [position(job_title="Работа")])
        row = db_session.execute(
            sa.select(CatalogPosition).where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
        ).scalar_one()

        with pytest.raises(ReviewError, match="Недопустимый kind"):
            set_kind(db_session, to_review_id=row.id, kind=CatalogKind.LOT_HEADER.value)


# ---------------------------------------------------------------------------
#  §9.4: ручное решение переживает истечение auto-TTL
# ---------------------------------------------------------------------------

class TestManualDecisionOutlivesAutoTtl:
    def test_manual_cache_still_matches_after_ttl_window(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()

        with freeze_time("2026-01-10 12:00:00"):
            import_and_match(
                db_session, resolver, contract, [position(job_title="Невиданная работа", unit="м2")]
            )
            to_review = db_session.execute(
                sa.select(CatalogPosition).where(
                    CatalogPosition.kind == CatalogKind.TO_REVIEW.value
                )
            ).scalar_one()
            target = factories.CatalogPositionFactory.create(
                standard_job_title="Стяжка",
                normalized_job_title=norm("Стяжка"),
                unit_id=to_review.unit_id,
                kind=CatalogKind.POSITION.value,
            )
            db_session.flush()
            merge_into_position(db_session, to_review_id=to_review.id, target_id=target.id)

        # Спустя вдвое больше, чем TTL авто-записей.
        with freeze_time("2026-04-10 12:00:00"):
            _o, match = import_and_match(
                db_session,
                resolver,
                contract,
                [position(job_title="Невиданная работа", unit="м2")],
                amendment_no=1,
            )

        assert match.counters.matched_cache == 1
        assert match.counters.to_review == 0
        item = db_session.execute(
            sa.select(PositionItem).order_by(PositionItem.id.desc()).limit(1)
        ).scalar_one()
        assert item.catalog_position_id == target.id
