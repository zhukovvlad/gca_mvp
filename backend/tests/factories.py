"""factory_boy фабрики для интеграционных тестов.

Использование: `user = UserFactory.create()` в тесте, который имеет
фикстуру `db_session`. Фабрики привязываются к session через `_register_session`.
"""
import datetime as dt
from decimal import Decimal

import factory
from factory.alchemy import SQLAlchemyModelFactory

from models import (
    CatalogKind,
    CatalogPosition,
    CatalogStatus,
    Contract,
    Contractor,
    Estimate,
    ImportJob,
    ImportJobStatus,
    Lot,
    ObjectModel,
    PositionItem,
    Proposal,
    RateClass,
    RateStandard,
    UnitOfMeasure,
    User,
    UserRole,
)
from security import hash_password

# Глобальный slot — устанавливается фикстурой db_session
_session_holder: dict = {"session": None}


def _register_session(session) -> None:
    _session_holder["session"] = session


def _require_session():
    session = _session_holder["session"]
    if session is None:
        raise RuntimeError("Session не зарегистрирована. Используй фикстуру `factories`.")
    return session


def _unit_id(code: str) -> int:
    return _require_session().query(UnitOfMeasure).filter_by(code=code).one().id


class _BaseFactory(SQLAlchemyModelFactory):
    class Meta:
        abstract = True
        sqlalchemy_session_persistence = "flush"

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        session = _session_holder["session"]
        if session is None:
            raise RuntimeError("Session не зарегистрирована. Используй фикстуру `factories`.")
        cls._meta.sqlalchemy_session = session
        return super()._create(model_class, *args, **kwargs)


class UserFactory(_BaseFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    # Пароль по умолчанию — "secret"; хэш считается лениво, чтобы тесты могли
    # проверять логин. Override password_hash через .create(password_hash=...).
    password_hash = factory.LazyFunction(lambda: hash_password("secret"))
    role = UserRole.member
    is_active = True


# ---------------------------------------------------------------------------
#  Доменные фабрики (схема фазы 2)
# ---------------------------------------------------------------------------

class RateClassFactory(_BaseFactory):
    class Meta:
        model = RateClass

    title = factory.Sequence(lambda n: f"Класс {n}")


class ObjectFactory(_BaseFactory):
    class Meta:
        model = ObjectModel

    title = factory.Sequence(lambda n: f"Объект {n}")
    address = factory.Sequence(lambda n: f"ул. Тестовая, {n}")


class ContractorFactory(_BaseFactory):
    class Meta:
        model = Contractor

    title = factory.Sequence(lambda n: f"Подрядчик {n}")
    inn = factory.Sequence(lambda n: f"77{n:010d}")
    address = "г. Тест, ул. Подрядная, 1"
    accreditation = "да"


class ContractFactory(_BaseFactory):
    class Meta:
        model = Contract

    object = factory.SubFactory(ObjectFactory)
    contractor = factory.SubFactory(ContractorFactory)
    rate_class = factory.SubFactory(RateClassFactory)
    contract_number = factory.Sequence(lambda n: f"ГП-{n:04d}")
    title = "Договор генподряда"
    signer = "Иванов И.И."
    signed_date = dt.date(2025, 3, 1)


class ImportJobFactory(_BaseFactory):
    class Meta:
        model = ImportJob

    contract = factory.SubFactory(ContractFactory)
    amendment_no = None
    filename = "estimate.xlsx"
    file_key = factory.Sequence(lambda n: f"key-{n:08d}")
    file_sha256 = factory.Sequence(lambda n: f"{n:064x}")
    status = ImportJobStatus.pending.value


class EstimateFactory(_BaseFactory):
    class Meta:
        model = Estimate

    contract = factory.SubFactory(ContractFactory)
    amendment_no = None
    title = "Смета"
    data_prepared_on_date = dt.date(2025, 4, 1)


class LotFactory(_BaseFactory):
    class Meta:
        model = Lot

    estimate = factory.SubFactory(EstimateFactory)
    lot_key = factory.Sequence(lambda n: f"lot_{n}")
    lot_title = "Лот №1"


class ProposalFactory(_BaseFactory):
    class Meta:
        model = Proposal

    lot = factory.SubFactory(LotFactory)
    contractor = factory.SubFactory(ContractorFactory)


class CatalogPositionFactory(_BaseFactory):
    class Meta:
        model = CatalogPosition

    standard_job_title = factory.Sequence(lambda n: f"Работа {n}")
    # Настоящая нормализация приезжает с парсером (фаза 3); в тестах схемы
    # достаточно детерминированной заглушки — важна лишь идентичность пары.
    normalized_job_title = factory.LazyAttribute(lambda o: o.standard_job_title.strip().lower())
    kind = CatalogKind.POSITION.value
    status = CatalogStatus.na.value


class PositionItemFactory(_BaseFactory):
    class Meta:
        model = PositionItem

    proposal = factory.SubFactory(ProposalFactory)
    position_key_in_proposal = factory.Sequence(lambda n: f"pos_{n}")
    job_title_in_proposal = factory.Sequence(lambda n: f"Работа {n}")
    is_chapter = False
    quantity = Decimal("1")
    suggested_quantity = Decimal("10")
    unit_cost_total = Decimal("100.00")
    total_cost_total = Decimal("1000.00")


class RateStandardFactory(_BaseFactory):
    class Meta:
        model = RateStandard

    catalog_position = factory.SubFactory(CatalogPositionFactory)
    rate_class = factory.SubFactory(RateClassFactory)
    standard_unit_rate = Decimal("100.00")
    valid_from = dt.date(2025, 1, 1)
    valid_to = None
