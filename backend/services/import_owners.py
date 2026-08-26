"""Владелец сметы при импорте — кто владеет строкой `estimates`, с чем сверять
шапку файла, что писать в предложение (спека контура §2.4).

ОДИН dataclass с тегом `kind` и три конструктора вместо иерархии классов
(план, Р2): `import_estimate` читает поля владельца, а не ветвится по типу.
Спека называет три типа — `ContractEstimateOwner`, `OfferEstimateOwner`,
`BaselineEstimateOwner`; здесь они — три функции, возвращающие `EstimateOwner`
с соответствующим `kind`.

Истина шапки (`HeaderTruth`) — то, с чем сверяется файл, и из файла ничего не
апсертится (§3): у договора — карточка договора; у предложения — карточка
ТЕНДЕРА для объекта и карточка ПОДРЯДЧИКА из пакета; у baseline подрядчика нет.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from models import Contract, Contractor, Offer, Tender, TenderRound

OwnerKind = Literal["contract", "offer", "baseline"]


@dataclass(frozen=True)
class HeaderTruth:
    """Реквизиты, с которыми сверяется шапка XLSX. `None` — сверять нечем."""

    object_title: str | None
    object_address: str | None
    contractor_title: str | None
    contractor_inn: str | None
    contractor_address: str | None
    contractor_accreditation: str | None


@dataclass(frozen=True)
class EstimateOwner:
    kind: OwnerKind
    #: Владелец сметы — ровно одно из трёх не None (CHECK схемы). Хранятся
    #: конкретные id, а не словарь: frozen=True не защитил бы содержимое dict.
    contract_id: int | None
    amendment_no: int | None
    offer_id: int | None
    round_id: int | None
    #: Подрядчик предложения: из карточки договора / из пакета; у baseline None.
    proposal_contractor_id: int | None
    is_baseline: bool
    truth: HeaderTruth

    def estimate_columns(self) -> dict[str, Any]:
        """Колонки владельца для `Estimate(...)`."""
        if self.kind == "contract":
            return {"contract_id": self.contract_id, "amendment_no": self.amendment_no}
        if self.kind == "offer":
            return {"offer_id": self.offer_id}
        return {"round_id": self.round_id}

    @property
    def replace_scope(self) -> tuple[int, int | None] | None:
        """Пара для `_replace_existing`; у раундовых владельцев None — замену
        раунда делает `round_import` ДО цикла (§2.6)."""
        if self.kind == "contract" and self.contract_id is not None:
            return (self.contract_id, self.amendment_no)
        return None

    @property
    def imports_additional_works(self) -> bool:
        """Контур допработ (`decide_owner`, `_import_additional_works`) — не для
        baseline: у базы нет «Сведений», а `additional_info` вырезан
        постобработкой (спека §1.4, §2.9)."""
        return self.kind != "baseline"

    @property
    def reads_deviation(self) -> bool:
        """`deviation_from_baseline_cost` читается только у offer-позиций (§2.10)."""
        return self.kind == "offer"

    @property
    def warns_on_unexpected_baseline(self) -> bool:
        """«В сметах ГП baseline нет» — предупреждение договорного пути; у
        раунда baseline ожидаем."""
        return self.kind == "contract"


def contract_estimate_owner(contract: Contract, amendment_no: int | None) -> EstimateOwner:
    """Договор: истина — карточка договора, но у подрядчика ТОЛЬКО название и
    ИНН (спека §2.4, ревизия гейта 3): договорная сверка адрес и аккредитацию не
    проверяла и не начинает — иначе изменилось бы поведение договорного импорта."""
    return EstimateOwner(
        kind="contract",
        contract_id=contract.id, amendment_no=amendment_no, offer_id=None, round_id=None,
        proposal_contractor_id=contract.contractor_id,
        is_baseline=False,
        truth=HeaderTruth(
            object_title=contract.object.title,
            object_address=contract.object.address,
            contractor_title=contract.contractor.title,
            contractor_inn=contract.contractor.inn,
            contractor_address=None,
            contractor_accreditation=None,
        ),
    )


def offer_estimate_owner(offer: Offer, tender: Tender, contractor: Contractor) -> EstimateOwner:
    """Предложение: объект из карточки тендера, подрядчик — ВСЕ четыре поля из
    пакета (спека §2.4)."""
    return EstimateOwner(
        kind="offer",
        contract_id=None, amendment_no=None, offer_id=offer.id, round_id=None,
        proposal_contractor_id=contractor.id,
        is_baseline=False,
        truth=HeaderTruth(
            object_title=tender.object.title,
            object_address=tender.object.address,
            contractor_title=contractor.title,
            contractor_inn=contractor.inn,
            contractor_address=contractor.address,
            contractor_accreditation=contractor.accreditation,
        ),
    )


def baseline_estimate_owner(tender_round: TenderRound, tender: Tender) -> EstimateOwner:
    return EstimateOwner(
        kind="baseline",
        contract_id=None, amendment_no=None, offer_id=None, round_id=tender_round.id,
        proposal_contractor_id=None,
        is_baseline=True,
        truth=HeaderTruth(
            object_title=tender.object.title,
            object_address=tender.object.address,
            contractor_title=None,
            contractor_inn=None,
            contractor_address=None,
            contractor_accreditation=None,
        ),
    )
