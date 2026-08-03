"""Сериализация ручных решений Review (AGENTS.md §5, «Ручной матчинг»).

Два оператора разбирают одну очередь. Решение читает состояние строки и потом
меняет его, поэтому источник блокируется `FOR UPDATE` на всё решение: иначе
второй оператор проверяет `kind` по данным, которые к моменту его записи уже
устарели.

Тесты требуют НАСТОЯЩИХ транзакций (транзакционная `db_session` — один
savepoint), поэтому работают на `committing_session_factory`.

О том, что здесь чем доказывается. Часть гонок ловится и без блокировки — просто
позже и грубее: `UPDATE position_items` и `DELETE` упираются в замки самих строк,
а проверка «удалена ровно одна строка» не даёт закоммитить решение, применённое к
исчезнувшему источнику. Поэтому мера, которую даёт именно `FOR UPDATE`, проверяется
отдельным тестом с настоящей конкуренцией
(`test_lost_update_of_a_kind_decision_is_refused`) — он и падает, если блокировку
убрать. Остальные тесты фиксируют наблюдаемый результат, а не механизм.
"""
from __future__ import annotations

import threading
import time

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from models import CatalogKind, CatalogPosition, MatchingCache, MatchSource, PositionItem
from parser.sanitize_text import normalize_job_title_with_lemmatization
from services.estimate_import import import_estimate
from services.matching import cache_key, match_positions
from services.review import ReviewError, merge_into_position, set_kind
from services.unit_resolution import UnitResolver
from tests.payloads import payload_for, position

pytestmark = pytest.mark.integration

TO_REVIEW_TITLE = "Невиданная работа"


@pytest.fixture
def queue(committing_db, committing_factories, committing_session_factory):
    """Закоммиченное состояние: TO_REVIEW-строка с позицией и две цели-POSITION."""
    contract = committing_factories.ContractFactory.create()
    committing_db.flush()
    resolver = UnitResolver(committing_db)
    outcome = import_estimate(
        committing_db,
        contract=contract,
        amendment_no=None,
        data=payload_for(contract, [position(job_title=TO_REVIEW_TITLE, unit="м2")]),
        parser_version="1.0.0",
        import_job_id=None,
        replace=False,
        unit_resolver=resolver,
    )
    match_positions(committing_db, outcome.positions_to_match)

    first_target = committing_factories.CatalogPositionFactory.create(
        standard_job_title="Стяжка первая",
        normalized_job_title=normalize_job_title_with_lemmatization("Стяжка первая"),
        kind=CatalogKind.POSITION.value,
    )
    second_target = committing_factories.CatalogPositionFactory.create(
        standard_job_title="Стяжка вторая",
        normalized_job_title=normalize_job_title_with_lemmatization("Стяжка вторая"),
        kind=CatalogKind.POSITION.value,
    )
    committing_db.commit()

    to_review = committing_db.execute(
        sa.select(CatalogPosition).where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
    ).scalar_one()

    class Queue:
        session_factory = committing_session_factory
        to_review_id = to_review.id
        normalized = to_review.normalized_job_title
        first_id = first_target.id
        second_id = second_target.id

    return Queue()


def _wait_until_a_backend_blocks(session_factory, *, timeout: float = 15.0) -> bool:
    """Ждёт, пока какой-нибудь backend этой БД не встанет на ожидание замка.

    Так вторая транзакция гарантированно доходит до точки блокировки до того, как
    первая коммитится, — без произвольных пауз «на глазок».
    """
    deadline = time.monotonic() + timeout
    with session_factory() as probe:
        while time.monotonic() < deadline:
            blocked = probe.execute(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            probe.rollback()  # не держим свой снимок
            if blocked:
                return True
            time.sleep(0.05)
    return False


class TestLostUpdate:
    """Мера, которую даёт именно `FOR UPDATE`.

    Без блокировки второй оператор читает `kind = TO_REVIEW`, ждёт на записи,
    и после коммита первого его UPDATE применяется к уже размеченной строке:
    решение первого оператора теряется молча. С блокировкой второй ждёт ЧТЕНИЯ,
    затем видит настоящее состояние и получает отказ.
    """

    def test_lost_update_of_a_kind_decision_is_refused(self, queue):
        outcome: dict[str, str] = {}
        second_started = threading.Event()

        def second_operator():
            with queue.session_factory() as second:
                second_started.set()
                try:
                    set_kind(
                        second,
                        to_review_id=queue.to_review_id,
                        kind=CatalogKind.TRASH.value,
                    )
                    second.commit()
                    outcome["applied"] = "TRASH"
                except ReviewError as exc:
                    second.rollback()
                    outcome["refused"] = str(exc)
                except Exception as exc:  # pragma: no cover — диагностика
                    second.rollback()
                    outcome["error"] = f"{type(exc).__name__}: {exc}"

        with queue.session_factory() as first:
            set_kind(
                first, to_review_id=queue.to_review_id, kind=CatalogKind.POSITION.value
            )
            thread = threading.Thread(target=second_operator, daemon=True)
            thread.start()
            assert second_started.wait(timeout=10)
            assert _wait_until_a_backend_blocks(queue.session_factory), (
                "второй оператор не встал на ожидание замка — источник не блокируется"
            )
            first.commit()

        thread.join(timeout=20)
        assert not thread.is_alive()

        assert "applied" not in outcome, (
            "решение второго оператора применилось поверх первого — потерянное обновление"
        )
        assert "kind=POSITION" in outcome.get("refused", "")

        with queue.session_factory() as check:
            assert (
                check.get(CatalogPosition, queue.to_review_id).kind
                == CatalogKind.POSITION.value
            )


class TestMergeRaceOutcome:
    """Наблюдаемый результат гонки слияний — независимо от того, чем он достигнут."""

    def test_second_merge_cannot_proceed_while_the_first_is_open(self, queue):
        with queue.session_factory() as first:
            merge_into_position(
                first, to_review_id=queue.to_review_id, target_id=queue.first_id
            )
            # Не коммитим: решение первого оператора ещё открыто.

            with queue.session_factory() as second:
                # Иначе второй ждал бы бесконечно и тест бы завис.
                second.execute(sa.text("SET LOCAL lock_timeout = '250ms'"))
                with pytest.raises(OperationalError) as exc:
                    merge_into_position(
                        second, to_review_id=queue.to_review_id, target_id=queue.second_id
                    )
                assert "lock" in str(exc.value).lower()
                second.rollback()

            first.rollback()

    def test_loser_gets_a_clear_refusal(self, queue):
        with queue.session_factory() as first:
            merge_into_position(
                first, to_review_id=queue.to_review_id, target_id=queue.first_id
            )
            first.commit()

        with queue.session_factory() as second, pytest.raises(ReviewError, match="не найдена"):
            merge_into_position(
                second, to_review_id=queue.to_review_id, target_id=queue.second_id
            )

    def test_only_the_winner_owns_positions_and_cache(self, queue):
        with queue.session_factory() as first:
            merge_into_position(
                first, to_review_id=queue.to_review_id, target_id=queue.first_id
            )
            first.commit()

        with queue.session_factory() as second:
            try:
                merge_into_position(
                    second, to_review_id=queue.to_review_id, target_id=queue.second_id
                )
                second.commit()
            except ReviewError:
                second.rollback()

        with queue.session_factory() as check:
            items = check.execute(sa.select(PositionItem)).scalars().all()
            assert {item.catalog_position_id for item in items} == {queue.first_id}

            cached = check.get(MatchingCache, cache_key(queue.normalized, "M2"))
            assert cached.catalog_position_id == queue.first_id
            assert cached.source == MatchSource.manual.value
            assert cached.expires_at is None

            # Никаких записей кэша на TO_REVIEW и никаких осиротевших строк.
            assert (
                check.execute(
                    sa.select(sa.func.count())
                    .select_from(CatalogPosition)
                    .where(CatalogPosition.kind == CatalogKind.TO_REVIEW.value)
                ).scalar_one()
                == 0
            )

    def test_kind_change_makes_a_later_merge_inapplicable(self, queue):
        with queue.session_factory() as first:
            set_kind(first, to_review_id=queue.to_review_id, kind=CatalogKind.POSITION.value)
            first.commit()

        with queue.session_factory() as second, pytest.raises(ReviewError, match="kind=POSITION"):
            merge_into_position(
                second, to_review_id=queue.to_review_id, target_id=queue.first_id
            )
