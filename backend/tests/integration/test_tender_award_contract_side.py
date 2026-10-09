"""Сторона договора при отметке победителя тендера (спека Б2 §2.4, §2.6, §2.7):
основание и пометка происхождения сметы в карточке договора, запрет смены
объекта и подрядчика у связанного договора, отвязка от тендера.

Тесты идут на сессиях с настоящими commit-ами: отказ через ключ откатывает
сессию (`translating_integrity`), и корпус на общей транзакции пропал бы вместе
с ней. Состояние «после отказа» читается свежей сессией. Отказ проверяется
кодом, статусом и неизменённой базой, а не подстрокой текста.
"""
from __future__ import annotations

import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from crud import contracts as crud_contracts
from crud import tender_awards as crud_awards
from crud.common import DomainError
from models import (
    Contract,
    Contractor,
    Estimate,
    ImportJobStatus,
    ObjectModel,
    Offer,
    OfferPackage,
    Tender,
    TenderAward,
    UserRole,
)
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.payloads import position, proposal, round_payload

pytestmark = pytest.mark.integration

# Независимые литералы: эталон не вычисляется кодом, который проверяется.
OLD_CARD_KEYS = {
    "id", "contract_number", "title", "object_id", "object_title", "contractor_id", "contractor_title",
    "rate_class_id", "rate_class_title", "signer", "signed_date", "total_amount", "estimates_count",
    "created_at", "updated_at", "notes", "advance_pct", "advance_note", "bank_guarantee_pct",
    "bank_guarantee_note", "retention_pct", "retention_note", "estimates",
}
BASIS_KEYS = {"award_id", "tender_id", "tender_number", "tender_title", "round_id", "stage_no", "offer_id"}

PARTIES_LOCKED_KEY_TEXT = (
    "Нельзя: договор заключён по тендеру, объект и подрядчик берутся из тендера. Чтобы их изменить, "
    "сначала отвяжите договор от тендера (договор, созданный из КП, не отвязывается — если победитель "
    "не тот, его удаляют)."
)
ESTIMATE_IS_COPY_TEXT = (
    "Нельзя: смета договора — копия КП победителя этого тендера. Если победитель не тот, удалите "
    "договор вместе со сметой; если загрузили подписанную смету вместо копии, отвязка станет доступна."
)
NOT_LINKED_TEXT = "Договор не привязан к тендеру."
# Тексты команды — дословно §2.7, подстановка на месте «…»/«N».
PARTIES_LOCKED_TEXT = (
    "Нельзя: договор заключён по тендеру № {}, объект и подрядчик берутся из тендера. Чтобы их изменить, "
    "сначала отвяжите договор от тендера (договор, созданный из КП, не отвязывается — если победитель "
    "не тот, его удаляют)."
)
IMPORT_ACTIVE_TEXT = "Импорт сметы этого договора выполняется (задание {}). Дождитесь завершения и повторите."

_JOIN_TIMEOUT = 20.0


@pytest.fixture
def scene(committing_db, committing_factories, committing_session_factory):
    """Тендер с одним этапом и участниками А и Б (настоящим `import_round`);
    отметка на КП участника А не ставится — её ставят тесты. Администратор `user`."""
    f = committing_factories
    db = committing_db
    tender = f.TenderFactory.create()
    rnd = f.TenderRoundFactory.create(tender=tender, stage_no=1)
    db.flush()
    import_round(
        db, tender_round=rnd,
        data=round_payload([_participant("7700000001", "ООО А"), _participant("7700000002", "ООО Б")]),
        parser_version="4.0.0", import_job_id=None, replace=False, unit_resolver=UnitResolver(db),
        category_resolver=CategoryResolver.from_db(db),
    )
    user = f.UserFactory.create(role=UserRole.admin)
    other_object = f.ObjectFactory.create()
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
        sf=committing_session_factory, db=db, f=f, tender_id=tender.id, object_id=tender.object_id,
        tender_number=tender.tender_number, tender_title=tender.title, round_id=rnd.id,
        offer_a=offer_id("7700000001"), contractor_a=contractor_id("7700000001"),
        contractor_b=contractor_id("7700000002"), other_object_id=other_object.id, user_id=user.id,
    )


def _participant(inn: str, title: str):
    return proposal(
        [position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")],
        title=title, inn=inn,
    )


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _award_id(s) -> int:
    """Действующая отметка на КП участника А (создаётся один раз на тест)."""
    with s.sf() as db:
        existing = db.execute(sa.select(TenderAward.id).where(TenderAward.tender_id == s.tender_id)).scalar()
        if existing is not None:
            return existing
        crud_awards.award_winner(db, s.tender_id, offer_id=s.offer_a, user_id=s.user_id)
    with s.sf() as db:
        return db.execute(sa.select(TenderAward.id).where(TenderAward.tender_id == s.tender_id)).scalar_one()


def _contract(s, *, number=None, object_id=None, contractor_id=None) -> int:
    kwargs = {}
    if number:
        kwargs["contract_number"] = number
    contract = s.f.ContractFactory.create(
        object=s.db.get(ObjectModel, object_id if object_id is not None else s.object_id),
        contractor=s.db.get(Contractor, contractor_id if contractor_id is not None else s.contractor_a),
        **kwargs,
    )
    s.db.commit()
    return contract.id


def _link(s, contract_id, award_id=None) -> int:
    award = award_id if award_id is not None else _award_id(s)
    with s.sf() as db:
        crud_awards.link_contract(db, s.tender_id, award, contract_id=contract_id)
    return award


def _linked_contract(s, *, number=None) -> tuple[int, int]:
    contract_id = _contract(s, number=number)
    return contract_id, _link(s, contract_id)


def _estimate(s, contract_id, *, amendment_no=None, source_award_id=None) -> int:
    estimate = s.f.EstimateFactory.create(
        contract=s.db.get(Contract, contract_id), amendment_no=amendment_no, source_award_id=source_award_id,
    )
    s.db.commit()
    return estimate.id


def _job(s, contract_id, status, *, amendment_no=None) -> int:
    job = s.f.ImportJobFactory.create(
        contract=s.db.get(Contract, contract_id), amendment_no=amendment_no, status=status.value,
    )
    s.db.commit()
    return job.id


def _spread_ids(s) -> None:
    """Разводит последовательности id тендеров, этапов, оферт и отметок по
    непересекающимся диапазонам выше всех существующих id (и выше номера этапа)."""
    tables = ("tenders", "tender_rounds", "offers", "tender_awards")
    with s.sf() as db:
        base = max(
            db.execute(sa.text(f"SELECT COALESCE(max(id), 0) FROM {table}")).scalar_one() for table in tables
        ) + 10
        for step, table in enumerate(tables, start=1):
            db.execute(sa.text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), {base + 100 * step})"))
        db.commit()


def _card(s, contract_id) -> dict:
    with s.sf() as db:
        return crud_contracts.get_contract_dict(db, contract_id)


def _refused(fn) -> DomainError:
    with pytest.raises(DomainError) as info:
        fn()
    return info.value


def _update(s, contract_id, **kwargs):
    with s.sf() as db:
        return crud_contracts.update_contract(db, contract_id, **kwargs)


def _unlink(s, contract_id):
    with s.sf() as db:
        return crud_contracts.unlink_tender_award(db, contract_id)


def _row(s, contract_id) -> Contract:
    with s.sf() as db:
        row = db.get(Contract, contract_id)
        db.expunge(row)
        return row


def _estimate_rows(s, contract_id):
    with s.sf() as db:
        return db.execute(
            sa.select(Estimate.id, Estimate.amendment_no, Estimate.source_award_id)
            .where(Estimate.contract_id == contract_id).order_by(Estimate.id)
        ).all()


# ---------------------------------------------------------------------------
#  Карточка договора: основание и пометка
# ---------------------------------------------------------------------------

class TestCardBasisAndOrigin:
    def test_contract_without_basis_has_both_keys_null(self, scene):
        contract_id = _contract(scene)
        _estimate(scene, contract_id)
        card = _card(scene, contract_id)
        assert card["tender_basis"] is None
        assert card["estimate_origin"] is None

    def test_existing_card_keys_are_kept_and_only_two_are_added(self, scene):
        contract_id = _contract(scene)
        assert set(_card(scene, contract_id)) == OLD_CARD_KEYS | {"tender_basis", "estimate_origin"}

    def test_basis_has_the_form_of_the_spec(self, scene):
        # На свежей схеме id тендера, этапа, оферты и отметки — все 1, как и номер
        # этапа: перестановка ключей формы §2.6 (round_id ↔ stage_no, round_id ↔
        # offer_id) проходила зелёной при узком прогоне. Разводим диапазоны id.
        _spread_ids(scene)
        tender_id, award_id = _second_tender_award(scene)
        contract_id = _contract(scene)
        with scene.sf() as db:
            crud_awards.link_contract(db, tender_id, award_id, contract_id=contract_id)
        card = _card(scene, contract_id)
        with scene.sf() as db:
            offer_id, round_id, tender_number, tender_title = db.execute(
                sa.select(TenderAward.offer_id, Offer.round_id, Tender.tender_number, Tender.title)
                .join(Offer, Offer.id == TenderAward.offer_id)
                .join(Tender, Tender.id == TenderAward.tender_id)
                .where(TenderAward.id == award_id)
            ).one()
        assert len({award_id, tender_id, round_id, offer_id, 1}) == 5, "id и номер этапа обязаны различаться"
        assert set(card["tender_basis"]) == BASIS_KEYS
        assert card["tender_basis"] == {
            "award_id": award_id, "tender_id": tender_id, "tender_number": tender_number,
            "tender_title": tender_title, "round_id": round_id, "stage_no": 1, "offer_id": offer_id,
        }

    def test_linked_without_estimate_is_no_estimate(self, scene):
        contract_id, _ = _linked_contract(scene)
        assert _card(scene, contract_id)["estimate_origin"] == "no_estimate"

    def test_linked_with_separately_uploaded_base_estimate(self, scene):
        contract_id, _ = _linked_contract(scene)
        _estimate(scene, contract_id)
        assert _card(scene, contract_id)["estimate_origin"] == "uploaded_separately"

    def test_linked_with_copy_of_the_kp_is_from_offer(self, scene):
        contract_id, award_id = _linked_contract(scene)
        _estimate(scene, contract_id, source_award_id=award_id)
        assert _card(scene, contract_id)["estimate_origin"] == "from_offer"

    def test_amendment_alone_does_not_make_an_estimate(self, scene):
        contract_id, _ = _linked_contract(scene)
        _estimate(scene, contract_id, amendment_no=1)
        assert _card(scene, contract_id)["estimate_origin"] == "no_estimate"

    @pytest.mark.parametrize("with_copy", [False, True])
    def test_amendment_does_not_change_the_mark_of_the_base_estimate(self, scene, with_copy):
        # Допсоглашение вставлено ПЕРВЫМ: запрос без фильтра `amendment_no IS NULL`
        # (и без порядка) отдал бы его строку раньше основной сметы.
        contract_id, award_id = _linked_contract(scene)
        _estimate(scene, contract_id, amendment_no=1)
        _estimate(scene, contract_id, source_award_id=award_id if with_copy else None)
        expected = "from_offer" if with_copy else "uploaded_separately"
        assert _card(scene, contract_id)["estimate_origin"] == expected


# ---------------------------------------------------------------------------
#  update_contract: объект и подрядчик связанного договора
# ---------------------------------------------------------------------------

class TestUpdateLinkedContract:
    def test_changing_object_of_a_linked_contract_is_refused(self, scene):
        contract_id, _ = _linked_contract(scene)
        err = _refused(lambda: _update(scene, contract_id, object_id=scene.other_object_id))
        assert (err.status_code, err.code) == (409, "contract_parties_locked")
        assert err.detail == PARTIES_LOCKED_TEXT.format(scene.tender_number)
        assert _row(scene, contract_id).object_id == scene.object_id

    def test_changing_contractor_of_a_linked_contract_is_refused(self, scene):
        contract_id, _ = _linked_contract(scene)
        err = _refused(lambda: _update(scene, contract_id, contractor_id=scene.contractor_b))
        assert (err.status_code, err.code) == (409, "contract_parties_locked")
        assert err.detail == PARTIES_LOCKED_TEXT.format(scene.tender_number)
        assert _row(scene, contract_id).contractor_id == scene.contractor_a

    def test_a_refused_change_leaves_other_fields_of_the_same_request_unchanged(self, scene):
        contract_id, _ = _linked_contract(scene, number="ГП-ЗАПРЕТ")
        before = _row(scene, contract_id)
        _refused(lambda: _update(
            scene, contract_id, contract_number="ГП-НОВЫЙ", object_id=scene.other_object_id,
        ))
        after = _row(scene, contract_id)
        assert (after.contract_number, after.object_id) == (before.contract_number, before.object_id)

    def test_same_object_and_contractor_pass_and_only_the_requested_field_changes(self, scene):
        contract_id, award_id = _linked_contract(scene, number="ГП-ПРАВКА")
        card = _update(
            scene, contract_id, object_id=scene.object_id, contractor_id=scene.contractor_a,
            contract_number="ГП-ПРАВКА-2", advance_pct=Decimal("12.5"),
        )
        assert card["contract_number"] == "ГП-ПРАВКА-2"
        assert card["advance_pct"] == Decimal("12.5")
        assert (card["object_id"], card["contractor_id"]) == (scene.object_id, scene.contractor_a)
        assert card["tender_basis"]["award_id"] == award_id

    def test_other_fields_of_a_linked_contract_are_editable(self, scene):
        contract_id, _ = _linked_contract(scene)
        other_class = scene.f.RateClassFactory.create()
        scene.db.commit()
        card = _update(scene, contract_id, rate_class_id=other_class.id, retention_pct=Decimal("5"))
        assert card["rate_class_id"] == other_class.id
        assert card["retention_pct"] == Decimal("5")

    def test_unlinked_contract_changes_object_and_contractor_as_before(self, scene):
        contract_id = _contract(scene)
        card = _update(scene, contract_id, object_id=scene.other_object_id, contractor_id=scene.contractor_b)
        assert (card["object_id"], card["contractor_id"]) == (scene.other_object_id, scene.contractor_b)

    def test_key_gives_the_code_of_the_sync_refusal(self, scene, monkeypatch):
        """Ключ `fk_contracts_tender_award` мимо проверки команды (проверка
        выключена подменой) отвечает тем же кодом, статусом и текстом §2.7 без
        подстановок."""
        contract_id, _ = _linked_contract(scene)
        sync = _refused(lambda: _update(scene, contract_id, object_id=scene.other_object_id))
        monkeypatch.setattr(crud_contracts, "_refuse_if_parties_locked", lambda *a, **k: None)
        via_key = _refused(lambda: _update(scene, contract_id, object_id=scene.other_object_id))
        assert sync.code == via_key.code == "contract_parties_locked"
        assert sync.status_code == via_key.status_code == 409
        assert via_key.detail == PARTIES_LOCKED_KEY_TEXT
        assert _row(scene, contract_id).object_id == scene.object_id

    def test_contract_number_conflict_keeps_its_old_answer(self, scene):
        _contract(scene, number="ГП-ЗАНЯТ")
        contract_id = _contract(scene, number="ГП-СВОЙ")
        err = _refused(lambda: _update(scene, contract_id, contract_number="ГП-ЗАНЯТ"))
        assert (err.status_code, err.code) == (409, None)
        assert err.detail == "Договор с таким номером уже есть."


def test_update_races_a_link_and_answers_parties_locked(scene):
    """Правка прочитала договор без основания (пауза ПОСЛЕ чтения, результат уже
    у клиента), привязка закоммитилась; правка упирается в `fk_contracts_tender_award`
    и отвечает кодом, а не 500 и не `code=None`. Если правка берёт замок строки
    договора, привязка не проходит, пока правка стоит на паузе, — предусловие
    ниже краснеет.

    Пауза правки (`_JOIN_TIMEOUT`) обязана быть длиннее ожидания привязки
    (`join(timeout=8)`): иначе правка, держащая замок, отпустила бы его по
    истечении паузы, и предусловие прошло бы ложно."""
    award_id = _award_id(scene)
    contract_id = _contract(scene, number="ГП-ГОНКА-ПРАВКИ")
    held, release = threading.Event(), threading.Event()
    outcome: dict[str, str] = {}

    def edit():
        session = scene.sf()
        try:
            @sa.event.listens_for(session.connection(), "after_cursor_execute")
            def _pause(conn, cursor, statement, parameters, context, executemany):
                if "FROM contracts" in statement and "FOR UPDATE" not in statement.upper() and not held.is_set():
                    held.set()
                    release.wait(timeout=_JOIN_TIMEOUT)

            crud_contracts.update_contract(session, contract_id, object_id=scene.other_object_id)
            outcome["edit"] = "ok"
        except DomainError as exc:
            outcome["edit"] = exc.code or str(exc.status_code)
        except Exception as exc:  # noqa: BLE001 - любая иная ошибка проваливает тест ниже
            outcome["edit"] = f"{type(exc).__name__}: {exc}"
        finally:
            session.close()

    def link():
        session = scene.sf()
        try:
            crud_awards.link_contract(session, scene.tender_id, award_id, contract_id=contract_id)
            outcome["link"] = "ok"
        except Exception as exc:  # noqa: BLE001
            outcome["link"] = f"{type(exc).__name__}: {exc}"
        finally:
            session.close()

    t_edit = threading.Thread(target=edit, daemon=True)
    t_link = threading.Thread(target=link, daemon=True)
    t_edit.start()
    linked_while_paused = False
    try:
        assert held.wait(timeout=10), "правка не дошла до паузы после чтения договора"
        t_link.start()
        t_link.join(timeout=8)
        linked_while_paused = not t_link.is_alive()
    finally:
        release.set()
        t_edit.join(timeout=_JOIN_TIMEOUT)
        if t_link.ident is not None:
            t_link.join(timeout=_JOIN_TIMEOUT)
    assert not t_edit.is_alive()
    assert not t_link.is_alive()
    assert linked_while_paused, "привязка не закоммитилась, пока правка стоит на паузе: правка держит договор"
    assert outcome == {"edit": "contract_parties_locked", "link": "ok"}
    row = _row(scene, contract_id)
    assert (row.tender_award_id, row.object_id) == (award_id, scene.object_id)


# ---------------------------------------------------------------------------
#  unlink_tender_award
# ---------------------------------------------------------------------------

class TestUnlink:
    def test_contract_without_estimate_is_unlinked(self, scene):
        contract_id, award_id = _linked_contract(scene)
        card = _unlink(scene, contract_id)
        assert card["tender_basis"] is None
        assert card["estimate_origin"] is None
        assert card["id"] == contract_id
        assert _row(scene, contract_id).tender_award_id is None
        with scene.sf() as db:
            fragment = crud_awards.award_card_fragment(db, scene.tender_id)
        assert fragment["award"]["id"] == award_id
        assert fragment["award"]["contract"] is None

    def test_separately_uploaded_estimate_stays_untouched(self, scene):
        contract_id, _ = _linked_contract(scene)
        estimate_id = _estimate(scene, contract_id)
        before = _estimate_rows(scene, contract_id)
        card = _unlink(scene, contract_id)
        assert card["tender_basis"] is None
        assert _estimate_rows(scene, contract_id) == before == [(estimate_id, None, None)]

    def test_copy_of_the_kp_refuses_and_changes_nothing(self, scene):
        contract_id, award_id = _linked_contract(scene)
        estimate_id = _estimate(scene, contract_id, source_award_id=award_id)
        err = _refused(lambda: _unlink(scene, contract_id))
        assert (err.status_code, err.code) == (409, "contract_estimate_is_copy")
        assert err.detail == ESTIMATE_IS_COPY_TEXT
        assert _row(scene, contract_id).tender_award_id == award_id
        assert _estimate_rows(scene, contract_id) == [(estimate_id, None, award_id)]

    def test_unlinked_contract_refuses_with_not_linked(self, scene):
        contract_id = _contract(scene)
        err = _refused(lambda: _unlink(scene, contract_id))
        assert (err.status_code, err.code) == (409, "contract_not_linked")
        assert err.detail == NOT_LINKED_TEXT

    def test_missing_contract_is_404(self, scene):
        err = _refused(lambda: _unlink(scene, 999999))
        assert err.status_code == 404

    @pytest.mark.parametrize("status", [
        ImportJobStatus.pending, ImportJobStatus.parsing, ImportJobStatus.importing, ImportJobStatus.matching,
    ])
    @pytest.mark.parametrize("amendment_no", [None, 1])
    def test_active_import_job_refuses_with_its_number(self, scene, status, amendment_no):
        contract_id, award_id = _linked_contract(scene)
        job_id = _job(scene, contract_id, status, amendment_no=amendment_no)
        err = _refused(lambda: _unlink(scene, contract_id))
        assert (err.status_code, err.code) == (409, "contract_import_active")
        assert err.detail == IMPORT_ACTIVE_TEXT.format(job_id)
        assert _row(scene, contract_id).tender_award_id == award_id

    @pytest.mark.parametrize("status", [ImportJobStatus.done, ImportJobStatus.error])
    def test_finished_import_jobs_do_not_block_the_unlink(self, scene, status):
        contract_id, _ = _linked_contract(scene)
        _job(scene, contract_id, status)
        _job(scene, contract_id, status, amendment_no=1)
        assert _unlink(scene, contract_id)["tender_basis"] is None

    def test_import_job_of_another_contract_does_not_block(self, scene):
        contract_id, _ = _linked_contract(scene)
        other = _contract(scene, number="ГП-ДРУГОЙ")
        _job(scene, other, ImportJobStatus.pending)
        assert _unlink(scene, contract_id)["tender_basis"] is None

    def test_active_import_is_checked_before_the_copy(self, scene):
        contract_id, award_id = _linked_contract(scene)
        _estimate(scene, contract_id, source_award_id=award_id)
        _job(scene, contract_id, ImportJobStatus.pending, amendment_no=1)
        err = _refused(lambda: _unlink(scene, contract_id))
        assert err.code == "contract_import_active"

    def test_not_linked_is_checked_before_the_active_import(self, scene):
        contract_id = _contract(scene)
        _job(scene, contract_id, ImportJobStatus.pending)
        err = _refused(lambda: _unlink(scene, contract_id))
        assert err.code == "contract_not_linked"

    def test_amendment_never_makes_the_estimate_a_copy(self, scene):
        contract_id, _ = _linked_contract(scene)
        _estimate(scene, contract_id, amendment_no=1)
        assert _unlink(scene, contract_id)["tender_basis"] is None

    def test_key_gives_the_code_of_the_sync_refusal(self, scene, monkeypatch):
        """Отвязка мимо проверки команды при смете-копии упирается в
        `fk_estimates_source_award` и отвечает кодом синхронного отказа."""
        contract_id, award_id = _linked_contract(scene)
        estimate_id = _estimate(scene, contract_id, source_award_id=award_id)
        sync = _refused(lambda: _unlink(scene, contract_id))
        monkeypatch.setattr(crud_contracts, "_refuse_if_estimate_is_copy", lambda *a, **k: None)
        via_key = _refused(lambda: _unlink(scene, contract_id))
        assert sync.code == via_key.code == "contract_estimate_is_copy"
        assert sync.status_code == via_key.status_code == 409
        assert via_key.detail == ESTIMATE_IS_COPY_TEXT
        assert _row(scene, contract_id).tender_award_id == award_id
        assert _estimate_rows(scene, contract_id) == [(estimate_id, None, award_id)]

    def test_after_unlink_the_object_changes_by_edit(self, scene):
        contract_id, _ = _linked_contract(scene)
        _unlink(scene, contract_id)
        card = _update(scene, contract_id, object_id=scene.other_object_id)
        assert card["object_id"] == scene.other_object_id

    def test_relink_to_another_award_of_the_same_object_and_contractor(self, scene):
        contract_id, first_award = _linked_contract(scene)
        estimate_id = _estimate(scene, contract_id)
        _unlink(scene, contract_id)
        other_tender_id, other_award = _second_tender_award(scene)
        with scene.sf() as db:
            crud_awards.link_contract(db, other_tender_id, other_award, contract_id=contract_id)
        card = _card(scene, contract_id)
        assert card["tender_basis"]["award_id"] == other_award != first_award
        assert card["tender_basis"]["tender_id"] == other_tender_id
        assert card["estimate_origin"] == "uploaded_separately"
        assert _estimate_rows(scene, contract_id) == [(estimate_id, None, None)]


def test_unlink_rereads_a_contract_already_loaded_in_the_session(scene):
    """Замок договора — с `populate_existing`: договор, загруженный в сессию до
    отвязки, перечитывается под замком. Иначе identity map отдал бы основание,
    которое другая сессия уже сняла, и вторая отвязка прошла бы успехом."""
    contract_id, _ = _linked_contract(scene)
    with scene.sf() as db:
        # Ссылка держится: identity map слабый, и объект без ссылки ушёл бы из
        # него сборщиком — тогда замок читал бы строку заново и без опции.
        loaded = db.get(Contract, contract_id)
        assert loaded.tender_award_id is not None
        _unlink(scene, contract_id)   # другая сессия, коммит
        err = _refused(lambda: crud_contracts.unlink_tender_award(db, contract_id))
    assert (err.status_code, err.code) == (409, "contract_not_linked")


def test_refused_unlink_releases_the_contract_lock_at_once(scene):
    """Отказ отвязки откатывает сессию (`rollback_on_domain_error`): замок
    договора `FOR UPDATE` снимается сразу, а не когда вызывающий закроет сессию.
    Пока сессия отказа открыта, другая сессия берёт замок договора `NOWAIT`."""
    contract_id, award_id = _linked_contract(scene)
    _estimate(scene, contract_id, source_award_id=award_id)
    with scene.sf() as db:
        err = _refused(lambda: crud_contracts.unlink_tender_award(db, contract_id))
        assert err.code == "contract_estimate_is_copy"
        with scene.sf() as other:
            locked = other.execute(
                sa.text("SELECT id FROM contracts WHERE id = :id FOR UPDATE NOWAIT"), {"id": contract_id}
            ).scalar_one()
            other.rollback()
    assert locked == contract_id


def test_concurrent_unlinks_serialize_on_the_contract_lock(scene):
    """Две отвязки одного договора: первая взяла договор `FOR UPDATE` и стоит на
    паузе после чтения; вторая ждёт замка и после коммита первой видит договор
    без основания — `contract_not_linked`. Без замка вторая прошла бы, пока
    первая на паузе, и обе ответили бы успехом. Пауза (`_JOIN_TIMEOUT`) длиннее
    ожидания второй (`join(timeout=3)`)."""
    contract_id, _ = _linked_contract(scene, number="ГП-ДВЕ-ОТВЯЗКИ")
    held, release = threading.Event(), threading.Event()
    outcome: dict[str, str] = {}

    def unlink(name, pause):
        session = scene.sf()
        try:
            if pause:
                @sa.event.listens_for(session.connection(), "after_cursor_execute")
                def _pause(conn, cursor, statement, parameters, context, executemany):
                    if "FROM contracts" in statement and not held.is_set():
                        held.set()
                        release.wait(timeout=_JOIN_TIMEOUT)

            crud_contracts.unlink_tender_award(session, contract_id)
            outcome[name] = "ok"
        except DomainError as exc:
            outcome[name] = exc.code or str(exc.status_code)
        except Exception as exc:  # noqa: BLE001 - любая иная ошибка проваливает тест ниже
            outcome[name] = f"{type(exc).__name__}: {exc}"
        finally:
            session.close()

    t_first = threading.Thread(target=unlink, args=("first", True), daemon=True)
    t_second = threading.Thread(target=unlink, args=("second", False), daemon=True)
    t_first.start()
    second_done_while_paused = None
    try:
        assert held.wait(timeout=10), "первая отвязка не дошла до паузы после чтения договора"
        t_second.start()
        t_second.join(timeout=3)
        second_done_while_paused = not t_second.is_alive()
    finally:
        release.set()
        t_first.join(timeout=_JOIN_TIMEOUT)
        if t_second.ident is not None:
            t_second.join(timeout=_JOIN_TIMEOUT)
    assert not t_first.is_alive()
    assert not t_second.is_alive()
    assert outcome == {"first": "ok", "second": "contract_not_linked"}
    assert second_done_while_paused is False, "вторая отвязка не ждала замка договора"
    assert _row(scene, contract_id).tender_award_id is None


def _second_tender_award(s) -> tuple[int, int]:
    """Второй тендер на том же объекте с тем же участником А и действующей
    отметкой на нём: (id тендера, id отметки)."""
    other_tender = s.f.TenderFactory.create(object=s.db.get(ObjectModel, s.object_id))
    other_round = s.f.TenderRoundFactory.create(tender=other_tender, stage_no=1)
    s.db.flush()
    import_round(
        s.db, tender_round=other_round, data=round_payload([_participant("7700000001", "ООО А")]),
        parser_version="4.0.0", import_job_id=None, replace=False, unit_resolver=UnitResolver(s.db),
        category_resolver=CategoryResolver.from_db(s.db),
    )
    s.db.commit()
    offer_id = s.db.execute(sa.select(Offer.id).where(Offer.round_id == other_round.id)).scalar_one()
    with s.sf() as db:
        crud_awards.award_winner(db, other_tender.id, offer_id=offer_id, user_id=s.user_id)
    with s.sf() as db:
        award_id = db.execute(
            sa.select(TenderAward.id).where(TenderAward.tender_id == other_tender.id)
        ).scalar_one()
    return other_tender.id, award_id
