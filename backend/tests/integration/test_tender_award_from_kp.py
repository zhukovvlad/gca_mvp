"""Договор из КП победителя: команда и импорт копии (спека Б2 §2.5, §2.7, §1.7).

Тесты идут на сессиях с настоящими commit-ами: отказ через ключ откатывает
сессию, а пайплайн работает на двух собственных сессиях. Состояние «после
отказа» читается свежей сессией. Отказ проверяется кодом, статусом и
неизменённым состоянием (договоры, задания, файлы хранилища), а не подстрокой.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from crud import contracts as crud_contracts
from crud import references as crud_references
from crud import tender_awards as crud_awards
from crud import tenders as crud_tenders
from crud.common import DomainError
from crud.estimate_totals import estimate_total_including_vat
from models import (
    ContextMember,
    Contract,
    Contractor,
    Estimate,
    EstimateRawData,
    ImportJob,
    ImportJobStatus,
    Lot,
    ObjectModel,
    Offer,
    OfferPackage,
    PositionItem,
    Proposal,
    TenderAward,
    UserRole,
)
from parser.constants import (
    JSON_KEY_CONTRACTOR_INN,
    JSON_KEY_LOTS,
    JSON_KEY_PROPOSALS,
    JSON_KEY_TOTAL_COST_INCLUDING_VAT,
)
from services import import_pipeline
from services.category_resolution import CategoryResolver
from services.import_owners import contract_estimate_owner
from services.round_import import import_round, projection_for_inn
from services.unit_resolution import UnitResolver
from tests.integration.test_estimates_api import xlsx_bytes
from tests.integration.test_import_pipeline import fake_parse
from tests.payloads import position, proposal, round_payload, summary_line

pytestmark = pytest.mark.integration

STAGE_BYTES = xlsx_bytes("stage-one")
STAGE_FILENAME = "Этап 1 сводная.xlsx"

INN_A, INN_B, INN_C = "7700000001", "7700000002", "7700000003"

# Независимые литералы: эталон не вычисляется кодом, который проверяется.
STAGE_FILE_MISSING_CODE = "stage_file_missing"
NUMBER_TAKEN_TEXT = "Договор с таким номером уже есть."


def _summary(total: str) -> dict:
    return {JSON_KEY_TOTAL_COST_INCLUDING_VAT: summary_line("ИТОГО, руб. с учетом НДС", total)}


def _participants(*, drop: str | None = None, blank_inn_of: str | None = None) -> list[dict]:
    """Три участника с РАЗНЫМИ позициями и ценами: тест копии различает победителя
    (Б) и соседей только по ним."""
    def line(title, unit, price):
        return position(
            job_title=title, unit=unit, quantity=1, suggested_quantity=1,
            unit_cost_total=price, total_cost_total=price,
        )

    rows = {
        INN_A: ("ООО А", [line("Стяжка пола", "м2", "11")], "1100.00"),
        INN_B: ("ООО Б", [line("Стяжка пола", "м2", "20"), line("Монтаж кабеля", "м", "35")], "2000.00"),
        INN_C: ("ООО В", [line("Стяжка пола", "м2", "31"), line("Окраска стен", "м2", "7")], "3000.00"),
    }
    out = []
    for inn, (title, positions, total) in rows.items():
        if inn == drop:
            continue
        out.append(proposal(
            positions, title=title, inn="" if inn == blank_inn_of else inn, summary=_summary(total),
        ))
    return out


@pytest.fixture
def scene(committing_db, committing_factories, committing_session_factory, tmp_storage):
    """Тендер с одним (финальным) этапом, тремя участниками, заданием этапа `done`
    с реальным файлом в хранилище и сметами КП, созданными настоящим
    `import_round` под ключом этого задания. Победитель — Б (в середине списка).
    Класс тендера A, класс объекта B (текущий класс объекта — снимок договора).
    Отметка не ставится: её ставят тесты."""
    f = committing_factories
    db = committing_db
    tender = f.TenderFactory.create()
    rnd = f.TenderRoundFactory.create(tender=tender, stage_no=1)
    class_b = f.RateClassFactory.create()
    db.flush()
    db.get(ObjectModel, tender.object_id).rate_class_id = class_b.id
    key = tmp_storage.save(STAGE_BYTES)
    stage_job = ImportJob(
        round_id=rnd.id, filename=STAGE_FILENAME, file_key=key,
        file_sha256=hashlib.sha256(STAGE_BYTES).hexdigest(), status=ImportJobStatus.done.value,
        estimates_created=3,
    )
    db.add(stage_job)
    db.flush()
    payload = round_payload(_participants())
    import_round(
        db, tender_round=rnd, data=payload, parser_version="4.0.0", import_job_id=stage_job.id,
        replace=False, unit_resolver=UnitResolver(db), category_resolver=CategoryResolver.from_db(db),
    )
    user = f.UserFactory.create(role=UserRole.admin)
    db.commit()

    def offer_id(inn):
        return db.execute(
            sa.select(Offer.id).join(OfferPackage, OfferPackage.id == Offer.package_id)
            .join(Contractor, Contractor.id == OfferPackage.contractor_id)
            .where(Offer.round_id == rnd.id, Contractor.inn == inn)
        ).scalar_one()

    def contractor_id(inn):
        return db.execute(sa.select(Contractor.id).where(Contractor.inn == inn)).scalar_one()

    return SimpleNamespace(
        sf=committing_session_factory, db=db, f=f, storage=tmp_storage, payload=payload,
        tender_id=tender.id, tender_number=tender.tender_number, object_id=tender.object_id,
        tender_class_id=tender.rate_class_id, object_class_id=class_b.id, round_id=rnd.id,
        stage_job_id=stage_job.id, stage_key=key, user_id=user.id,
        offer_a=offer_id(INN_A), offer_b=offer_id(INN_B), offer_c=offer_id(INN_C),
        contractor_a=contractor_id(INN_A), contractor_b=contractor_id(INN_B),
        contractor_c=contractor_id(INN_C),
    )


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _award(s, offer=None) -> int:
    """Действующая отметка (по умолчанию на КП победителя Б), один раз на тест."""
    with s.sf() as db:
        existing = db.execute(sa.select(TenderAward.id).where(TenderAward.tender_id == s.tender_id)).scalar()
        if existing is not None:
            return existing
        crud_awards.award_winner(db, s.tender_id, offer_id=offer or s.offer_b, user_id=s.user_id)
    with s.sf() as db:
        return db.execute(sa.select(TenderAward.id).where(TenderAward.tender_id == s.tender_id)).scalar_one()


def _fields(number="ГП-КП-1", **overrides) -> crud_awards.ContractFromAwardFields:
    values = dict(
        contract_number=number, signed_date=dt.date(2026, 10, 1), title="Договор из КП", signer="Петров П.П.",
        total_amount=Decimal("1234.50"), notes="примечание", advance_pct=Decimal("30"), advance_note="аванс",
        bank_guarantee_pct=None, bank_guarantee_note=None, retention_pct=Decimal("5"), retention_note=None,
    )
    values.update(overrides)
    return crud_awards.ContractFromAwardFields(**values)


def _create(s, award_id=None, **field_overrides):
    award = award_id if award_id is not None else _award(s)
    with s.sf() as db:
        return crud_awards.create_contract_from_award(
            db, s.storage, s.tender_id, award, fields=_fields(**field_overrides),
        )


def _files(s) -> list[str]:
    root = s.storage.root
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def _state(s) -> tuple:
    with s.sf() as db:
        return (
            db.execute(sa.select(sa.func.count()).select_from(Contract)).scalar_one(),
            db.execute(sa.select(sa.func.count()).select_from(ImportJob)).scalar_one(),
            tuple(_files(s)),
        )


def _refused(s, code, status=409, award_id=None, **field_overrides) -> DomainError:
    """Отказ команды: код, статус и НЕИЗМЕНЁННОЕ состояние — ни договора, ни
    задания, ни лишнего файла в хранилище.

    Синхронный отказ случается ДО копии файла (шаги 1–4 спеки §2.5 раньше
    шага 5): `save` не вызывается вовсе. Одного счёта файлов мало — копия,
    сохранённая и тут же удалённая ключом-страховкой, его не меняет, и
    выпавшая проверка команды пряталась бы за ключом."""
    before = _state(s)
    saves: list[int] = []
    real_save = s.storage.save
    s.storage.save = lambda data: saves.append(1) or real_save(data)
    try:
        with pytest.raises(DomainError) as info:
            _create(s, award_id, **field_overrides)
    finally:
        del s.storage.save
    assert (info.value.status_code, info.value.code) == (status, code)
    assert saves == []
    assert _state(s) == before
    return info.value


def _run(s, job_id, payload=None):
    import_pipeline.run_import_job(
        job_id, session_factory=s.sf, storage=s.storage, parse=fake_parse(payload or s.payload),
    )
    with s.sf() as db:
        return db.get(ImportJob, job_id)


def _copy_positions(s, contract_id) -> list[tuple[str, Decimal]]:
    with s.sf() as db:
        rows = db.execute(
            sa.select(PositionItem.job_title_in_proposal, PositionItem.unit_cost_total)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id).join(Estimate, Estimate.id == Lot.estimate_id)
            .where(Estimate.contract_id == contract_id).order_by(PositionItem.id)
        ).all()
    return [(title, price) for title, price in rows]


def _contract_estimates(s, contract_id) -> list:
    with s.sf() as db:
        return db.execute(sa.select(Estimate).where(Estimate.contract_id == contract_id)).scalars().all()


WINNER_POSITIONS = [("Стяжка пола", Decimal("20")), ("Монтаж кабеля", Decimal("35"))]


# ---------------------------------------------------------------------------
#  Команда: успех
# ---------------------------------------------------------------------------

class TestCreateContractFromAward:
    def test_creates_contract_and_copy_job(self, scene):
        award_id = _award(scene)
        files_before = _files(scene)

        card, job = _create(scene)

        with scene.sf() as db:
            contract = db.get(Contract, card["id"])
            assert (contract.object_id, contract.contractor_id, contract.tender_award_id) == (
                scene.object_id, scene.contractor_b, award_id,
            )
            assert contract.contract_number == "ГП-КП-1"
            assert contract.signed_date == dt.date(2026, 10, 1)
            assert (contract.title, contract.signer, contract.notes) == (
                "Договор из КП", "Петров П.П.", "примечание",
            )
            assert contract.total_amount == Decimal("1234.50")
            assert (contract.advance_pct, contract.advance_note) == (Decimal("30"), "аванс")
            assert contract.retention_pct == Decimal("5")
            stored = db.get(ImportJob, job.id)
            assert stored.contract_id == contract.id
            assert stored.amendment_no is None
            assert stored.round_id is None
            assert stored.source_award_id == award_id
            assert stored.status == ImportJobStatus.pending.value
            assert stored.filename == STAGE_FILENAME
            assert stored.file_sha256 == hashlib.sha256(STAGE_BYTES).hexdigest()
            assert stored.file_key != scene.stage_key
        assert card["id"] == contract.id and card["tender_basis"]["award_id"] == award_id
        assert card["estimate_origin"] == "no_estimate"
        # В хранилище ровно один новый файл, побайтно равный файлу этапа; старый на месте.
        new_files = set(_files(scene)) - set(files_before)
        assert new_files == {stored.file_key}
        assert scene.stage_key in _files(scene)
        with scene.storage.get(stored.file_key) as handle:
            assert handle.read() == STAGE_BYTES
        with scene.storage.get(scene.stage_key) as handle:
            assert handle.read() == STAGE_BYTES

    def test_contract_class_is_the_current_object_class_not_the_tender_class(self, scene):
        assert scene.tender_class_id != scene.object_class_id   # предусловие: классы разные
        card, _ = _create(scene)
        assert card["rate_class_id"] == scene.object_class_id
        with scene.sf() as db:
            assert db.get(Contract, card["id"]).rate_class_id == scene.object_class_id

    def test_text_fields_are_cleaned_like_create_contract(self, scene):
        card, _ = _create(
            scene, number="  ГП-КП-7  ", title="   ", signer="  Петров П.П.  ", notes="",
            advance_note="  аванс  ", retention_note=" ",
        )
        with scene.sf() as db:
            contract = db.get(Contract, card["id"])
            assert contract.contract_number == "ГП-КП-7"
            assert (contract.title, contract.signer, contract.notes) == (None, "Петров П.П.", None)
            assert (contract.advance_note, contract.retention_note) == ("аванс", None)

    def test_copy_sha256_is_recomputed_from_the_bytes_not_taken_from_the_stage_job(self, scene):
        # Спека §2.5 шаг 5: sha256 пересчитывается по сохранённым байтам. Запись
        # задания КП здесь нарочно неверна — копировать её значило бы унести ошибку.
        _award(scene)
        with scene.sf() as db:
            db.execute(sa.update(ImportJob).where(ImportJob.id == scene.stage_job_id).values(file_sha256="0" * 64))
            db.commit()
        _, job = _create(scene)
        with scene.sf() as db:
            assert db.get(ImportJob, job.id).file_sha256 == hashlib.sha256(STAGE_BYTES).hexdigest()

    def test_error_after_commit_keeps_the_copy_of_the_committed_job(self, scene, monkeypatch):
        # Обратная сторона шага 1 AGENTS.md §5: задание закоммичено — его файл
        # живёт, даже если ответ потом не собрался.
        _award(scene)
        files_before = set(_files(scene))

        def broken_card(db, contract_id):
            raise RuntimeError("карточка не собралась")

        monkeypatch.setattr(crud_contracts, "get_contract_dict", broken_card)
        with pytest.raises(RuntimeError):
            _create(scene)

        with scene.sf() as db:
            job = db.execute(sa.select(ImportJob).where(ImportJob.source_award_id.is_not(None))).scalar_one()
        assert set(_files(scene)) - files_before == {job.file_key}

    def test_background_task_is_not_started_by_the_command(self, scene):
        _, job = _create(scene)
        with scene.sf() as db:
            assert db.get(ImportJob, job.id).status == ImportJobStatus.pending.value
            assert db.execute(sa.select(sa.func.count()).select_from(Estimate).where(
                Estimate.contract_id.is_not(None))).scalar_one() == 0

    def test_stage_job_is_found_by_the_winner_estimate_not_by_current_round_job(self, scene):
        award_id = _award(scene)
        # Удалена КП другого участника этапа: набор смет неполон, у этапа «текущего
        # задания» больше нет, а файл КП победителя остаётся.
        with scene.sf() as db:
            db.execute(sa.delete(Estimate).where(Estimate.offer_id == scene.offer_a))
            db.commit()
            assert crud_tenders.current_round_job(db, scene.round_id) is None   # предусловие

        card, job = _create(scene, award_id)

        assert job.filename == STAGE_FILENAME
        with scene.sf() as db:
            assert db.get(Contract, card["id"]).tender_award_id == award_id


# ---------------------------------------------------------------------------
#  Команда: отказы ничего не создают
# ---------------------------------------------------------------------------

class TestCreateRefusals:
    def test_no_stage_job_for_the_winner_estimate(self, scene):
        _award(scene)
        with scene.sf() as db:
            db.execute(sa.update(Estimate).where(Estimate.offer_id == scene.offer_b).values(import_job_id=None))
            db.commit()
        _refused(scene, STAGE_FILE_MISSING_CODE)

    @pytest.mark.parametrize("status", [ImportJobStatus.error, ImportJobStatus.pending])
    def test_stage_job_is_not_done(self, scene, status):
        _award(scene)
        with scene.sf() as db:
            db.execute(sa.update(ImportJob).where(ImportJob.id == scene.stage_job_id).values(status=status.value))
            db.commit()
        _refused(scene, STAGE_FILE_MISSING_CODE)

    def test_stage_file_is_gone_from_storage(self, scene):
        _award(scene)
        assert scene.storage.delete(scene.stage_key)
        _refused(scene, STAGE_FILE_MISSING_CODE)

    def test_stage_job_key_is_not_a_storage_key(self, scene):
        # Ключ задания КП не проходит формат хранилища (`StorageKeyError`) —
        # файл этапа так же недоступен, отказ тот же.
        _award(scene)
        with scene.sf() as db:
            db.execute(sa.update(ImportJob).where(ImportJob.id == scene.stage_job_id).values(file_key="../чужой"))
            db.commit()
        _refused(scene, STAGE_FILE_MISSING_CODE)

    def test_blank_contract_number_is_422_like_create_contract(self, scene):
        _award(scene)
        error = _refused(scene, None, status=422, number="   ")
        assert error.detail == "Поле «Номер договора» не может быть пустым."

    def test_stage_file_missing_text_names_the_stage(self, scene):
        _award(scene)
        scene.storage.delete(scene.stage_key)
        error = _refused(scene, STAGE_FILE_MISSING_CODE)
        assert error.detail == (
            "Файл этапа 1 недоступен в хранилище — договор из КП создать нельзя. Заведите договор "
            "обычной формой и загрузите смету."
        )

    def test_object_class_is_null(self, scene):
        _award(scene)
        with scene.sf() as db:
            db.get(ObjectModel, scene.object_id).rate_class_id = None
            db.commit()
            title = db.get(ObjectModel, scene.object_id).title
        error = _refused(scene, None, status=422)
        assert error.detail == (
            f"У объекта «{title}» не задан класс, а класс договора обязателен: он фиксируется снимком на "
            "момент подписания и по нему сравниваются нормативы. Укажите rate_class_id в запросе либо "
            "задайте класс объекту."
        )

    def test_contract_number_is_taken(self, scene):
        _award(scene)
        scene.f.ContractFactory.create(contract_number="ГП-ЗАНЯТ")
        scene.db.commit()
        error = _refused(scene, None, number="ГП-ЗАНЯТ")
        assert error.detail == NUMBER_TAKEN_TEXT

    def test_award_is_not_active(self, scene):
        award_id = _award(scene)
        with scene.sf() as db:
            crud_awards.mark_not_concluded(
                db, scene.tender_id, award_id, not_concluded_on=dt.date(2026, 10, 2), note=None,
                user_id=scene.user_id,
            )
        _refused(scene, "award_not_active", award_id=award_id)

    def test_award_already_has_a_contract(self, scene):
        award_id = _award(scene)
        _create(scene, award_id)
        _refused(scene, "award_has_contract", award_id=award_id, number="ГП-КП-2")

    def test_unknown_award_is_404(self, scene):
        _award(scene)
        before = _state(scene)
        with pytest.raises(DomainError) as info:
            _create(scene, 987_654_321)
        assert info.value.status_code == 404
        assert _state(scene) == before


# ---------------------------------------------------------------------------
#  Команда: каждый запрет дважды (проверка и ключ), копия не остаётся
# ---------------------------------------------------------------------------

class TestKeysBackTheChecks:
    def test_number_taken_in_a_race_is_the_same_refusal_and_removes_the_new_file(self, scene, monkeypatch):
        _award(scene)
        scene.f.ContractFactory.create(contract_number="ГП-ГОНКА")
        scene.db.commit()
        sync = _refused(scene, None, number="ГП-ГОНКА")
        # Проверку номера выключаем: конфликт ловит ключ после сохранения копии.
        monkeypatch.setattr(crud_awards, "_refuse_if_number_taken", lambda db, number: None)
        files_before = _files(scene)
        before = _state(scene)

        with pytest.raises(DomainError) as info:
            _create(scene, number="ГП-ГОНКА")

        assert (info.value.status_code, info.value.code, info.value.detail) == (
            409, None, NUMBER_TAKEN_TEXT,
        )
        assert (sync.code, sync.detail) == (None, NUMBER_TAKEN_TEXT)
        assert _files(scene) == files_before
        assert _state(scene) == before

    def test_award_taken_by_a_contract_in_a_race_is_award_has_contract_and_removes_the_new_file(
        self, scene, monkeypatch,
    ):
        award_id = _award(scene)
        _create(scene, award_id)
        # Проверку «без договора» выключаем: ключ `uq_contracts_tender_award`.
        monkeypatch.setattr(crud_awards, "_refuse_if_has_contract", lambda db, award: None)
        before = _state(scene)

        with pytest.raises(DomainError) as info:
            _create(scene, award_id, number="ГП-КП-2")

        assert (info.value.status_code, info.value.code) == (409, "award_has_contract")
        assert _state(scene) == before

    def test_award_vanished_in_a_race_is_award_not_active_and_removes_the_new_file(self, scene, monkeypatch):
        award_id = _award(scene)
        real_lock = crud_awards._lock_open_award

        def stale_lock(db, tender_id, award_id_):
            award = db.get(TenderAward, award_id_)    # отметка уже в сессии команды
            with scene.sf() as other:                 # а другой сеанс её снял
                other.execute(sa.delete(TenderAward).where(TenderAward.id == award_id_))
                other.commit()
            return award

        monkeypatch.setattr(crud_awards, "_lock_open_award", stale_lock)
        assert real_lock is not stale_lock
        before = _state(scene)

        with pytest.raises(DomainError) as info:
            _create(scene, award_id)

        assert (info.value.status_code, info.value.code) == (409, "award_not_active")
        assert _state(scene) == before


# ---------------------------------------------------------------------------
#  Пайплайн: смета-копия
# ---------------------------------------------------------------------------

def _total(s, estimate_id):
    with s.sf() as db:
        return estimate_total_including_vat(db, estimate_id)


class TestCopyPipeline:
    def test_copy_carries_the_winner_positions_and_prices_among_three(self, scene):
        award_id = _award(scene)
        card, job = _create(scene)

        done = _run(scene, job.id)

        assert done.status == ImportJobStatus.done.value, done.error_text
        assert done.estimates_created == 1
        assert _copy_positions(scene, card["id"]) == WINNER_POSITIONS   # не А (11) и не В (31, 7)
        (estimate,) = _contract_estimates(scene, card["id"])
        assert (estimate.source_award_id, estimate.amendment_no, estimate.import_job_id) == (
            award_id, None, job.id,
        )
        with scene.sf() as db:
            winner_estimate = db.get(TenderAward, award_id).estimate_id
        assert _total(scene, estimate.id) == _total(scene, winner_estimate) == Decimal("2000.00")
        assert (done.positions_total, done.matched_cache + done.matched_exact + done.to_review) == (2, 2)

    def test_copy_job_carries_the_copy_warning(self, scene):
        _award(scene)
        card, job = _create(scene)

        done = _run(scene, job.id)

        assert (
            f"Смета — копия КП участника «ООО Б» этапа 1 тендера № {scene.tender_number} "
            f"(файл «{STAGE_FILENAME}»)"
        ) in done.warnings

    def test_copy_positions_are_members_of_contexts(self, scene):
        _award(scene)
        card, job = _create(scene)
        _run(scene, job.id)
        with scene.sf() as db:
            positions = db.execute(
                sa.select(PositionItem.id).join(Proposal, Proposal.id == PositionItem.proposal_id)
                .join(Lot, Lot.id == Proposal.lot_id).join(Estimate, Estimate.id == Lot.estimate_id)
                .where(Estimate.contract_id == card["id"])
            ).scalars().all()
            members = db.execute(
                sa.select(ContextMember.position_item_id).where(ContextMember.position_item_id.in_(positions))
            ).scalars().all()
        assert len(positions) == 2 and sorted(members) == sorted(positions)

    def test_parsed_data_is_the_whole_file_not_the_projection(self, scene):
        _award(scene)
        card, job = _create(scene)
        done = _run(scene, job.id)
        assert done.parsed_data == scene.payload
        # Разбор сметы-копии — проекция одного участника.
        (estimate,) = _contract_estimates(scene, card["id"])
        with scene.sf() as db:
            raw = db.get(EstimateRawData, estimate.id).raw_data
        inns = {
            block[JSON_KEY_CONTRACTOR_INN]
            for lot in raw[JSON_KEY_LOTS].values() for block in lot[JSON_KEY_PROPOSALS].values()
        }
        assert inns == {INN_B}

    def test_ordinary_owner_gives_the_same_columns_as_before(self, scene):
        contract = scene.f.ContractFactory.create()
        scene.db.commit()
        base = contract_estimate_owner(contract, None)
        assert base.estimate_columns() == {"contract_id": contract.id, "amendment_no": None}
        assert contract_estimate_owner(contract, 2).estimate_columns() == {
            "contract_id": contract.id, "amendment_no": 2,
        }
        assert base.source_award_id is None
        with_award = contract_estimate_owner(contract, None, source_award_id=77)
        assert with_award.estimate_columns() == {
            "contract_id": contract.id, "amendment_no": None, "source_award_id": 77,
        }


# ---------------------------------------------------------------------------
#  Опознание КП по ИНН блока файла (решение 13)
# ---------------------------------------------------------------------------

class TestRecognitionByFileInn:
    def test_inn_fixed_before_the_award(self, scene):
        with scene.sf() as db:
            crud_references.update_contractor(db, scene.contractor_b, inn="7700000099")
        _award(scene)
        card, job = _create(scene)

        done = _run(scene, job.id)

        assert done.status == ImportJobStatus.done.value, done.error_text
        assert _copy_positions(scene, card["id"]) == WINNER_POSITIONS
        assert any("ИНН подрядчика в файле («7700000002») не совпадает с карточкой" in w for w in done.warnings)

    def test_inn_fixed_after_the_award_before_the_contract(self, scene):
        _award(scene)
        with scene.sf() as db:
            crud_references.update_contractor(db, scene.contractor_b, inn="7700000099")
        card, job = _create(scene)

        done = _run(scene, job.id)

        assert done.status == ImportJobStatus.done.value, done.error_text
        assert _copy_positions(scene, card["id"]) == WINNER_POSITIONS
        assert any("ИНН подрядчика в файле («7700000002») не совпадает с карточкой" in w for w in done.warnings)

    def test_inns_of_winner_and_neighbour_swapped_after_the_award(self, scene):
        _award(scene)
        with scene.sf() as db:    # обмен через промежуточное значение: уникальность ИНН
            crud_references.update_contractor(db, scene.contractor_b, inn="7700000555")
            crud_references.update_contractor(db, scene.contractor_c, inn=INN_B)
            crud_references.update_contractor(db, scene.contractor_b, inn=INN_C)
        card, job = _create(scene)

        done = _run(scene, job.id)

        assert done.status == ImportJobStatus.done.value, done.error_text
        # Цены победителя (20, 35), а не соседа с его прежним ИНН (31, 7).
        assert _copy_positions(scene, card["id"]) == WINNER_POSITIONS


# ---------------------------------------------------------------------------
#  Пайплайн: отказы
# ---------------------------------------------------------------------------

class TestCopyPipelineRefusals:
    def _assert_no_estimate(self, s, card_id, job):
        assert job.status == ImportJobStatus.error.value
        assert _contract_estimates(s, card_id) == []
        with s.sf() as db:
            assert crud_contracts.get_contract_dict(db, card_id)["estimate_origin"] == "no_estimate"

    def test_file_has_no_block_with_the_award_inn(self, scene):
        _award(scene)
        card, job = _create(scene)

        done = _run(scene, job.id, round_payload(_participants(drop=INN_B)))

        self._assert_no_estimate(scene, card["id"], done)
        assert done.error_text == "В файле этапа 1 нет КП участника «ООО Б» (ИНН 7700000002)"

    def test_split_refuses_with_its_own_text(self, scene):
        _award(scene)
        card, job = _create(scene)

        done = _run(scene, job.id, round_payload(_participants(blank_inn_of=INN_C)))

        self._assert_no_estimate(scene, card["id"], done)
        assert done.error_text == (
            "В лоте «lot_1» у блока подрядчика «ООО В» нет ИНН — участника раунда не по чему опознать. "
            "Файл раунда обязан нести ИНН в каждом блоке."
        )

    def test_contract_basis_differs_from_the_job_award(self, scene):
        award_id = _award(scene)
        card, job = _create(scene)
        with scene.sf() as db:    # подмена в базе: основание снято мимо команд
            db.execute(sa.update(Contract).where(Contract.id == card["id"]).values(tender_award_id=None))
            db.commit()

        done = _run(scene, job.id)

        assert done.status == ImportJobStatus.error.value
        assert done.error_text == (
            "Договор не опирается на отметку победителя, по которой создано задание, — смета-копия не "
            "создана. Загрузите смету в договор обычной формой."
        )
        assert _contract_estimates(scene, card["id"]) == []
        assert award_id is not None

    def test_contract_basis_is_another_award_of_the_same_parties(self, scene):
        first = _award(scene)
        with scene.sf() as db:    # прежняя отметка закрыта, ставится новая на того же участника
            crud_awards.mark_not_concluded(
                db, scene.tender_id, first, not_concluded_on=dt.date(2026, 10, 2), note=None,
                user_id=scene.user_id,
            )
            crud_awards.award_winner(db, scene.tender_id, offer_id=scene.offer_b, user_id=scene.user_id)
        with scene.sf() as db:
            second = db.execute(
                sa.select(TenderAward.id).where(TenderAward.tender_id == scene.tender_id,
                                                TenderAward.not_concluded_on.is_(None))
            ).scalar_one()
        assert second != first
        card, job = _create(scene, second)
        with scene.sf() as db:    # подмена в базе: задание ссылается на другую отметку
            db.execute(sa.update(ImportJob).where(ImportJob.id == job.id).values(source_award_id=first))
            db.commit()

        done = _run(scene, job.id)

        assert done.status == ImportJobStatus.error.value
        assert done.error_text == (
            "Договор не опирается на отметку победителя, по которой создано задание, — смета-копия не "
            "создана. Загрузите смету в договор обычной формой."
        )
        assert _contract_estimates(scene, card["id"]) == []

    def test_award_vanished_between_the_contract_and_the_award_reads(self, scene, monkeypatch):
        # Сессия B уже прочла договор (основание — отметка), и тут мимо команд
        # другой сеанс отвязывает договор и удаляет отметку. Договор в сессии B
        # всё ещё «опирается» на отметку, а самой отметки уже нет: отказ тем же
        # текстом основания, а не непредвиденной ошибкой.
        award_id = _award(scene)
        card, job = _create(scene)
        real = import_pipeline._award_copy_data

        def vanishing(db, contract, context, parsed):
            with scene.sf() as other:
                other.execute(sa.update(Contract).where(Contract.id == card["id"]).values(tender_award_id=None))
                other.execute(sa.delete(TenderAward).where(TenderAward.id == award_id))
                other.commit()
            assert contract.tender_award_id == award_id    # предусловие: сессия B видит старое основание
            return real(db, contract, context, parsed)

        monkeypatch.setattr(import_pipeline, "_award_copy_data", vanishing)

        done = _run(scene, job.id)

        assert done.status == ImportJobStatus.error.value
        assert done.error_text == (
            "Договор не опирается на отметку победителя, по которой создано задание, — смета-копия не "
            "создана. Загрузите смету в договор обычной формой."
        )
        assert _contract_estimates(scene, card["id"]) == []

    def test_projection_for_inn_unit(self):
        data = round_payload(_participants())
        found = projection_for_inn(data, INN_B)
        assert found is not None and found.inn == INN_B
        assert projection_for_inn(data, "7700000404") is None


# ---------------------------------------------------------------------------
#  Правило 1 AGENTS.md §5 после копии
# ---------------------------------------------------------------------------

class TestUploadAfterCopy:
    def test_same_file_returns_the_copy_job(self, scene, committing_client):
        _award(scene)
        card, job = _create(scene)
        _run(scene, job.id)
        before = _state(scene)

        response = committing_client.post(
            "/api/v1/estimates/upload",
            files={"file": ("stage.xlsx", STAGE_BYTES, "application/octet-stream")},
            data={"contract_id": str(card["id"])},
        )

        assert response.status_code == 200
        assert response.json()["id"] == job.id
        assert _state(scene) == before

    def test_other_file_with_replace_makes_the_estimate_uploaded_separately(
        self, scene, committing_client, monkeypatch,
    ):
        _award(scene)
        card, job = _create(scene)
        _run(scene, job.id)
        with scene.sf() as db:
            contract = db.get(Contract, card["id"])
            from tests.payloads import payload_for
            signed = payload_for(contract, [position(job_title="Подписанная работа", unit="м2", unit_cost_total="9")])
        monkeypatch.setattr(import_pipeline, "parse_estimate", fake_parse(signed))

        response = committing_client.post(
            "/api/v1/estimates/upload",
            files={"file": ("signed.xlsx", xlsx_bytes("signed"), "application/octet-stream")},
            data={"contract_id": str(card["id"]), "replace": "true"},
        )

        assert response.status_code == 202
        (estimate,) = _contract_estimates(scene, card["id"])
        assert estimate.source_award_id is None
        with scene.sf() as db:
            assert crud_contracts.get_contract_dict(db, card["id"])["estimate_origin"] == "uploaded_separately"
        assert _copy_positions(scene, card["id"]) == [("Подписанная работа", Decimal("9"))]
