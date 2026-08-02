"""factory_boy фабрики для интеграционных тестов.

Использование: `project = ProjectFactory.create()` в тесте, который имеет
фикстуру `db_session`. Фабрики привязываются к session через `_register_session`.
"""
from datetime import date
from decimal import Decimal

import factory
from factory.alchemy import SQLAlchemyModelFactory

from models import (
    CompensationCorridor,
    Document,
    Invoice,
    InvoiceItem,
    MaterialClass,
    MaterialType,
    Organization,
    OrgRole,
    Project,
    ProjectRole,
    ReferencePrice,
    Supplier,
    UnitOfMeasure,
    User,
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


def _material_type_id(code: str) -> int:
    return _require_session().query(MaterialType).filter_by(code=code).one().id


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


class OrganizationFactory(_BaseFactory):
    class Meta:
        model = Organization

    name = factory.Sequence(lambda n: f"ООО Организация {n}")
    inn = factory.Sequence(lambda n: f"{n + 1000000000:010d}")
    kind = ProjectRole.customer


class UserFactory(_BaseFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    # Пароль по умолчанию — "secret"; хэш считается лениво, чтобы тесты могли
    # проверять логин. Override password_hash через .create(password_hash=...).
    password_hash = factory.LazyFunction(lambda: hash_password("secret"))
    is_superuser = False
    organization = factory.SubFactory(OrganizationFactory)
    org_role = OrgRole.member
    is_active = True


class ProjectFactory(_BaseFactory):
    class Meta:
        model = Project

    name = factory.Sequence(lambda n: f"Объект {n}")
    contract_number = factory.Sequence(lambda n: f"Д-{n:03d}")


class SupplierFactory(_BaseFactory):
    class Meta:
        model = Supplier

    name = factory.Sequence(lambda n: f"ООО Поставщик {n}")
    inn = factory.Sequence(lambda n: f"{n:010d}")


class MaterialClassFactory(_BaseFactory):
    class Meta:
        model = MaterialClass

    # Default to concrete/В25. Tests override material_type_code to switch type.
    class Params:
        material_type_code = "concrete"

    material_type_id = factory.LazyAttribute(lambda obj: _material_type_id(obj.material_type_code))
    name = factory.LazyAttribute(
        lambda obj: {"concrete": "В25", "rebar": "d12", "other": "X"}.get(obj.material_type_code, "X")
    )
    calc_role = "base"


class ReferencePriceFactory(_BaseFactory):
    class Meta:
        model = ReferencePrice

    project = factory.SubFactory(ProjectFactory)
    material_class = factory.SubFactory(MaterialClassFactory)
    unit_id = factory.LazyAttribute(lambda _: _unit_id("M3"))
    price = 8000.0
    period_start = date(2026, 1, 1)
    period_end = date(2026, 12, 31)
    source = "контракт"


class DocumentFactory(_BaseFactory):
    class Meta:
        model = Document

    project = factory.SubFactory(ProjectFactory)
    filename = factory.Sequence(lambda n: f"doc_{n}.pdf")
    s3_key = factory.Sequence(lambda n: f"2026/05/{n}_doc.pdf")
    doc_type = "invoice"
    status = "parsed"


class InvoiceFactory(_BaseFactory):
    class Meta:
        model = Invoice

    document = factory.SubFactory(DocumentFactory)
    supplier_id = None
    number = factory.Sequence(lambda n: f"СФ-{n}")
    date = date(2026, 3, 15)
    supplier_name = "ООО Поставщик"
    supplier_inn = "0000000000"
    vat_rate = 20.0
    ai_confidence = 0.9


class InvoiceItemFactory(_BaseFactory):
    class Meta:
        model = InvoiceItem

    invoice = factory.SubFactory(InvoiceFactory)
    raw_name = "Бетон В25"
    item_type = "material"
    quantity = Decimal("5")
    raw_unit = "м3"
    # ВНИМАНИЕ: дефолт нормализации рассчитан на м³ (multiplier=1, normalized == raw).
    # При override raw_unit на другую единицу (напр. "кг") нужно ЯВНО задать
    # normalized_unit_id / normalized_quantity / normalized_unit_price —
    # иначе фикстура будет несогласованной (кг → тонны, multiplier 0.001).
    normalized_unit_id = factory.LazyAttribute(lambda _: _unit_id("M3"))
    normalized_quantity = factory.LazyAttribute(lambda obj: Decimal(str(obj.quantity)))
    unit_price = Decimal("8000.0")
    # Денежные поля — Decimal, чтобы Numeric-колонки не получали float-артефактов.
    # Decimal(str(...)) делает арифметику устойчивой к int/float/Decimal override в тестах.
    normalized_unit_price = factory.LazyAttribute(lambda obj: Decimal(str(obj.unit_price)))
    # amount и vat_amount выводятся из quantity * unit_price — это предотвращает
    # рассинхронизацию при override quantity. Тесты могут передать amount явно.
    amount = factory.LazyAttribute(lambda obj: Decimal(str(obj.quantity)) * Decimal(str(obj.unit_price)))
    vat_amount = factory.LazyAttribute(lambda obj: round(Decimal(str(obj.amount)) * Decimal("0.20"), 2))


class CompensationCorridorFactory(_BaseFactory):
    class Meta:
        model = CompensationCorridor

    project_id = factory.LazyAttribute(lambda _: ProjectFactory.create().id)
    material_type_id = None
    material_class_id = factory.LazyAttribute(lambda _: MaterialClassFactory.create().id)
    is_compensable = True
    corridor_pct = Decimal("5.00")
