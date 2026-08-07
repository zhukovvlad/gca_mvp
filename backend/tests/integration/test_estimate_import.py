"""Сервис импорта сметы (AGENTS.md §5, шаг 3; порт ImportFullTender)."""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

import pytest
import sqlalchemy as sa

from models import (
    Estimate,
    EstimateAdditionalWork,
    EstimateRawData,
    Lot,
    PositionItem,
    Proposal,
    ProposalAdditionalInfo,
    ProposalSummaryLine,
)
from parser.constants import (
    JSON_KEY_BASELINE_PROPOSAL,
    JSON_KEY_CONTRACTOR_ITEMS,
    JSON_KEY_CONTRACTOR_POSITIONS,
    JSON_KEY_CONTRACTOR_TITLE,
    JSON_KEY_LOT_TITLE,
    JSON_KEY_LOTS,
    JSON_KEY_PROPOSALS,
)
from parser.postprocess import BASELINE_MISSING_TITLE
from services.category_resolution import CategoryResolver
from services.estimate_import import (
    EstimateImportError,
    compare_header_with_contract,
    import_estimate,
)
from services.unit_resolution import UnitResolver
from tests.payloads import (
    additional_works_row,
    estimate_payload,
    payload_for,
    position,
    proposal,
    summary_line,
    svedeniya_info,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def resolver(db_session):
    return UnitResolver(db_session)


def run_import(db_session, resolver, contract, data, *, amendment_no=None, replace=False, job=None):
    return import_estimate(
        db_session,
        contract=contract,
        amendment_no=amendment_no,
        data=data,
        parser_version="1.0.0",
        import_job_id=job.id if job is not None else None,
        replace=replace,
        unit_resolver=resolver,
        category_resolver=CategoryResolver.from_db(db_session),
    )


# ---------------------------------------------------------------------------
#  Обход тендер → лоты → предложение → позиции/итоги → raw JSON
# ---------------------------------------------------------------------------

class TestFullWalk:
    def test_creates_the_whole_tree(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        job = factories.ImportJobFactory.create(contract=contract)
        db_session.flush()
        data = payload_for(
            contract,
            [
                position(job_title="Раздел 1", is_chapter=True, chapter_number="1"),
                position(
                    job_title="Устройство стяжки",
                    unit="м2",
                    quantity=1,
                    suggested_quantity=12.5,
                    unit_cost_total="100.50",
                    total_cost_total="1256.25",
                    chapter_ref="1",
                    number="2",
                ),
            ],
        )

        outcome = run_import(db_session, resolver, contract, data, job=job)

        estimate = db_session.get(Estimate, outcome.estimate_id)
        assert estimate.contract_id == contract.id
        assert estimate.amendment_no is None
        assert estimate.import_job_id == job.id
        assert estimate.title == "Смета к договору генподряда"

        raw = db_session.get(EstimateRawData, outcome.estimate_id)
        assert raw.parser_version == "1.0.0"
        assert raw.raw_data["tender_id"] == "001-ГП"

        lot = db_session.execute(
            sa.select(Lot).where(Lot.estimate_id == estimate.id)
        ).scalar_one()
        assert (lot.lot_key, lot.lot_title) == ("lot_1", "Лот №1 - Тестовый")

        proposal_row = db_session.execute(
            sa.select(Proposal).where(Proposal.lot_id == lot.id)
        ).scalar_one()
        # Подрядчик — из карточки договора, не из файла (§3).
        assert proposal_row.contractor_id == contract.contractor_id
        assert proposal_row.is_baseline is False
        assert (proposal_row.contractor_coordinate, proposal_row.contractor_width) == ("J6", 11)

        items = db_session.execute(
            sa.select(PositionItem)
            .where(PositionItem.proposal_id == proposal_row.id)
            .order_by(PositionItem.position_key_in_proposal)
        ).scalars().all()
        assert len(items) == 2
        chapter, work = items
        assert chapter.is_chapter is True
        assert work.is_chapter is False
        assert work.chapter_ref_in_proposal == "1"
        assert work.job_title_in_proposal == "Устройство стяжки"
        assert work.deviation_from_baseline_cost is None

        assert outcome.positions_total == 2
        # К каскаду допущена одна строка: раздел исключён (§5, шаг 4).
        assert [p.job_title for p in outcome.positions_to_match] == ["Устройство стяжки"]

    def test_summary_and_additional_info(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract)

        outcome = run_import(db_session, resolver, contract, data)

        proposal_id = db_session.execute(
            sa.select(Proposal.id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == outcome.estimate_id)
        ).scalar_one()
        lines = {
            line.summary_key: line
            for line in db_session.execute(
                sa.select(ProposalSummaryLine).where(
                    ProposalSummaryLine.proposal_id == proposal_id
                )
            ).scalars()
        }
        assert set(lines) == {"total_cost_including_vat", "vat_amount", "total_cost_excluding_vat"}
        assert lines["total_cost_including_vat"].total_cost == Decimal("1200.00")
        assert lines["total_cost_excluding_vat"].total_cost == Decimal("1000.00")

        info = {
            row.info_key: row.info_value
            for row in db_session.execute(
                sa.select(ProposalAdditionalInfo).where(
                    ProposalAdditionalInfo.proposal_id == proposal_id
                )
            ).scalars()
        }
        assert info == {"Условия оплаты": "Аванс 30%", "Гарантия": None}

    def test_several_lots_are_imported(self, db_session, factories, resolver):
        """Слой лотов сохранён (§4); ровно одно предложение требуется в КАЖДОМ лоте."""
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract)
        data["lots"]["lot_2"] = {
            "lot_title": "Лот №2 - Второй",
            "proposals": {"contractor_1": proposal([position(job_title="Работа 2")])},
            "baseline_proposal": {"title": "Расчетная стоимость отсутствует"},
        }

        outcome = run_import(db_session, resolver, contract, data)

        keys = db_session.execute(
            sa.select(Lot.lot_key).where(Lot.estimate_id == outcome.estimate_id).order_by(Lot.lot_key)
        ).scalars().all()
        assert keys == ["lot_1", "lot_2"]


# ---------------------------------------------------------------------------
#  Деньги и количества
# ---------------------------------------------------------------------------

class TestMoneyAndQuantities:
    def test_money_strings_become_exact_decimals(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(
            contract,
            [
                position(
                    job_title="Работа",
                    unit="шт",
                    unit_cost={
                        "materials": "1.01",
                        "works": "2.02",
                        "indirect_costs": "3.03",
                        "total": "6.06",
                    },
                    total_cost={
                        "materials": "10.10",
                        "works": "20.20",
                        "indirect_costs": "30.30",
                        "total": "14998746.74",
                    },
                    organizer_total="99.99",
                )
            ],
        )

        outcome = run_import(db_session, resolver, contract, data)

        item = db_session.execute(
            sa.select(PositionItem).join(Proposal).join(Lot).where(
                Lot.estimate_id == outcome.estimate_id
            )
        ).scalar_one()
        assert item.unit_cost_total == Decimal("6.06")
        assert item.total_cost_total == Decimal("14998746.74")
        assert item.total_cost_for_organizer_quantity == Decimal("99.99")

    def test_float_quantity_has_no_binary_tail(self, db_session, factories, resolver):
        """Количества приходят числами; Decimal(float) дал бы двоичный хвост (§3)."""
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(
            contract, [position(job_title="Работа", unit="м2", quantity=1, suggested_quantity=0.1)]
        )

        outcome = run_import(db_session, resolver, contract, data)

        item = db_session.execute(
            sa.select(PositionItem).join(Proposal).join(Lot).where(
                Lot.estimate_id == outcome.estimate_id
            )
        ).scalar_one()
        assert item.suggested_quantity == Decimal("0.1")
        assert item.quantity == Decimal("1")

    def test_empty_cost_is_null_not_zero(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, [position(job_title="Раздел", is_chapter=True, chapter_number="1")])

        outcome = run_import(db_session, resolver, contract, data)

        item = db_session.execute(
            sa.select(PositionItem).join(Proposal).join(Lot).where(
                Lot.estimate_id == outcome.estimate_id
            )
        ).scalar_one()
        assert item.unit_cost_total is None
        assert item.quantity is None

    def test_garbage_in_money_column_becomes_null_with_warning(
        self, db_session, factories, resolver
    ):
        """Одна нечитаемая ячейка не роняет импорт 2,5-тысячной сметы."""
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(
            contract,
            [position(job_title="Работа", unit="шт", unit_cost_total="уточняется", total_cost_total="10")],
        )

        outcome = run_import(db_session, resolver, contract, data)

        item = db_session.execute(
            sa.select(PositionItem).join(Proposal).join(Lot).where(
                Lot.estimate_id == outcome.estimate_id
            )
        ).scalar_one()
        assert item.unit_cost_total is None
        assert item.total_cost_total == Decimal("10")
        assert any("уточняется" in w for w in outcome.warnings)

    def test_value_warnings_are_squashed(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(
            contract,
            [position(job_title=f"Работа {i}", unit="шт", unit_cost_total="мусор") for i in range(30)],
        )

        outcome = run_import(db_session, resolver, contract, data)

        assert any("и ещё" in w for w in outcome.warnings)
        assert sum("мусор" in w for w in outcome.warnings) == 10


# ---------------------------------------------------------------------------
#  Единицы измерения (решение фазы 4 §2.3)
# ---------------------------------------------------------------------------

class TestUnits:
    def test_alias_is_resolved(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, [position(job_title="Работа", unit="кв.м")])

        outcome = run_import(db_session, resolver, contract, data)

        item = db_session.execute(
            sa.select(PositionItem).join(Proposal).join(Lot).where(
                Lot.estimate_id == outcome.estimate_id
            )
        ).scalar_one()
        code = db_session.execute(
            sa.text("SELECT code FROM units_of_measure WHERE id = :id"), {"id": item.unit_id}
        ).scalar_one()
        assert code == "M2"
        assert outcome.positions_to_match[0].unit.unit_norm == "M2"

    def test_unknown_unit_gives_null_and_warning(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(
            contract,
            [
                position(job_title="Работа A", unit="тонно-километр"),
                position(job_title="Работа Б", unit="тонно-километр"),
            ],
        )

        outcome = run_import(db_session, resolver, contract, data)

        items = db_session.execute(
            sa.select(PositionItem).join(Proposal).join(Lot).where(
                Lot.estimate_id == outcome.estimate_id
            )
        ).scalars().all()
        assert all(item.unit_id is None for item in items)
        # Одно предупреждение на уникальный текст, с числом позиций.
        unit_warnings = [w for w in outcome.warnings if "тонно-километр" in w]
        assert len(unit_warnings) == 1
        assert "позиций: 2" in unit_warnings[0]

    def test_unknown_unit_does_not_create_a_unit(self, db_session, factories, resolver):
        """§4: units_of_measure — курируемый справочник, импорт его не пополняет."""
        contract = factories.ContractFactory.create()
        db_session.flush()
        before = db_session.execute(sa.text("SELECT count(*) FROM units_of_measure")).scalar_one()

        run_import(
            db_session, resolver, contract, payload_for(contract, [position(job_title="Р", unit="фунт")])
        )

        after = db_session.execute(sa.text("SELECT count(*) FROM units_of_measure")).scalar_one()
        assert after == before


# ---------------------------------------------------------------------------
#  Сверка шапки с карточкой (§3)
# ---------------------------------------------------------------------------

class TestHeaderComparison:
    def test_matching_header_gives_no_warnings(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()

        outcome = run_import(db_session, resolver, contract, payload_for(contract))

        assert outcome.warnings == []

    def test_object_mismatch_warns_but_imports(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, tender_object="Совершенно другой объект")

        outcome = run_import(db_session, resolver, contract, data)

        assert outcome.estimate_id is not None
        assert any(w.startswith("Объект в файле") for w in outcome.warnings)

    def test_inn_mismatch_warns(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, inn="9999999999")

        outcome = run_import(db_session, resolver, contract, data)

        assert any("ИНН подрядчика" in w for w in outcome.warnings)

    def test_punctuation_only_difference_is_not_a_mismatch(self, db_session, factories):
        contract = factories.ContractFactory.create()
        contract.object.title = "ЖК «Северный», корп. 2"
        contract.object.address = "г. Тест, ул. Ленина, д. 5"
        db_session.flush()
        data = estimate_payload(
            tender_object="ЖК Северный корп 2",
            tender_address="г Тест ул Ленина д 5",
            title=contract.contractor.title,
            inn=contract.contractor.inn,
        )

        warnings = compare_header_with_contract(
            data, contract, data["lots"]["lot_1"]["proposals"]["contractor_1"]
        )
        assert warnings == []

    def test_contractor_details_from_file_are_not_persisted(self, db_session, factories, resolver):
        """Адрес и аккредитация из файла не апсертятся в карточку подрядчика (§3)."""
        contract = factories.ContractFactory.create()
        original_address = contract.contractor.address
        db_session.flush()
        data = payload_for(contract)
        data["lots"]["lot_1"]["proposals"]["contractor_1"]["address"] = "другой адрес из файла"

        run_import(db_session, resolver, contract, data)

        db_session.refresh(contract.contractor)
        assert contract.contractor.address == original_address


# ---------------------------------------------------------------------------
#  Отказы (решение фазы 4 §2.1)
# ---------------------------------------------------------------------------

class TestRejections:
    def test_two_contractors_are_rejected(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(
            contract,
            proposals={
                "contractor_1": proposal([position(job_title="Работа")], title="ООО Первый"),
                "contractor_2": proposal([position(job_title="Работа")], title="ООО Второй"),
            },
        )

        with pytest.raises(EstimateImportError, match="нескольким|несколькими"):
            run_import(db_session, resolver, contract, data)

    def test_two_contractors_message_names_them(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(
            contract,
            proposals={
                "contractor_1": proposal([position(job_title="Работа")], title="ООО Первый"),
                "contractor_2": proposal([position(job_title="Работа")], title="ООО Второй"),
            },
        )

        with pytest.raises(EstimateImportError) as exc:
            run_import(db_session, resolver, contract, data)
        assert "ООО Первый" in str(exc.value) and "ООО Второй" in str(exc.value)

    def test_no_proposal_is_rejected(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, proposals={})

        with pytest.raises(EstimateImportError, match="нет предложения"):
            run_import(db_session, resolver, contract, data)

    def test_no_lots_is_rejected(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, lots={})

        with pytest.raises(EstimateImportError, match="ни одного лота"):
            run_import(db_session, resolver, contract, data)

    def test_rejection_leaves_nothing_behind(self, db_session, factories, resolver):
        """Отказ происходит ДО первой вставки — половины сметы не остаётся."""
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(
            contract,
            proposals={
                "contractor_1": proposal([position(job_title="Работа")], title="А"),
                "contractor_2": proposal([position(job_title="Работа")], title="Б"),
            },
        )

        with pytest.raises(EstimateImportError):
            run_import(db_session, resolver, contract, data)

        count = db_session.execute(
            sa.select(sa.func.count()).select_from(Estimate).where(Estimate.contract_id == contract.id)
        ).scalar_one()
        assert count == 0


# ---------------------------------------------------------------------------
#  Предупреждения-эвристики (решения фазы 4 §2.2, §3.4)
# ---------------------------------------------------------------------------

class TestHeuristicWarnings:
    def test_all_money_null_warns_about_formulas(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(
            contract,
            [position(job_title="Работа 1", unit="м2"), position(job_title="Работа 2", unit="шт")],
            summary={},
        )

        outcome = run_import(db_session, resolver, contract, data)

        assert any("пересчёта формул" in w for w in outcome.warnings)

    def test_priced_estimate_has_no_formula_warning(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(
            contract, [position(job_title="Работа", unit="м2", unit_cost_total="10.00")]
        )

        outcome = run_import(db_session, resolver, contract, data)

        assert not any("пересчёта формул" in w for w in outcome.warnings)

    def test_unexpected_baseline_warns(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, baseline_title="Расчетная стоимость")

        outcome = run_import(db_session, resolver, contract, data)

        assert any("расчётной стоимости" in w for w in outcome.warnings)
        # Baseline не импортируется — предложение в лоте ровно одно.
        proposals = db_session.execute(
            sa.select(sa.func.count())
            .select_from(Proposal)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == outcome.estimate_id)
        ).scalar_one()
        assert proposals == 1

    def test_untitled_position_is_stored_but_not_matched(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(
            contract,
            [position(job_title=None, unit="шт", unit_cost_total="1"), position(job_title="Работа")],
        )

        outcome = run_import(db_session, resolver, contract, data)

        assert outcome.positions_total == 2
        assert [p.job_title for p in outcome.positions_to_match] == ["Работа"]
        assert any("без наименования" in w for w in outcome.warnings)

    def test_long_job_title_warns_without_quoting_it_whole(
        self, db_session, factories, resolver
    ):
        """Наименование на килобайты — сигнал к ручной проверке, а не отказ.

        В реальном файле в это поле попала спецификация на 5077 символов. Импорт
        такое принимает (работа могла быть описана и так), но предупреждение
        обязано быть агрегированным: полный текст в `warnings` превратил бы
        историю загрузок в свалку.
        """
        contract = factories.ContractFactory.create()
        db_session.flush()
        spec = "Наружные блоки кондиционирования, " + "детали спецификации; " * 200
        data = payload_for(
            contract,
            [
                position(job_title=spec, unit="шт", number="12.3"),
                position(job_title="Обычная работа", unit="м2", number="13"),
            ],
        )

        outcome = run_import(db_session, resolver, contract, data)

        found = [w for w in outcome.warnings if "длиннее" in w and "спецификация" in w]
        assert len(found) == 1, outcome.warnings
        warning = found[0]
        assert "позиций: 1" in warning
        assert "12.3" in warning                    # номер позиции
        assert str(len(spec.strip())) in warning     # длина (наименование хранится обрезанным)
        assert spec not in warning                   # но не сам текст
        assert "лот" not in warning                  # лот единственный — не шумим
        assert len(warning) < 700

    def test_long_job_title_warning_is_one_per_estimate_not_per_lot(
        self, db_session, factories, resolver
    ):
        """Предупреждение агрегируется по всей смете, а не по каждому лоту.

        `_import_positions` вызывается по разу на лот, поэтому аккумулятор длинных
        наименований живёт уровнем выше: иначе файл с тремя лотами дал бы три почти
        одинаковых предупреждения, каждое со своими десятью примерами.
        """
        contract = factories.ContractFactory.create()
        db_session.flush()
        spec = "спецификация подробная " * 60
        data = payload_for(contract, [position(job_title=spec + " один", number="1")])
        data["lots"]["lot_2"] = {
            "lot_title": "Лот №2",
            "proposals": {
                "contractor_1": proposal([position(job_title=spec + " два", number="2")])
            },
            "baseline_proposal": {"title": "Расчетная стоимость отсутствует"},
        }

        outcome = run_import(db_session, resolver, contract, data)

        found = [w for w in outcome.warnings if "длиннее" in w and "спецификация" in w]
        assert len(found) == 1, outcome.warnings
        assert "позиций: 2" in found[0]
        # Номера позиций начинаются заново в каждом лоте, поэтому у многолотовой
        # сметы в примере обязан быть лот — иначе два «№1» не различить.
        assert "лот lot_1, №1" in found[0]
        assert "лот lot_2, №2" in found[0]

    def test_long_job_title_warning_shows_lot_even_if_only_one_lot_has_them(
        self, db_session, factories, resolver
    ):
        """Лот показывается по многолотовости сметы, а не примеров.

        Считать лоты по самим примерам — ошибка: одна длинная позиция в смете из
        нескольких лотов дала бы «лотов один», хотя искать «№1» пришлось бы во
        всех.
        """
        contract = factories.ContractFactory.create()
        db_session.flush()
        spec = "спецификация подробная " * 60
        data = payload_for(contract, [position(job_title=spec, number="1")])
        data["lots"]["lot_2"] = {
            "lot_title": "Лот №2",
            "proposals": {"contractor_1": proposal([position(job_title="Короткая", number="1")])},
            "baseline_proposal": {"title": "Расчетная стоимость отсутствует"},
        }

        outcome = run_import(db_session, resolver, contract, data)

        warning = next(w for w in outcome.warnings if "длиннее" in w)
        assert "позиций: 1" in warning
        assert "лот lot_1, №1" in warning

    def test_long_job_title_warning_promises_nothing_about_review(
        self, db_session, factories, resolver
    ):
        """Раздел в Review не попадает — обещать его нельзя.

        Длина учитывается и у разделов (подозрительна сама длина поля), но
        `is_chapter` к матчингу не допускается, то есть в очередь ручного матчинга
        такая строка не придёт.
        """
        contract = factories.ContractFactory.create()
        db_session.flush()
        spec = "раздел с описанием на много символов " * 40
        data = payload_for(
            contract,
            [position(job_title=spec, is_chapter=True, number="7", chapter_number="7")],
        )

        outcome = run_import(db_session, resolver, contract, data)

        warning = next(w for w in outcome.warnings if "длиннее" in w)
        assert "Review" not in warning
        assert "проверьте указанные позиции" in warning
        assert outcome.positions_to_match == []

    def test_long_job_title_warning_caps_examples(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(
            contract,
            [
                position(job_title=f"{i} " + "спецификация " * 100, unit="шт", number=str(i))
                for i in range(15)
            ],
        )

        outcome = run_import(db_session, resolver, contract, data)

        warning = next(w for w in outcome.warnings if "спецификация" in w and "длиннее" in w)
        assert "позиций: 15" in warning
        assert "и ещё 5" in warning

    def test_unparsable_prepared_date_warns(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, executor_date="как-нибудь потом")

        outcome = run_import(db_session, resolver, contract, data)

        assert db_session.get(Estimate, outcome.estimate_id).data_prepared_on_date is None
        assert any("Дата составления" in w for w in outcome.warnings)

    def test_iso_prepared_date_is_stored(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, executor_date="2026-05-14")

        outcome = run_import(db_session, resolver, contract, data)

        assert db_session.get(Estimate, outcome.estimate_id).data_prepared_on_date == dt.date(
            2026, 5, 14
        )


# ---------------------------------------------------------------------------
#  replace-флоу (§5, правило 3)
# ---------------------------------------------------------------------------

class TestReplace:
    def test_replace_deletes_the_old_estimate_and_its_tree(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        first = run_import(db_session, resolver, contract, payload_for(contract))
        old_id = first.estimate_id

        second = run_import(
            db_session, resolver, contract, payload_for(contract), replace=True
        )

        assert second.replaced_estimate_id == old_id
        assert db_session.get(Estimate, old_id) is None
        assert db_session.get(EstimateRawData, old_id) is None
        assert (
            db_session.execute(
                sa.select(sa.func.count()).select_from(Lot).where(Lot.estimate_id == old_id)
            ).scalar_one()
            == 0
        )
        assert any(f"estimate_id={old_id}" in w for w in second.warnings)

    def test_replace_keeps_old_import_jobs(self, db_session, factories, resolver):
        """Старые задания и их файлы — аудит, они не удаляются (§5)."""
        contract = factories.ContractFactory.create()
        old_job = factories.ImportJobFactory.create(contract=contract, status="done")
        db_session.flush()
        run_import(db_session, resolver, contract, payload_for(contract), job=old_job)

        run_import(db_session, resolver, contract, payload_for(contract), replace=True)

        assert db_session.get(type(old_job), old_job.id) is not None

    def test_replace_without_existing_estimate_is_a_noop(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()

        outcome = run_import(db_session, resolver, contract, payload_for(contract), replace=True)

        assert outcome.replaced_estimate_id is None

    def test_replace_targets_only_its_own_amendment(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        base = run_import(db_session, resolver, contract, payload_for(contract))
        amendment = run_import(
            db_session, resolver, contract, payload_for(contract), amendment_no=1
        )

        run_import(
            db_session, resolver, contract, payload_for(contract), amendment_no=1, replace=True
        )

        assert db_session.get(Estimate, base.estimate_id) is not None
        assert db_session.get(Estimate, amendment.estimate_id) is None

    def test_second_import_of_the_same_pair_without_replace_hits_the_unique_index(
        self, db_session, factories, resolver
    ):
        """UNIQUE NULLS NOT DISTINCT: исходную смету нельзя загрузить дважды."""
        contract = factories.ContractFactory.create()
        db_session.flush()
        run_import(db_session, resolver, contract, payload_for(contract))

        with pytest.raises(sa.exc.IntegrityError):
            run_import(db_session, resolver, contract, payload_for(contract))
            db_session.flush()


# ---------------------------------------------------------------------------
#  Summary/итоги без своих строк — только с текстом
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_summary_without_totals_is_stored(self, db_session, factories, resolver):
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, summary={"merged_99": summary_line("Странная строка")})

        outcome = run_import(db_session, resolver, contract, data)

        line = db_session.execute(
            sa.select(ProposalSummaryLine)
            .join(Proposal, Proposal.id == ProposalSummaryLine.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == outcome.estimate_id)
        ).scalar_one()
        assert (line.summary_key, line.job_title, line.total_cost) == (
            "merged_99",
            "Странная строка",
            None,
        )

    def test_raw_data_is_stored_verbatim(self, db_session, factories, resolver):
        """raw_data — архив содержимого файла, включая поля без своих колонок."""
        contract = factories.ContractFactory.create()
        db_session.flush()
        data = payload_for(contract, [position(job_title="Работа", article_smr="СМР-42")])

        outcome = run_import(db_session, resolver, contract, data)

        stored = db_session.get(EstimateRawData, outcome.estimate_id).raw_data
        positions = stored["lots"]["lot_1"]["proposals"]["contractor_1"]["contractor_items"][
            "positions"
        ]
        assert positions["1"]["article_smr"] == "СМР-42"


# ---------------------------------------------------------------------------
#  Материализация резолва статьи в position_items (Ф3)
# ---------------------------------------------------------------------------

class TestCategoryMaterialization:
    """Ф3: план резолвера доезжает до строк сметы (спека Ф3 §2.8)."""

    @pytest.fixture
    def contract(self, factories):
        return factories.ContractFactory.create()

    def test_positions_point_at_their_chapter_row(self, db_session, resolver, contract):
        payload = payload_for(
            contract,
            [
                position(job_title="1 Подготовительные работы", number="1",
                         chapter_number="1", article_smr="1. Подготовительные работы",
                         is_chapter=True),
                position(job_title="Расчистка", number="2", unit="м2",
                         suggested_quantity=10, unit_cost_total="100.00"),
            ],
        )
        outcome = run_import(db_session, resolver, contract, payload)
        items = _items_by_key(db_session, outcome.estimate_id)
        chapter, work = items["1"], items["2"]
        assert chapter.is_chapter is True
        assert chapter.work_category_id is not None
        assert chapter.category_source == "file"
        assert chapter.smr_article_raw == "1. Подготовительные работы"
        assert work.chapter_item_id == chapter.id
        assert work.work_category_id is None

    def test_row_outside_structure_is_not_attached(self, db_session, resolver, contract):
        """Агрегатная строка допработ: chapter_item_id остаётся NULL (спека §2.6)."""
        payload = payload_for(
            contract,
            [
                position(job_title="1 Подготовительные работы", number="1",
                         chapter_number="1", article_smr="1. Подготовительные работы",
                         is_chapter=True),
                position(job_title="Расчистка", number="2", total_cost_total="500.00"),
                position(job_title="Дополнительные работы", number=None,
                         chapter_number=None, total_cost_total="700.00"),
            ],
        )
        outcome = run_import(db_session, resolver, contract, payload)
        items = _items_by_key(db_session, outcome.estimate_id)
        assert items["3"].chapter_item_id is None
        assert items["2"].chapter_item_id == items["1"].id
        assert any("вне структуры" in w or "без номера" in w for w in outcome.warnings)

    def test_chapter_item_id_never_crosses_a_proposal(self, db_session, resolver, contract):
        """Два лота по одному предложению — поперечных ссылок нет (спека §4.2)."""
        first = proposal([
            position(job_title="1 Подготовительные работы", number="1", chapter_number="1",
                     article_smr="1. Подготовительные работы", is_chapter=True),
            position(job_title="Расчистка", number="2"),
        ])
        second = proposal([
            position(job_title="4 Возведение конструкций", number="1", chapter_number="4",
                     article_smr="4. Возведение конструкций", is_chapter=True),
            position(job_title="Монолит", number="2"),
        ])
        payload = payload_for(contract)
        payload[JSON_KEY_LOTS] = {
            "lot_1": {JSON_KEY_LOT_TITLE: "Лот №1", JSON_KEY_PROPOSALS: {"contractor_1": first},
                      JSON_KEY_BASELINE_PROPOSAL: {JSON_KEY_CONTRACTOR_TITLE: BASELINE_MISSING_TITLE}},
            "lot_2": {JSON_KEY_LOT_TITLE: "Лот №2", JSON_KEY_PROPOSALS: {"contractor_1": second},
                      JSON_KEY_BASELINE_PROPOSAL: {JSON_KEY_CONTRACTOR_TITLE: BASELINE_MISSING_TITLE}},
        }
        run_import(db_session, resolver, contract, payload)
        crossing = db_session.execute(
            sa.text(
                "select count(*) from position_items child "
                "join position_items parent on parent.id = child.chapter_item_id "
                "where child.proposal_id <> parent.proposal_id"
            )
        ).scalar_one()
        assert crossing == 0
        # И привязка при этом есть — иначе ноль был бы вакуозным.
        attached = db_session.execute(
            sa.text("select count(*) from position_items where chapter_item_id is not null")
        ).scalar_one()
        assert attached == 2

    def test_disabled_structure_keeps_rows_and_raw_but_no_links(
        self, db_session, resolver, contract
    ):
        payload = payload_for(
            contract,
            [
                position(job_title="1 Подготовительные работы", number="1", chapter_number="1",
                         article_smr="1. Подготовительные работы", is_chapter=True),
                # Статья на НЕ-разделе внутри деградации D: если резолвер её
                # материализует, импорт падает о ck_position_items_article_only_on_chapters
                # и теряет смету целиком. Пока строка не несла статью, гейт не стерёг
                # никто (найдено финальным ревью).
                position(job_title="Расчистка", number="2", total_cost_total="500.00",
                         article_smr="4.1. Ж/Б конструкции"),
                position(job_title="Примечание", number="3", chapter_number="прим.",
                         is_chapter=True),
            ],
        )
        outcome = run_import(db_session, resolver, contract, payload)
        items = _items_by_key(db_session, outcome.estimate_id)
        assert len(items) == 3
        assert items["1"].smr_article_raw == "1. Подготовительные работы"
        assert items["2"].smr_article_raw is None
        assert any("не раздел" in w for w in outcome.warnings)
        assert all(i.work_category_id is None for i in items.values())
        assert all(i.category_source is None for i in items.values())
        assert all(i.chapter_item_id is None for i in items.values())
        assert any("не определена" in w for w in outcome.warnings)

    def test_non_canonical_position_keys_fail_the_import_with_an_explanation(
        self, db_session, resolver, contract
    ):
        payload = payload_for(contract, [position(job_title="Расчистка", number="1")])
        positions = payload[JSON_KEY_LOTS]["lot_1"][JSON_KEY_PROPOSALS]["contractor_1"][
            JSON_KEY_CONTRACTOR_ITEMS
        ][JSON_KEY_CONTRACTOR_POSITIONS]
        positions["07"] = positions.pop("1")
        with pytest.raises(EstimateImportError, match="1..N"):
            run_import(db_session, resolver, contract, payload)

    def test_a_non_dict_row_fails_the_import_instead_of_being_dropped(
        self, db_session, resolver, contract
    ):
        """Осознанное изменение поведения на пути, который не покрывал никто.

        Раньше `_import_positions` молча пропускал не-словарь (строка терялась без
        следа); теперь импорт отказывает с объяснением. Парсер такого не отдаёт —
        `postprocess.annotate` падает раньше, — но тихая потеря строки сметы
        недопустима, а неохваченное поведение не значит «правильное».
        """
        payload = payload_for(contract, [position(job_title="Расчистка", number="1")])
        positions = payload[JSON_KEY_LOTS]["lot_1"][JSON_KEY_PROPOSALS]["contractor_1"][
            JSON_KEY_CONTRACTOR_ITEMS
        ][JSON_KEY_CONTRACTOR_POSITIONS]
        positions["2"] = "не словарь"
        with pytest.raises(EstimateImportError, match="не является словарём"):
            run_import(db_session, resolver, contract, payload)

    def test_replace_removes_an_estimate_with_filled_chapter_links(
        self, db_session, resolver, contract
    ):
        """RESTRICT на составном self-FK каскаду не мешает (спека §1.3 факт 8).

        Замер на scratch-таблицах это показал; здесь то же свойство проверяется на
        настоящей схеме и настоящем replace-флоу.
        """
        rows_with_links = [
            position(job_title="1 Подготовительные работы", number="1", chapter_number="1",
                     article_smr="1. Подготовительные работы", is_chapter=True),
            position(job_title="1.1 Расчистка", number="2", chapter_number="1.1",
                     is_chapter=True),
            position(job_title="Вывоз грунта", number="3", total_cost_total="500.00"),
        ]
        first = run_import(db_session, resolver, contract, payload_for(contract, rows_with_links))
        filled = db_session.execute(
            sa.text("select count(*) from position_items where chapter_item_id is not null")
        ).scalar_one()
        assert filled == 2            # иначе замена ничего не доказывает

        second = run_import(
            db_session, resolver, contract, payload_for(contract, rows_with_links), replace=True
        )
        assert second.replaced_estimate_id == first.estimate_id
        assert db_session.get(Estimate, first.estimate_id) is None
        assert len(_items_by_key(db_session, second.estimate_id)) == 3


# ---------------------------------------------------------------------------
#  Допработы: расшивка «Сведений», estimate_additional_works (Ф4, спека §2.7-§2.9)
# ---------------------------------------------------------------------------

def _additional_works_of(db_session, estimate_id: int) -> list[EstimateAdditionalWork]:
    return db_session.execute(
        sa.select(EstimateAdditionalWork)
        .join(Proposal, Proposal.id == EstimateAdditionalWork.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_id)
        .order_by(Proposal.id, EstimateAdditionalWork.ordinal)
    ).scalars().all()


class TestAdditionalWorks:
    """Ф4: расшивка агрегатной строки допработ по строкам «Сведений»."""

    @pytest.fixture
    def contract(self, factories):
        return factories.ContractFactory.create()

    def test_additional_works_rows_are_created(self, db_session, resolver, contract):
        data = payload_for(
            contract,
            [position(job_title="Обычная работа", unit="м2", total_cost_total="1000.00")],
            additional_works=additional_works_row(total="300.00"),
            additional_info=svedeniya_info(
                "Работа А - 100 руб.",
                "Работа Б - 150 руб.",
                "Работа В - 50 руб.",
            ),
        )

        outcome = run_import(db_session, resolver, contract, data)

        rows = _additional_works_of(db_session, outcome.estimate_id)
        assert [r.ordinal for r in rows] == [1, 2, 3]
        assert [r.title for r in rows] == ["Работа А", "Работа Б", "Работа В"]
        assert [r.total_amount for r in rows] == [Decimal("100"), Decimal("150"), Decimal("50")]
        assert all(isinstance(r.total_amount, Decimal) for r in rows)

    def test_aggregate_row_does_not_become_a_position(self, db_session, resolver, contract):
        data = payload_for(
            contract,
            [position(job_title="Обычная работа", unit="м2", total_cost_total="1000.00")],
            additional_works=additional_works_row(total="300.00"),
        )

        outcome = run_import(db_session, resolver, contract, data)

        titles = db_session.execute(
            sa.select(PositionItem.job_title_in_proposal)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == outcome.estimate_id)
        ).scalars().all()
        assert "Дополнительные работы" not in titles
        assert outcome.positions_total == 1

    def test_stale_1_1_0_shape_is_rejected(self, db_session, resolver, contract):
        """Копия агрегатной строки в `positions` — форма парсера 1.1.0 (спека §2.7)."""
        stale_copy = position(
            job_title="Дополнительные работы",
            number=None,
            chapter_number=None,
            total_cost_total="300.00",
        )
        data = payload_for(
            contract,
            [
                position(job_title="Обычная работа", unit="м2", total_cost_total="1000.00"),
                stale_copy,
            ],
            additional_works=additional_works_row(total="300.00"),
        )

        with pytest.raises(EstimateImportError, match=re.escape("1.1.0")):
            run_import(db_session, resolver, contract, data)
        # `import_estimate` не управляет транзакцией сама (§5): в проде откат
        # делает `with db.begin():` вокруг вызова (`import_pipeline.py`), здесь —
        # явный rollback, воспроизводящий то же самое для "сырого" `db_session`.
        db_session.rollback()

        estimate_count = db_session.execute(
            sa.select(sa.func.count()).select_from(Estimate).where(Estimate.contract_id == contract.id)
        ).scalar_one()
        assert estimate_count == 0
        work_count = db_session.execute(
            sa.select(sa.func.count()).select_from(EstimateAdditionalWork)
        ).scalar_one()
        assert work_count == 0

    def test_stale_shape_with_inner_whitespace_in_the_title_is_also_rejected(
        self, db_session, resolver, contract
    ):
        """Гейт нормализует название ТОЙ ЖЕ функцией, что и парсер.

        Парсер 1.1.0 распознавал агрегатную строку через `normalized_cell_text`
        (схлопывает ВНУТРЕННИЕ пробельные последовательности) и клал в оба места
        СЫРОЕ название. Гейт, обрезающий только края, такую копию пропустил бы —
        и двойной счёт прошёл бы ровно через защиту, которая от него поставлена.
        Найдено финальным ревью; спека §2.7 говорит «нормализованное название»,
        а нормализатор в проекте один.
        """
        stale_copy = position(
            job_title="Дополнительные  работы",  # два пробела внутри — как в файле
            number=None,
            chapter_number=None,
            total_cost_total="300.00",
        )
        data = payload_for(
            contract,
            [
                position(job_title="Обычная работа", unit="м2", total_cost_total="1000.00"),
                stale_copy,
            ],
            additional_works=additional_works_row(total="300.00"),
        )

        with pytest.raises(EstimateImportError, match=re.escape("1.1.0")):
            run_import(db_session, resolver, contract, data)
        db_session.rollback()

    def test_same_title_with_other_money_is_not_the_stale_shape(self, db_session, resolver, contract):
        """Тот же заголовок, ДРУГИЕ деньги — обычная строка «вне структуры» Ф3."""
        outside_row = position(
            job_title="Дополнительные работы",
            number=None,
            chapter_number=None,
            total_cost_total="700.00",
        )
        data = payload_for(
            contract,
            [
                position(job_title="Обычная работа", unit="м2", total_cost_total="1000.00"),
                outside_row,
            ],
            additional_works=additional_works_row(total="300.00"),
        )

        outcome = run_import(db_session, resolver, contract, data)

        items = _items_by_key(db_session, outcome.estimate_id)
        assert items["2"].job_title_in_proposal == "Дополнительные работы"
        assert any("вне структуры" in w or "без номера" in w for w in outcome.warnings)

    def test_owner_is_ambiguous_across_two_lots(self, db_session, resolver, contract):
        """Текст «Сведений» здесь НЕПУСТ, и это не декорация.

        С пустым текстом тест был бы вакуозен: расшивать было бы нечего, и
        снятие правила владельца («расшить каждому» либо «расшить первый лот»)
        не изменило бы ни одной записи — обе ветки дали бы одну
        нераспределённую запись на свой `T`. Пробел найден негативной проверкой
        Task 7 ([verifying-guards.md](../../../docs/insights/verifying-guards.md),
        слой 7: снятие, которое ничего не валит, означает, что защиты нет).
        Текст в обоих предложениях один и тот же — он факт уровня листа
        (спека §1.5 факт 2).
        """
        shared_text = svedeniya_info("1 Работа по разделу - 300 руб.")
        data = payload_for(
            contract,
            [position(job_title="Обычная работа 1", unit="м2", total_cost_total="1000.00")],
            additional_works=additional_works_row(total="300.00"),
            additional_info=shared_text,
        )
        data[JSON_KEY_LOTS]["lot_2"] = {
            JSON_KEY_LOT_TITLE: "Лот №2",
            JSON_KEY_PROPOSALS: {
                "contractor_1": proposal(
                    [position(job_title="Обычная работа 2", unit="м2", total_cost_total="2000.00")],
                    additional_works=additional_works_row(total="500.00"),
                    additional_info=shared_text,
                )
            },
            JSON_KEY_BASELINE_PROPOSAL: {JSON_KEY_CONTRACTOR_TITLE: BASELINE_MISSING_TITLE},
        }

        outcome = run_import(db_session, resolver, contract, data)

        # СЧЁТОМ, а не any(...): спека §2.2 требует РОВНО одно предупреждение на
        # смету, и «есть такое» прошло бы и при двух копиях по числу предложений.
        assert len([w for w in outcome.warnings if "неоднозначен" in w]) == 1
        # И ни одного per-proposal «не расшиты»: причину уже назвало то самое
        # одно предупреждение (снятие этого молчания раньше не роняло ничего).
        assert [w for w in outcome.warnings if "не расшиты" in w or "осталась нераспределённой" in w] == []
        rows = _additional_works_of(db_session, outcome.estimate_id)
        assert sorted(r.total_amount for r in rows) == [Decimal("300.00"), Decimal("500.00")]
        # Ни одна из двух записей не расшита — общий текст не применён никому.
        assert all(r.chapter_ref_raw is None and r.raw_line is None for r in rows)

    def test_only_the_owner_gets_the_breakdown(self, db_session, resolver, contract):
        data = payload_for(
            contract,
            [position(job_title="Обычная работа 1", unit="м2", total_cost_total="1000.00")],
            additional_works=additional_works_row(total="300.00"),
            additional_info=svedeniya_info("Работа А - 300 руб."),
        )
        data[JSON_KEY_LOTS]["lot_2"] = {
            JSON_KEY_LOT_TITLE: "Лот №2",
            JSON_KEY_PROPOSALS: {
                "contractor_1": proposal(
                    [position(job_title="Обычная работа 2", unit="м2", total_cost_total="2000.00")]
                )
            },
            JSON_KEY_BASELINE_PROPOSAL: {JSON_KEY_CONTRACTOR_TITLE: BASELINE_MISSING_TITLE},
        }

        outcome = run_import(db_session, resolver, contract, data)

        rows = _additional_works_of(db_session, outcome.estimate_id)
        assert len(rows) == 1
        assert (rows[0].title, rows[0].total_amount) == ("Работа А", Decimal("300"))

    def test_replace_over_an_estimate_with_additional_works(self, db_session, resolver, contract):
        data = payload_for(
            contract,
            [position(job_title="Обычная работа", unit="м2", total_cost_total="1000.00")],
            additional_works=additional_works_row(total="300.00"),
        )
        first = run_import(db_session, resolver, contract, data)
        assert len(_additional_works_of(db_session, first.estimate_id)) == 1

        second = run_import(db_session, resolver, contract, data, replace=True)

        assert second.replaced_estimate_id == first.estimate_id
        assert _additional_works_of(db_session, first.estimate_id) == []
        assert len(_additional_works_of(db_session, second.estimate_id)) == 1

    def test_unallocated_remainder_warns_with_both_amounts(self, db_session, resolver, contract):
        data = payload_for(
            contract,
            [position(job_title="Обычная работа", unit="м2", total_cost_total="1000.00")],
            additional_works=additional_works_row(total="300.00"),
            additional_info=svedeniya_info("Работа А - 100 руб."),
        )

        outcome = run_import(db_session, resolver, contract, data)

        assert any("Нераспределённый остаток" in w for w in outcome.warnings)

    def test_unallocated_remainder_is_materialized_as_the_last_record(
        self, db_session, resolver, contract
    ):
        """Остаток — не только предупреждение, но и ЗАПИСЬ в БД (спека §2.6).

        Пробел найден негативной проверкой Task 7: снятие записи остатка (при
        сохранённом предупреждении) роняло юнит-тест матрицы и не роняло ни
        одного интеграционного — то есть инвариант «сумма записей предложения
        равна `T`» до БД никем не доводился. Здесь `P < T`, и проверяется
        именно доведённый до БД результат: остаток отдельной записью, с
        последним `ordinal`, без ссылки и без сырья
        ([verifying-guards.md](../../../docs/insights/verifying-guards.md), слой 7).
        """
        data = payload_for(
            contract,
            [position(job_title="Обычная работа", unit="м2", total_cost_total="1000.00")],
            additional_works=additional_works_row(total="300.00"),
            additional_info=svedeniya_info("Работа А - 100 руб."),
        )

        outcome = run_import(db_session, resolver, contract, data)

        rows = _additional_works_of(db_session, outcome.estimate_id)
        assert [r.ordinal for r in rows] == [1, 2]
        assert [r.total_amount for r in rows] == [Decimal("100"), Decimal("200")]
        remainder = rows[-1]
        assert remainder.title == "Дополнительные работы"
        assert remainder.chapter_ref_raw is None
        assert remainder.raw_line is None
        # Инвариант §2.6, доведённый до БД: сумма записей равна контрольной сумме.
        assert sum((r.total_amount for r in rows), Decimal("0")) == Decimal("300.00")

    def test_f3_warning_appears_exactly_once_per_proposal(self, db_session, resolver, contract):
        """План резолва Ф3 строится РОВНО ОДИН раз на предложение (спека §2.8 п.1).

        Ф4 подняла вызов `resolve_proposal` из `_import_positions` в
        `import_estimate`, чтобы его результат достался обоим потребителям —
        позициям и допработам. Второй независимый вызов задвоил бы ВСЕ
        предупреждения Ф3, а не только категорийные (спека §1.5 факт 3), и
        поймать это можно только счётом: `any(...)` прошёл бы и при двух
        копиях. Поэтому здесь `== 1`, а не «есть такое предупреждение».
        """
        data = payload_for(
            contract,
            [
                position(job_title="1 Раздел без статьи", number="1", chapter_number="1",
                         is_chapter=True),
                position(job_title="Работа", number="2", total_cost_total="500.00"),
            ],
            additional_works=additional_works_row(total="300.00"),
        )

        outcome = run_import(db_session, resolver, contract, data)

        unassigned = [w for w in outcome.warnings if "Разделов без статьи" in w]
        assert len(unassigned) == 1, outcome.warnings

    def test_unreadable_line_warns_with_count_and_raw_text(self, db_session, resolver, contract):
        data = payload_for(
            contract,
            [position(job_title="Обычная работа", unit="м2", total_cost_total="1000.00")],
            additional_works=additional_works_row(total="300.00"),
            additional_info=svedeniya_info("Строка без суммы и единиц измерения"),
        )

        outcome = run_import(db_session, resolver, contract, data)

        assert any("Нечитаемых строк" in w for w in outcome.warnings)

    def test_unresolved_reference_warns_with_the_ref_and_reason(self, db_session, resolver, contract):
        data = payload_for(
            contract,
            [position(job_title="Обычная работа", unit="м2", total_cost_total="1000.00")],
            additional_works=additional_works_row(total="300.00"),
            additional_info=svedeniya_info("99 Несуществующий раздел - 300 руб."),
        )

        outcome = run_import(db_session, resolver, contract, data)

        assert any("Ссылка не разрешилась" in w for w in outcome.warnings)


def _items_by_key(db_session, estimate_id: int) -> dict[str, PositionItem]:
    rows = db_session.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_id)
    ).scalars().all()
    return {item.position_key_in_proposal: item for item in rows}
