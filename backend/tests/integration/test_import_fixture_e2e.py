"""Полный конвейер на обезличенном fixture: upload → … → done (DoD фазы 4).

Единственное место, где парсер работает по-настоящему, поэтому файл разбирается
ОДИН раз на модуль (~20 с, `docs/phase3-parser.md` §3), а оба сценария
пользуются одним `ParseResult`.

Числа fixture (2576 позиций, 746 разделов) — из `docs/phase3-parser.md`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from models import (
    CatalogKind,
    CatalogPosition,
    Estimate,
    EstimateRawData,
    ImportJobStatus,
    Lot,
    PositionItem,
    Proposal,
)
from parser import parse_estimate
from services import import_pipeline
from services.review import set_kind
from services.unit_resolution import UnitResolver

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "gp_estimate_fixture.xlsx"

#: Замеры фазы 3 на этом же файле.
FIXTURE_POSITIONS = 2576
FIXTURE_CHAPTERS = 746
FIXTURE_WORK_ROWS = FIXTURE_POSITIONS - FIXTURE_CHAPTERS


@pytest.fixture(scope="module")
def parsed_fixture():
    """Разбор fixture — один раз на модуль: это самая дорогая операция в тестах."""
    if not FIXTURE.is_file():
        pytest.skip(f"Нет {FIXTURE}")
    return parse_estimate(str(FIXTURE))


@pytest.fixture
def stub_with_fixture(monkeypatch, parsed_fixture):
    """Пайплайн получает уже разобранный fixture вместо повторного чтения XLSX."""
    monkeypatch.setattr(import_pipeline, "parse_estimate", lambda _handle: parsed_fixture)
    return parsed_fixture


def upload(client, contract_id: int, content: bytes):
    response = client.post(
        "/api/v1/estimates/upload",
        files={"file": ("смета.xlsx", content, "application/octet-stream")},
        data={"contract_id": str(contract_id)},
    )
    assert response.status_code == 202, response.text
    return client.get(f"/api/v1/import-jobs/{response.json()['id']}").json()


def xlsx_stub(marker: str) -> bytes:
    """Байты-заглушка: настоящий разбор подменён, но эндпоинт проверяет ZIP-магию."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", marker)
    return buffer.getvalue()


class TestFullPipelineOnFixture:
    def test_upload_to_done(
        self, committing_client, committing_db, committing_factories, stub_with_fixture
    ):
        contract = committing_factories.ContractFactory.create()
        committing_db.commit()

        job = upload(committing_client, contract.id, xlsx_stub("first"))

        assert job["status"] == ImportJobStatus.done.value
        assert job["error_text"] is None
        assert job["estimate_id"] is not None

        counters = job["counters"]
        assert counters["positions_total"] == FIXTURE_WORK_ROWS
        assert (
            counters["matched_cache"]
            + counters["matched_exact"]
            + counters["matched_nonposition"]
            + counters["to_review"]
            == FIXTURE_WORK_ROWS
        )
        # Каталог пуст — вся смета уходит в очередь Review.
        assert counters["to_review"] == FIXTURE_WORK_ROWS

        committing_db.expire_all()
        estimate_id = job["estimate_id"]
        assert (
            committing_db.execute(
                sa.select(sa.func.count())
                .select_from(PositionItem)
                .join(Proposal, Proposal.id == PositionItem.proposal_id)
                .join(Lot, Lot.id == Proposal.lot_id)
                .where(Lot.estimate_id == estimate_id)
            ).scalar_one()
            == FIXTURE_POSITIONS
        )
        assert (
            committing_db.execute(
                sa.select(sa.func.count())
                .select_from(PositionItem)
                .join(Proposal, Proposal.id == PositionItem.proposal_id)
                .join(Lot, Lot.id == Proposal.lot_id)
                .where(Lot.estimate_id == estimate_id, PositionItem.is_chapter.is_(True))
            ).scalar_one()
            == FIXTURE_CHAPTERS
        )

        raw = committing_db.get(EstimateRawData, estimate_id)
        assert raw.parser_version == stub_with_fixture.parser_version
        assert raw.raw_data["lots"]["lot_1"]["proposals"]["contractor_1"]["contractor_items"]

        # Разделы к матчингу не допускаются (§5, шаг 4).
        unmatched_chapters = committing_db.execute(
            sa.select(sa.func.count())
            .select_from(PositionItem)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(
                Lot.estimate_id == estimate_id,
                PositionItem.is_chapter.is_(True),
                PositionItem.catalog_position_id.isnot(None),
            )
        ).scalar_one()
        assert unmatched_chapters == 0

    def test_second_upload_of_the_same_file_is_idempotent(
        self, committing_client, committing_db, committing_factories, stub_with_fixture
    ):
        contract = committing_factories.ContractFactory.create()
        committing_db.commit()
        content = xlsx_stub("same")
        first = upload(committing_client, contract.id, content)

        response = committing_client.post(
            "/api/v1/estimates/upload",
            files={"file": ("смета.xlsx", content, "application/octet-stream")},
            data={"contract_id": str(contract.id)},
        )

        assert response.status_code == 200
        assert response.json()["id"] == first["id"]
        committing_db.expire_all()
        assert (
            committing_db.execute(sa.select(sa.func.count()).select_from(Estimate)).scalar_one() == 1
        )


class TestMatchingMetric:
    """Метрика §10: после наполнения каталога те же строки матчатся ≥ 90 %.

    Загрузка идёт на НОВЫЙ договор — на старом сработала бы идемпотентность и
    вернулся бы прежний job (§10 требует именно контрольные договоры).
    """

    def test_metric_after_review_queue_is_processed(
        self, committing_client, committing_db, committing_factories, stub_with_fixture
    ):
        first_contract = committing_factories.ContractFactory.create()
        second_contract = committing_factories.ContractFactory.create()
        committing_db.commit()

        upload(committing_client, first_contract.id, xlsx_stub("first"))

        # Оператор разбирает очередь: всё утверждается как POSITION.
        committing_db.expire_all()
        resolver = UnitResolver(committing_db)
        queue = (
            committing_db.execute(
                sa.select(CatalogPosition.id).where(
                    CatalogPosition.kind == CatalogKind.TO_REVIEW.value
                )
            )
            .scalars()
            .all()
        )
        assert queue, "очередь Review не должна быть пустой после первой загрузки"
        for row_id in queue:
            set_kind(
                committing_db,
                to_review_id=row_id,
                kind=CatalogKind.POSITION.value,
                resolver=resolver,
            )
        committing_db.commit()

        job = upload(committing_client, second_contract.id, xlsx_stub("second"))

        counters = job["counters"]
        matched = counters["matched_cache"] + counters["matched_exact"]
        assert counters["positions_total"] == FIXTURE_WORK_ROWS
        assert matched / counters["positions_total"] >= 0.9
        # Ручные решения дают hit ветки 1, а не повторный точный поиск.
        assert counters["matched_cache"] == counters["positions_total"]
        assert counters["to_review"] == 0
