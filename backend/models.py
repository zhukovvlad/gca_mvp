import enum
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.orm import relationship

from database import Base

# ---------------------------------------------------------------------------
#  Auth enums
# ---------------------------------------------------------------------------

class OrgRole(str, enum.Enum):
    """Роль пользователя внутри организации."""
    superadmin = "superadmin"
    admin = "admin"
    member = "member"


class ProjectRole(str, enum.Enum):
    """Роль организации на проекте."""
    customer = "customer"      # заказчик — видит все данные, управляет базовыми ценами
    contractor = "contractor"  # подрядчик — видит только свои загрузки


class UnitDimension(str, enum.Enum):
    """Физическая размерность единицы измерения."""
    mass = "mass"
    volume = "volume"
    length = "length"
    count = "count"


class ItemType(str, enum.Enum):
    """Роль строки счёта в расчёте (ортогональна material_type)."""
    material = "material"
    delivery = "delivery"
    other = "other"


# ---------------------------------------------------------------------------
#  Auth models
# ---------------------------------------------------------------------------

class Organization(Base):
    """Организация — единица изоляции данных."""
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    inn = Column(String, nullable=True, index=True)
    # Роль организации (заказчик/подрядчик). Используем тот же enum, что и
    # ProjectOrganization.project_role — значения идентичны. NOT NULL со
    # server_default='customer': для УПД-трекера типичная организация — заказчик
    # (загружает счета своих подрядчиков); существующие строки миграция заполнит
    # этим значением. native_enum=False → VARCHAR + CHECK (см. примечание у org_role).
    kind = Column(
        SqlEnum(ProjectRole, name="org_kind", native_enum=False),
        nullable=False,
        server_default=ProjectRole.customer.value,
    )
    created_at = Column(DateTime, server_default=sa_text("(now() AT TIME ZONE 'utc')"))

    users = relationship("User", back_populates="organization")
    project_links = relationship("ProjectOrganization", back_populates="organization")


class User(Base):
    """Пользователь системы — принадлежит организации (или суперюзер без org)."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    email = Column(String, nullable=False, unique=True)  # unique уже создаёт индекс в PG
    password_hash = Column(String, nullable=False)
    is_superuser = Column(Boolean, nullable=False, default=False)
    # native_enum=False: хранит VARCHAR с CHECK constraint, а не PG ENUM.
    # Это позволяет добавлять значения без ALTER TYPE и без блокировки таблицы.
    org_role = Column(
        SqlEnum(OrgRole, name="org_role", native_enum=False),
        nullable=True,
    )
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=sa_text("(now() AT TIME ZONE 'utc')"))

    organization = relationship("Organization", back_populates="users")
    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )


class ProjectOrganization(Base):
    """Связь проект ↔ организация с ролью (customer/contractor)."""
    __tablename__ = "project_organizations"

    project_id = Column(Integer, ForeignKey("projects.id"), primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), primary_key=True)
    project_role = Column(
        SqlEnum(ProjectRole, name="project_role", native_enum=False),
        nullable=False,
    )
    created_at = Column(DateTime, server_default=sa_text("(now() AT TIME ZONE 'utc')"))

    project = relationship("Project", back_populates="org_links")
    organization = relationship("Organization", back_populates="project_links")


class RefreshToken(Base):
    """Refresh-токены — хранимые в БД, отзываемые.

    Хранится хэш токена (sha256), сам токен пользователю отдаётся в httpOnly cookie.
    Ротация: при каждом /refresh старый revoked_at проставляется, создаётся новый.
    """
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True)  # unique уже создаёт индекс в PG
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=sa_text("(now() AT TIME ZONE 'utc')"))
    user_agent = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)

    user = relationship("User", back_populates="refresh_tokens")


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    contract_number = Column(String)
    customer_org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    documents = relationship("Document", back_populates="project", cascade="all, delete-orphan")
    reference_prices = relationship("ReferencePrice", back_populates="project", cascade="all, delete-orphan")
    org_links = relationship(
        "ProjectOrganization", back_populates="project", cascade="all, delete-orphan"
    )


class UnitOfMeasure(Base):
    __tablename__ = "units_of_measure"

    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True)   # TON, KG, M3, L, M, PCS
    name = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    dimension = Column(
        SqlEnum(UnitDimension, name="ck_unit_dimension", native_enum=False),
        nullable=False,
    )
    base_unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="RESTRICT"), nullable=True
    )
    to_base_multiplier = Column(Numeric(30, 15), nullable=False, server_default=sa_text("1"))

    base_unit = relationship("UnitOfMeasure", remote_side=[id])

    __table_args__ = (
        CheckConstraint(
            "(base_unit_id IS NOT NULL) OR (to_base_multiplier = 1)",
            name="ck_unit_base_multiplier",
        ),
    )


class UnitAlias(Base):
    __tablename__ = "unit_aliases"

    id = Column(Integer, primary_key=True)
    raw_text = Column(String, nullable=False, unique=True)  # normalize_unit_key() output
    unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="CASCADE"), nullable=False
    )

    unit = relationship("UnitOfMeasure")


class MaterialType(Base):
    __tablename__ = "material_types"

    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True)   # concrete / rebar / other
    name = Column(String, nullable=False)
    default_unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="RESTRICT"), nullable=True
    )

    default_unit = relationship("UnitOfMeasure")


class MaterialClass(Base):
    __tablename__ = "material_classes"

    id = Column(Integer, primary_key=True, index=True)
    material_type_id = Column(
        Integer, ForeignKey("material_types.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    name = Column(String, nullable=False)  # В15, В40
    calc_role = Column(String, nullable=False, default="base")  # base / additive / exclude
    created_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    material_type = relationship("MaterialType")
    reference_prices = relationship("ReferencePrice", back_populates="material_class")
    invoice_items = relationship("InvoiceItem", back_populates="material_class")


class ReferencePrice(Base):
    __tablename__ = "reference_prices"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    material_class_id = Column(Integer, ForeignKey("material_classes.id"), nullable=False)
    price = Column(Numeric(19, 4), nullable=False)
    unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="RESTRICT"), nullable=False
    )
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    source = Column(String)

    project = relationship("Project", back_populates="reference_prices")
    material_class = relationship("MaterialClass", back_populates="reference_prices")
    unit = relationship("UnitOfMeasure")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    s3_key = Column(String)
    doc_type = Column(String, default="unknown")
    # Статусная модель обработки: pending → processing → parsed | error.
    # server_default='pending' для новых строк; исторические строки не трогаем (бэкфилл — Q2).
    status = Column(String, nullable=False, server_default="pending", default="pending")
    # sha256 hex дайджест файла — дедупликация до парсинга (экономит вызовы AI)
    file_hash = Column(String(64), nullable=True, index=True)
    uploaded_by_org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))
    # Стоимость ИИ-разбора (OpenRouter usage.cost), накопительно по всем разборам
    # документа — upload + reparse + deskew-reparse. USD. Numeric(10,6): доли цента.
    parse_cost_usd = Column(Numeric(10, 6), nullable=False, server_default="0")
    # Число платных вызовов OpenRouter по этому документу (честность накопления).
    parse_count = Column(Integer, nullable=False, server_default="0")
    # Момент захвата обработки (guard S0-5) — для детекции зависших задач.
    processing_started_at = Column(DateTime, nullable=True)
    # Человекочитаемая причина последней ошибки (раньше жила только в логах).
    last_error = Column(String, nullable=True)
    # Ownership-токен запуска. Зарезервировано под ступень 2 (поздний retry);
    # на ступени 0 всегда NULL, но колонка заводится сразу, чтобы не делать вторую миграцию.
    processing_run_id = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "file_hash", name="uq_documents_project_file_hash"),
    )

    project = relationship("Project", back_populates="documents")
    invoices = relationship("Invoice", back_populates="document", cascade="all, delete-orphan")


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    inn = Column(String, nullable=True, unique=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    invoices = relationship("Invoice", back_populates="supplier")

    __table_args__ = (
        # Оба уже в БД (миграция b3c7e9f12a45) — объявляем как метаданные.
        Index("ix_suppliers_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
        Index("uq_suppliers_name_no_inn", "name", unique=True, postgresql_where=sa_text("inn IS NULL")),
    )


class ProjectSupplierExclusion(Base):
    __tablename__ = "project_supplier_exclusions"

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id", ondelete="CASCADE"), primary_key=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=sa_text("(now() AT TIME ZONE 'utc')"))


class CompensationCorridor(Base):
    """Corridor rule for a project: type-level default or class-level override.

    Exactly one of material_type_id / material_class_id is set (chk_corridor_target_exclusive).
    is_compensable=true requires corridor_pct (chk_corridor_pct_required_if_compensable).
    Whitelist default: no row → not compensable.
    Fallback: class-level → type-level → no row.
    """
    __tablename__ = "compensation_corridors"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    material_type_id = Column(
        Integer, ForeignKey("material_types.id", ondelete="RESTRICT"), nullable=True
    )
    material_class_id = Column(
        Integer, ForeignKey("material_classes.id", ondelete="CASCADE"), nullable=True
    )
    is_compensable = Column(Boolean, nullable=False, default=False)
    corridor_pct = Column(Numeric(5, 2), nullable=True)
    created_at = Column(DateTime, server_default=sa_text("(now() AT TIME ZONE 'utc')"))
    updated_at = Column(
        DateTime,
        server_default=sa_text("(now() AT TIME ZONE 'utc')"),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
    )

    __table_args__ = (
        CheckConstraint(
            "(material_type_id IS NOT NULL AND material_class_id IS NULL) OR "
            "(material_type_id IS NULL AND material_class_id IS NOT NULL)",
            name="chk_corridor_target_exclusive",
        ),
        CheckConstraint(
            "(is_compensable IS FALSE) OR (is_compensable IS TRUE AND corridor_pct IS NOT NULL)",
            name="chk_corridor_pct_required_if_compensable",
        ),
        Index(
            "uq_corridor_project_type", "project_id", "material_type_id",
            unique=True, postgresql_where=sa_text("material_class_id IS NULL"),
        ),
        Index(
            "uq_corridor_project_class", "project_id", "material_class_id",
            unique=True, postgresql_where=sa_text("material_type_id IS NULL"),
        ),
    )

    material_type = relationship("MaterialType")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True, index=True)
    number = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    supplier_name = Column(String)
    supplier_inn = Column(String)
    vat_rate = Column(Numeric(5, 2), default=20.0)
    ai_confidence = Column(Float)
    verified = Column(Boolean, default=False, nullable=False)
    verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    __table_args__ = (
        Index("ix_invoices_document_id_date", "document_id", "date"),
    )

    document = relationship("Document", back_populates="invoices")
    supplier = relationship("Supplier", back_populates="invoices")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    raw_name = Column(String)
    item_type = Column(
        SqlEnum(ItemType, name="ck_item_type", native_enum=False), nullable=False
    )
    material_class_id = Column(Integer, ForeignKey("material_classes.id"), nullable=True)
    quantity = Column(Numeric(15, 4), nullable=False)
    raw_unit = Column(String)
    normalized_unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    normalized_quantity = Column(Numeric(20, 6), nullable=True)
    normalized_unit_price = Column(Numeric(24, 6), nullable=True)
    unit_price = Column(Numeric(19, 4), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    vat_amount = Column(Numeric(15, 2))

    __table_args__ = (
        # Уже в БД (миграция c7d8e9f0a1b2) — объявляем, чтобы autogenerate не предлагал drop.
        Index("ix_invoice_items_invoice_id_item_type", "invoice_id", "item_type"),
    )

    invoice = relationship("Invoice", back_populates="items")
    material_class = relationship("MaterialClass", back_populates="invoice_items")
    normalized_unit = relationship("UnitOfMeasure")
