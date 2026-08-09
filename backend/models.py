import enum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
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
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import relationship

from database import Base


def _created_at() -> Column:
    return Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))


def _updated_at() -> Column:
    return Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        onupdate=sa_text("now()"),
    )


#: Набор "пробельных" символов для CHECK на непустое название (btrim по явным
#: кодовым точкам, включая неразрывный пробел chr(160) — locale-независимо).
#: Общая идиома для `work_categories` (Ф1) и `estimate_additional_works` (Ф4):
#: один набор символов, а не два места, которые могли бы молча разойтись.
TITLE_BLANK_CHARS_SQL = "' ' || chr(9) || chr(10) || chr(13) || chr(160)"

# ---------------------------------------------------------------------------
#  Enums
# ---------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    """Роль пользователя (single-tenant, AGENTS.md §3).

    admin — управление пользователями, классами, нормативами, замена/удаление смет;
    member — чтение, загрузка смет, ручной матчинг (Review).
    """
    admin = "admin"
    member = "member"


class UnitDimension(str, enum.Enum):
    """Физическая размерность единицы измерения."""
    mass = "mass"
    volume = "volume"
    area = "area"
    length = "length"
    count = "count"
    time = "time"


# ---------------------------------------------------------------------------
#  Auth models
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, unique=True)  # unique уже создаёт индекс в PG
    password_hash = Column(String, nullable=False)
    # native_enum=False: хранит VARCHAR с CHECK constraint, а не PG ENUM.
    # Это позволяет добавлять значения без ALTER TYPE и без блокировки таблицы.
    role = Column(
        SqlEnum(UserRole, name="user_role", native_enum=False),
        nullable=False,
        server_default=UserRole.member.value,
    )
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=sa_text("(now() AT TIME ZONE 'utc')"))

    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )


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


# ---------------------------------------------------------------------------
#  Единицы измерения (UnitOfMeasure + UnitAlias из udp-tenders; слияние с
#  units из tenders-go — фаза 2)
# ---------------------------------------------------------------------------

class UnitOfMeasure(Base):
    __tablename__ = "units_of_measure"

    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True)   # TON, KG, M3, M2, M, PCS, ...
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


# ---------------------------------------------------------------------------
#  Доменные перечисления
#
#  Значения хранятся в БД строками (varchar/text) + CHECK-констрейнт, а не
#  PG ENUM: добавление значения не требует ALTER TYPE и блокировки таблицы.
#  Python-энумы здесь — для кода (сравнения, литералы), к типу колонки они
#  НЕ привязаны: колонка объявлена как Text/String, чтобы отражение схемы
#  совпадало с миграцией байт-в-байт (alembic check).
# ---------------------------------------------------------------------------

class CatalogKind(str, enum.Enum):
    """Классификация каталожной строки (AGENTS.md §4).

    POSITION    — работа, участвует в матчинге, нормативах и матрице;
    HEADER      — заголовок раздела внутри сметы;
    LOT_HEADER  — заголовок лота;
    TRASH       — мусорная строка (не работа);
    TO_REVIEW   — создана автоматически при промахе матчинга, ждёт решения оператора.
    """
    POSITION = "POSITION"
    HEADER = "HEADER"
    LOT_HEADER = "LOT_HEADER"
    TRASH = "TRASH"
    TO_REVIEW = "TO_REVIEW"


class CatalogStatus(str, enum.Enum):
    """Жизненный цикл каталожной строки (перенос из tenders-go)."""
    pending_indexing = "pending_indexing"
    active = "active"
    deprecated = "deprecated"
    archived = "archived"
    na = "na"


class MatchSource(str, enum.Enum):
    """Происхождение записи matching_cache (AGENTS.md §4).

    auto   — поставлена автоматикой, TTL 30 дней, продлевается при hit;
    manual — решение оператора из Review, expires_at = NULL, не истекает.
    """
    auto = "auto"
    manual = "manual"


class ImportJobStatus(str, enum.Enum):
    """Статусы задания импорта (AGENTS.md §4, §5)."""
    pending = "pending"
    parsing = "parsing"
    importing = "importing"
    matching = "matching"
    done = "done"
    error = "error"


#: Терминальные статусы: только они снимают лок на пару (contract_id, amendment_no)
#: и только они не подлежат startup-recovery (§5). Значение продублировано в
#: частичном уникальном индексе uq_import_jobs_active_pair — менять синхронно.
TERMINAL_IMPORT_JOB_STATUSES = (ImportJobStatus.done, ImportJobStatus.error)

#: Незавершённые статусы — те, что startup-recovery переводит в error (§5).
ACTIVE_IMPORT_JOB_STATUSES = tuple(
    s for s in ImportJobStatus if s not in TERMINAL_IMPORT_JOB_STATUSES
)


def _sql_str_list(values) -> str:
    """('a', 'b') — литерал списка для CHECK/WHERE в миграциях и __table_args__."""
    return ", ".join(f"'{v.value if isinstance(v, enum.Enum) else v}'" for v in values)


#: Дублирует миграцию 0009 намеренно; расхождение ловят parity-тесты
#: test_schema_constraints.py — `alembic check` для Computed и CHECK бесполезен
#: (спека Ф5 §1.5 п. 4).
OBJECT_AREA_TOTAL_EXPRESSION = "area_aboveground_sp + area_underground_sp"


# ---------------------------------------------------------------------------
#  Справочники объектов и подрядчиков (перенос из tenders-go)
# ---------------------------------------------------------------------------

class RateClass(Base):
    """Класс объекта: единица группировки нормативов расценок (AGENTS.md §1.3, §4)."""
    __tablename__ = "rate_classes"

    id = Column(BigInteger, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (UniqueConstraint("title", name="uq_rate_classes_title"),)


class ObjectModel(Base):
    """Строительный объект. Класс здесь — значение ПО УМОЛЧАНИЮ для новых договоров;
    авторитетен снимок в contracts.rate_class_id (§4)."""
    __tablename__ = "objects"

    id = Column(BigInteger, primary_key=True)
    title = Column(String, nullable=False)
    address = Column(String, nullable=False)
    rate_class_id = Column(
        BigInteger, ForeignKey("rate_classes.id", ondelete="SET NULL"), nullable=True
    )
    # ТЭП объекта (спека Ф5 §2.2): две вводимые площади, третья — вычисляемая.
    # Обе или ни одной (§2.3 п. 3); ноль в части законен и отличим от NULL
    # (§2.3 п. 1); ноль в общей непредставим — она знаменатель руб/м² (§2.3 п. 2).
    area_aboveground_sp = Column(Numeric, nullable=True)
    area_underground_sp = Column(Numeric, nullable=True)
    area_total_sp = Column(
        Numeric,
        Computed(OBJECT_AREA_TOTAL_EXPRESSION, persisted=True),
        nullable=True,
    )
    created_at = _created_at()
    updated_at = _updated_at()

    rate_class = relationship("RateClass")

    __table_args__ = (
        UniqueConstraint("title", name="uq_objects_title"),
        Index("ix_objects_rate_class_id", "rate_class_id"),
        CheckConstraint(
            "area_aboveground_sp IS NULL OR area_aboveground_sp >= 0",
            name="ck_objects_area_aboveground_sp_non_negative",
        ),
        CheckConstraint(
            "area_underground_sp IS NULL OR area_underground_sp >= 0",
            name="ck_objects_area_underground_sp_non_negative",
        ),
        CheckConstraint(
            "area_total_sp IS NULL OR area_total_sp > 0",
            name="ck_objects_area_total_sp_positive",
        ),
        CheckConstraint(
            "(area_aboveground_sp IS NULL) = (area_underground_sp IS NULL)",
            name="ck_objects_areas_both_or_neither",
        ),
    )


class Contractor(Base):
    """Подрядчик. Перенос из tenders-go без изменений."""
    __tablename__ = "contractors"

    id = Column(BigInteger, primary_key=True)
    title = Column(String, nullable=False)
    inn = Column(String, nullable=False)
    address = Column(String, nullable=False)
    accreditation = Column(String, nullable=False)
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (UniqueConstraint("inn", name="uq_contractors_inn"),)


# ---------------------------------------------------------------------------
#  Договор и сметы
# ---------------------------------------------------------------------------

class Contract(Base):
    """Договор генподряда — карточка, которая является источником истины при
    импорте (§3): объект, подрядчик и реквизиты НЕ апсертятся из XLSX."""
    __tablename__ = "contracts"

    id = Column(BigInteger, primary_key=True)
    object_id = Column(BigInteger, ForeignKey("objects.id"), nullable=False)
    contractor_id = Column(BigInteger, ForeignKey("contractors.id"), nullable=False)
    # СНИМОК класса на момент создания договора: переклассификация объекта
    # не меняет отклонения прошлых смет (§4).
    rate_class_id = Column(BigInteger, ForeignKey("rate_classes.id"), nullable=False)

    contract_number = Column(String, nullable=False)
    title = Column(String, nullable=True)
    signer = Column(String, nullable=True)
    # NOT NULL: фолбэк даты сравнения с нормативом, если у сметы нет
    # data_prepared_on_date (§4). Обе даты не могут быть NULL одновременно.
    signed_date = Column(Date, nullable=False)
    total_amount = Column(Numeric, nullable=True)
    notes = Column(Text, nullable=True)
    # Коммерческие условия (спека Ф5 §2.5): три пары «процент + комментарий».
    # Парного CHECK между процентом и комментарием нет — комментарий без
    # процента законен и означает «условие есть, но одним процентом не
    # выражается» (§2.5 п. 1). Ноль в проценте законен и отличим от NULL.
    advance_pct = Column(Numeric, nullable=True)
    advance_note = Column(Text, nullable=True)
    bank_guarantee_pct = Column(Numeric, nullable=True)
    bank_guarantee_note = Column(Text, nullable=True)
    retention_pct = Column(Numeric, nullable=True)
    retention_note = Column(Text, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    object = relationship("ObjectModel")
    contractor = relationship("Contractor")
    rate_class = relationship("RateClass")

    __table_args__ = (
        UniqueConstraint("contract_number", name="uq_contracts_contract_number"),
        CheckConstraint(
            "total_amount IS NULL OR total_amount >= 0",
            name="ck_contracts_total_amount_non_negative",
        ),
        CheckConstraint(
            "advance_pct IS NULL OR (advance_pct >= 0 AND advance_pct <= 100)",
            name="ck_contracts_advance_pct_range",
        ),
        CheckConstraint(
            "bank_guarantee_pct IS NULL OR (bank_guarantee_pct >= 0 AND bank_guarantee_pct <= 100)",
            name="ck_contracts_bank_guarantee_pct_range",
        ),
        CheckConstraint(
            "retention_pct IS NULL OR (retention_pct >= 0 AND retention_pct <= 100)",
            name="ck_contracts_retention_pct_range",
        ),
        Index("ix_contracts_object_id", "object_id"),
        Index("ix_contracts_contractor_id", "contractor_id"),
        Index("ix_contracts_rate_class_id", "rate_class_id"),
    )


class ImportJob(Base):
    """Задание импорта сметы (§4, §5). Записи не удаляются — это аудит."""
    __tablename__ = "import_jobs"

    id = Column(BigInteger, primary_key=True)
    contract_id = Column(BigInteger, ForeignKey("contracts.id"), nullable=False)
    amendment_no = Column(Integer, nullable=True)

    filename = Column(Text, nullable=False)   # оригинальное имя, только в БД (§8)
    file_key = Column(Text, nullable=False)   # непрозрачный ключ на диске (uuid)
    file_sha256 = Column(Text, nullable=False)

    status = Column(Text, nullable=False, server_default=ImportJobStatus.pending.value)
    error_text = Column(Text, nullable=True)
    warnings = Column(JSONB, nullable=False, server_default=sa_text("'[]'::jsonb"))

    positions_total = Column(Integer, nullable=False, server_default=sa_text("0"))
    matched_cache = Column(Integer, nullable=False, server_default=sa_text("0"))
    matched_exact = Column(Integer, nullable=False, server_default=sa_text("0"))
    matched_nonposition = Column(Integer, nullable=False, server_default=sa_text("0"))
    to_review = Column(Integer, nullable=False, server_default=sa_text("0"))

    created_at = _created_at()
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    contract = relationship("Contract")

    __table_args__ = (
        UniqueConstraint("file_key", name="uq_import_jobs_file_key"),
        CheckConstraint(
            f"status IN ({_sql_str_list(ImportJobStatus)})", name="ck_import_jobs_status"
        ),
        # -1 — сентинел в COALESCE(amendment_no, -1) частичного уникального
        # индекса; допсоглашения нумеруются с 1.
        CheckConstraint(
            "amendment_no IS NULL OR amendment_no > 0", name="ck_import_jobs_amendment_no"
        ),
        Index("ix_import_jobs_contract_id", "contract_id", "amendment_no"),
        # Очередь startup-recovery (§5): все незавершённые задания.
        Index(
            "ix_import_jobs_active",
            "id",
            postgresql_where=sa_text(f"status NOT IN ({_sql_str_list(TERMINAL_IMPORT_JOB_STATUSES)})"),
        ),
        # uq_import_jobs_active_pair — частичный уникальный индекс по
        # (contract_id, COALESCE(amendment_no,-1)); выражение Alembic не
        # выражает декларативно, создаётся raw SQL в миграции 0002.
    )


class Estimate(Base):
    """Смета к договору (бывш. tenders). 1 договор : N смет, различаются
    номером допсоглашения; NULL = исходная смета (§4)."""
    __tablename__ = "estimates"

    id = Column(BigInteger, primary_key=True)
    contract_id = Column(BigInteger, ForeignKey("contracts.id"), nullable=False)
    amendment_no = Column(Integer, nullable=True)
    title = Column(String, nullable=True)
    data_prepared_on_date = Column(Date, nullable=True)
    import_job_id = Column(
        BigInteger, ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True
    )
    created_at = _created_at()
    updated_at = _updated_at()

    contract = relationship("Contract")
    import_job = relationship("ImportJob")
    raw_data = relationship(
        "EstimateRawData", back_populates="estimate", uselist=False, cascade="all, delete-orphan"
    )
    lots = relationship("Lot", back_populates="estimate", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "amendment_no IS NULL OR amendment_no > 0", name="ck_estimates_amendment_no"
        ),
        # Отдельного индекса по contract_id нет намеренно: выборки по договору
        # обслуживает uq_estimates_contract_amendment — полный уникальный индекс
        # с ведущей колонкой contract_id (создаётся raw SQL в миграции 0002).
        Index("ix_estimates_import_job_id", "import_job_id"),
        # uq_estimates_contract_amendment — UNIQUE NULLS NOT DISTINCT
        # (contract_id, amendment_no), синтаксис PG16; создаётся raw SQL
        # в миграции 0002. Обычный UNIQUE не годится: NULL-ы в нём различны,
        # и исходную смету можно было бы загрузить дважды.
    )


class EstimateRawData(Base):
    """Полный JSON парсера — источник истины по содержимому файла (§4)."""
    __tablename__ = "estimate_raw_data"

    estimate_id = Column(
        BigInteger, ForeignKey("estimates.id", ondelete="CASCADE"), primary_key=True
    )
    raw_data = Column(JSONB, nullable=False)
    parser_version = Column(Text, nullable=False)
    created_at = _created_at()

    estimate = relationship("Estimate", back_populates="raw_data")


class Lot(Base):
    """Лот сметы. Перенос из tenders-go; FK на estimates — ON DELETE CASCADE,
    этого требует replace-флоу (§5), в отличие от оригинала."""
    __tablename__ = "lots"

    id = Column(BigInteger, primary_key=True)
    estimate_id = Column(
        BigInteger, ForeignKey("estimates.id", ondelete="CASCADE"), nullable=False
    )
    lot_key = Column(String, nullable=False)
    lot_title = Column(String, nullable=False)
    lot_key_parameters = Column(JSONB, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    estimate = relationship("Estimate", back_populates="lots")
    proposal = relationship(
        "Proposal", back_populates="lot", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("estimate_id", "lot_key", name="uq_lots_estimate_lot_key"),
        Index("idx_gin_lots_key_parameters", "lot_key_parameters", postgresql_using="gin"),
    )


class Proposal(Base):
    """Предложение подрядчика по лоту. В сметах ГП — РОВНО ОДНО на лот (§4);
    инвариант закреплён уникальным индексом по lot_id."""
    __tablename__ = "proposals"

    id = Column(BigInteger, primary_key=True)
    lot_id = Column(BigInteger, ForeignKey("lots.id", ondelete="CASCADE"), nullable=False)
    contractor_id = Column(BigInteger, ForeignKey("contractors.id"), nullable=False)
    # В сметах ГП baseline-колонки нет — поле сохранено для 1:1 переноса JSON
    # парсера и задела на возврат тендеров (§4).
    is_baseline = Column(Boolean, nullable=False, server_default=sa_text("false"))
    contractor_coordinate = Column(String(255), nullable=True)
    contractor_width = Column(Integer, nullable=True)
    contractor_height = Column(Integer, nullable=True)
    # Ставка НДС, заявленная в шапке ценового блока (фаза 7, спека Ф4б §2.9) —
    # факт файла, а не наш вывод: деление по блоку итогов служит только
    # перекрёстной проверкой при разборе, поэтому колонки `vat_rate_source`
    # здесь нет — источник у сохранённого значения ровно один. Хранится в
    # процентных пунктах (`20`, не `0.20`).
    vat_rate = Column(Numeric, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    lot = relationship("Lot", back_populates="proposal")
    contractor = relationship("Contractor")
    position_items = relationship(
        "PositionItem", back_populates="proposal", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("lot_id", name="uq_proposals_lot_id"),
        Index("ix_proposals_contractor_id", "contractor_id"),
        # Запрет непредставимого состояния, а не основной фильтр (спека §2.9):
        # защитная конверсия при импорте отсеивает NaN/Infinity и диапазон ДО
        # вставки строки — CHECK стережёт правку мимо приложения.
        CheckConstraint(
            "vat_rate IS NULL OR (vat_rate >= 0 AND vat_rate <= 100)",
            name="ck_proposals_vat_rate",
        ),
    )


class ProposalAdditionalInfo(Base):
    __tablename__ = "proposal_additional_info"

    id = Column(BigInteger, primary_key=True)
    proposal_id = Column(
        BigInteger, ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False
    )
    info_key = Column(Text, nullable=False)
    info_value = Column(Text, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (
        UniqueConstraint("proposal_id", "info_key", name="uq_proposal_additional_info_key"),
    )


class ProposalSummaryLine(Base):
    """Итоговые строки предложения («Итого по смете» и т.п.)."""
    __tablename__ = "proposal_summary_lines"

    id = Column(BigInteger, primary_key=True)
    proposal_id = Column(
        BigInteger, ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False
    )
    summary_key = Column(Text, nullable=False)
    job_title = Column(Text, nullable=False)
    materials_cost = Column(Numeric, nullable=True)
    works_cost = Column(Numeric, nullable=True)
    indirect_costs_cost = Column(Numeric, nullable=True)
    total_cost = Column(Numeric, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (
        UniqueConstraint("proposal_id", "summary_key", name="uq_proposal_summary_lines_key"),
    )


class EstimateAdditionalWork(Base):
    """Расшивка агрегатной строки «Дополнительные работы» по строкам «Сведений
    по дополнительным работам» (фаза 7, спека Ф4, миграция 0007).

    Висит на `proposal_id`, а не на `estimate_id` (отступление от брифа, спека
    §2.3): деньги агрегатной строки принадлежат конкретному предложению, и
    резолв ссылки в статью определён В ЕГО ПРЕДЕЛАХ, а не в пределах сметы
    (спека §2.5) — номера разделов между лотами могут повторяться.
    """
    __tablename__ = "estimate_additional_works"

    id = Column(BigInteger, primary_key=True)
    proposal_id = Column(
        BigInteger,
        ForeignKey(
            "proposals.id", ondelete="CASCADE", name="fk_estimate_additional_works_proposal_id"
        ),
        nullable=False,
    )
    # Порядок строк «Сведений»; нераспределённая запись (остаток или полная
    # сумма при пустых «Сведениях») получает последний ordinal.
    ordinal = Column(Integer, nullable=False)
    # «3.2.2» как в тексте; NULL у нераспределённой записи.
    chapter_ref_raw = Column(Text, nullable=True)
    title = Column(Text, nullable=False)
    total_amount = Column(Numeric, nullable=False)
    # Единственный источник статьи в v1 — ссылка (ck_..._unresolved_ref);
    # category_source не заводится — второй правды об одном факте не нужно
    # (спека §2.3): "привязана/нет" уже выводится из этой колонки.
    work_category_id = Column(
        BigInteger,
        ForeignKey(
            "work_categories.id",
            ondelete="RESTRICT",
            name="fk_estimate_additional_works_work_category_id",
        ),
        nullable=True,
    )
    # Исходная строка «Сведений»; NULL у нераспределённой записи.
    raw_line = Column(Text, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (
        UniqueConstraint(
            "proposal_id", "ordinal", name="uq_estimate_additional_works_proposal_ordinal"
        ),
        CheckConstraint("total_amount >= 0", name="ck_estimate_additional_works_total_amount"),
        CheckConstraint("ordinal > 0", name="ck_estimate_additional_works_ordinal"),
        CheckConstraint(
            f"btrim(title, {TITLE_BLANK_CHARS_SQL}) <> ''",
            name="ck_estimate_additional_works_title_not_blank",
        ),
        CheckConstraint(
            "chapter_ref_raw IS NOT NULL OR work_category_id IS NULL",
            name="ck_estimate_additional_works_unresolved_ref",
        ),
        CheckConstraint(
            "raw_line IS NOT NULL OR (chapter_ref_raw IS NULL AND work_category_id IS NULL)",
            name="ck_estimate_additional_works_raw_line_pairs",
        ),
        # Отдельного индекса по proposal_id нет намеренно — его обслуживает
        # левый префикс uq_estimate_additional_works_proposal_ordinal (тот же
        # приём, что заменил idx_position_items_proposal_id в миграции 0006).
        Index(
            "idx_estimate_additional_works_work_category_id",
            "work_category_id",
            postgresql_where=sa_text("work_category_id IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
#  Каталожный контур
# ---------------------------------------------------------------------------

class CatalogPosition(Base):
    """Каталожная строка. Единица идентичности работы — нормализованное
    название + единица измерения (§3, §4); уникальность именно по этой паре."""
    __tablename__ = "catalog_positions"

    id = Column(BigInteger, primary_key=True)
    standard_job_title = Column(Text, nullable=False)   # отображаемое название
    # normalize(standard_job_title); вычисляется в Python при insert/update.
    # Та же нормализация, что у matcher и cache_key — вторых представлений
    # строки в системе нет (§4).
    normalized_job_title = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    embedding = Column(Vector(768), nullable=True)      # векторный матчинг — вне MVP (§5)
    kind = Column(Text, nullable=False, server_default=CatalogKind.TO_REVIEW.value)
    status = Column(String(50), nullable=False, server_default=CatalogStatus.na.value)
    unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="SET NULL"), nullable=True
    )
    fts_vector = Column(
        TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, COALESCE(standard_job_title, ''::text))",
                 persisted=True),
        nullable=True,
    )
    created_at = _created_at()
    updated_at = _updated_at()

    unit = relationship("UnitOfMeasure")

    __table_args__ = (
        CheckConstraint(f"kind IN ({_sql_str_list(CatalogKind)})", name="ck_catalog_positions_kind"),
        CheckConstraint(
            f"status IN ({_sql_str_list(CatalogStatus)})", name="ck_catalog_positions_status"
        ),
        # Индекса по standard_job_title НЕТ намеренно (миграция 0003): поиск по
        # каталогу — ILIKE '%…%', обычный btree его не обслуживает, зато ронял
        # импорт названий длиннее 2704 байт (`ProgramLimitExceeded`).
        Index("idx_catalog_positions_kind", "kind"),
        Index("idx_cp_status", "status"),
        Index("idx_cp_kind_review", "id", postgresql_where=sa_text("kind = 'TO_REVIEW'")),
        Index("idx_cp_status_pending", "id", postgresql_where=sa_text("status = 'pending_indexing'")),
        Index("idx_catalog_positions_fts", "fts_vector", postgresql_using="gin"),
        Index(
            "idx_cp_kind_pos_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=sa_text("kind = 'POSITION'"),
        ),
        # uq_catalog_positions_norm_hash_unit — UNIQUE по выражению, создаётся raw
        # SQL в миграции 0003:
        #   sha256(replace(normalized_job_title, E'\\', E'\\\\')::bytea), COALESCE(unit_id,-1)
        # Удвоение обратных слэшей обязательно: `text::bytea` разбирает вход как
        # escape-формат bytea (см. `services.matching.norm_hash`).
        # На него же опирается ON CONFLICT в get-or-create матчинга (§5.4.3).
        # Идентичность работы — полная пара (normalized_job_title, unit_id); хэш
        # нужен потому, что btree не индексирует значения длиннее 2704 байт, а в
        # наименование сметы попадают спецификации на несколько килобайт.
        # Совпадение подтверждается сравнением полного текста в matching.py.
    )


class MatchingCache(Base):
    """Кэш матчинга «нормализованная пара → каталожная строка» (§4).

    Инвариант (§5): записи кэша НИКОГДА не указывают на строки kind='TO_REVIEW' —
    это условие безопасности DELETE при слиянии в Review.
    """
    __tablename__ = "matching_cache"

    # sha256(norm_version || '|' || normalized_title || '|' || unit_norm)
    cache_key = Column(Text, primary_key=True)
    norm_version = Column(SmallInteger, nullable=False)
    job_title_text = Column(Text, nullable=False)  # исходники — для перевыпуска ключей
    unit_text = Column(Text, nullable=True)
    catalog_position_id = Column(
        BigInteger, ForeignKey("catalog_positions.id", ondelete="CASCADE"), nullable=False
    )
    source = Column(Text, nullable=False)
    # source='auto'   → now() + 30 дней, продлевается при hit;
    # source='manual' → NULL, не истекает.
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    catalog_position = relationship("CatalogPosition")

    __table_args__ = (
        CheckConstraint(f"source IN ({_sql_str_list(MatchSource)})", name="ck_matching_cache_source"),
        # Правило TTL (§4) целиком, обе ветки: у 'auto' срок ОБЯЗАН быть
        # (иначе автоматическая запись становится бессрочной и подменяет собой
        # ручное решение), у 'manual' его обязано не быть.
        CheckConstraint(
            f"(source = '{MatchSource.manual.value}' AND expires_at IS NULL)"
            f" OR (source = '{MatchSource.auto.value}' AND expires_at IS NOT NULL)",
            name="ck_matching_cache_ttl_by_source",
        ),
        Index("idx_matching_cache_catalog_id", "catalog_position_id"),
        Index("idx_matching_cache_expires_at", "expires_at"),
    )


class PositionItem(Base):
    """Строка сметы. Перенос из tenders-go.

    deviation_from_baseline_cost остаётся NULL: в сметах ГП baseline нет (§4).
    """
    __tablename__ = "position_items"

    id = Column(BigInteger, primary_key=True)
    proposal_id = Column(
        BigInteger, ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False
    )
    # NULL, пока строка не прошла матчинг. FK без CASCADE: каталожную строку
    # нельзя удалить, пока на неё ссылаются позиции (условие DELETE в Review, §5).
    catalog_position_id = Column(
        BigInteger, ForeignKey("catalog_positions.id"), nullable=True
    )

    position_key_in_proposal = Column(String(255), nullable=False)
    # Орфография ключа — как в JSON парсера (constants.JSON_KEY_COMMENT_ORGANIZER);
    # в tenders-go колонка называлась comment_organazier (опечатка), исправлено.
    comment_organizer = Column(Text, nullable=True)
    comment_contractor = Column(Text, nullable=True)
    item_number_in_proposal = Column(String(50), nullable=True)
    chapter_number_in_proposal = Column(String(50), nullable=True)
    job_title_in_proposal = Column(Text, nullable=False)
    unit_id = Column(Integer, ForeignKey("units_of_measure.id"), nullable=True)

    quantity = Column(Numeric, nullable=True)            # «Общее кол-во» — объём заказчика
    suggested_quantity = Column(Numeric, nullable=True)  # «Предлагаемое количество» (§4)
    total_cost_for_organizer_quantity = Column(Numeric, nullable=True)

    unit_cost_materials = Column(Numeric, nullable=True)
    unit_cost_works = Column(Numeric, nullable=True)
    unit_cost_indirect_costs = Column(Numeric, nullable=True)
    unit_cost_total = Column(Numeric, nullable=True)
    total_cost_materials = Column(Numeric, nullable=True)
    total_cost_works = Column(Numeric, nullable=True)
    total_cost_indirect_costs = Column(Numeric, nullable=True)
    total_cost_total = Column(Numeric, nullable=True)
    deviation_from_baseline_cost = Column(Numeric, nullable=True)

    is_chapter = Column(Boolean, nullable=False, server_default=sa_text("false"))
    chapter_ref_in_proposal = Column(String(50), nullable=True)

    # Фаза 7, миграция 0006: привязка к статье классификатора.
    # Поля статьи живут ТОЛЬКО на строках-разделах (ck_..._article_only_on_chapters);
    # у позиции статья выводится через chapter_item_id -> её строка-раздел.
    smr_article_raw = Column(Text, nullable=True)
    work_category_id = Column(
        BigInteger,
        ForeignKey("work_categories.id", ondelete="RESTRICT",
                   name="fk_position_items_work_category_id"),
        nullable=True,
    )
    category_source = Column(Text, nullable=True)
    # Одиночного FK на position_items.id НЕТ: цель задаёт составной FK в
    # __table_args__ — он проверяет и существование строки, и совпадение proposal.
    chapter_item_id = Column(BigInteger, nullable=True)

    created_at = _created_at()
    updated_at = _updated_at()

    proposal = relationship("Proposal", back_populates="position_items")
    catalog_position = relationship("CatalogPosition")
    unit = relationship("UnitOfMeasure")

    __table_args__ = (
        UniqueConstraint(
            "proposal_id", "position_key_in_proposal", name="uq_position_items_proposal_id_key"
        ),
        # Цель составного self-FK. ЗАМЕНЯЕТ idx_position_items_proposal_id:
        # proposal_id — левый префикс, поиск по нему по-прежнему идёт индексом.
        UniqueConstraint("proposal_id", "id", name="uq_position_items_proposal_id_id"),
        ForeignKeyConstraint(
            ["proposal_id", "chapter_item_id"],
            ["position_items.proposal_id", "position_items.id"],
            ondelete="RESTRICT",
            name="fk_position_items_chapter",
        ),
        CheckConstraint(
            "is_chapter OR (smr_article_raw IS NULL AND work_category_id IS NULL "
            "AND category_source IS NULL)",
            name="ck_position_items_article_only_on_chapters",
        ),
        CheckConstraint(
            "(work_category_id IS NULL) = (category_source IS NULL)",
            name="ck_position_items_category_source_pairs",
        ),
        CheckConstraint(
            "category_source IS NULL OR category_source = 'file'",
            name="ck_position_items_category_source",
        ),
        Index("idx_position_items_catalog_id", "catalog_position_id"),
        Index("idx_position_items_unit_id", "unit_id"),
        Index(
            "idx_position_items_work_category_id",
            "work_category_id",
            postgresql_where=sa_text("work_category_id IS NOT NULL"),
        ),
        Index(
            "idx_position_items_chapter_item_id",
            "chapter_item_id",
            postgresql_where=sa_text("chapter_item_id IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
#  Нормативы расценок
# ---------------------------------------------------------------------------

class RateStandard(Base):
    """Норматив ставки для (каталожная позиция × класс объекта) на период
    [valid_from, valid_to). Переутверждение = UPDATE valid_to старой строки +
    INSERT новой; историю не мутировать (§4)."""
    __tablename__ = "rate_standards"

    id = Column(BigInteger, primary_key=True)
    catalog_position_id = Column(
        BigInteger, ForeignKey("catalog_positions.id"), nullable=False
    )
    rate_class_id = Column(BigInteger, ForeignKey("rate_classes.id"), nullable=False)
    standard_unit_rate = Column(Numeric, nullable=False)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)   # NULL = бесконечность
    inflation_index = Column(Numeric, nullable=True)
    approved_by = Column(Text, nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(Text, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    catalog_position = relationship("CatalogPosition")
    rate_class = relationship("RateClass")

    __table_args__ = (
        CheckConstraint("standard_unit_rate > 0", name="ck_rate_standards_rate_positive"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="ck_rate_standards_period"
        ),
        CheckConstraint(
            "inflation_index IS NULL OR inflation_index > 0",
            name="ck_rate_standards_inflation_index",
        ),
        Index("ix_rate_standards_class", "rate_class_id"),
        # ex_rate_standards_no_overlap — EXCLUDE USING gist, запрет пересечения
        # периодов для одной пары (позиция, класс); создаётся raw SQL в 0002.
        # Он же даёт gist-индекс по (catalog_position_id, rate_class_id, период).
    )


# ---------------------------------------------------------------------------
#  Классификатор видов работ (фаза 7, спека Ф1)
# ---------------------------------------------------------------------------

WORK_CATEGORY_CODE_REGEX = "^[0-9]+([.][0-9]+)*$"
WORK_CATEGORY_IS_BUCKET_EXPRESSION = "code = '99' OR code LIKE '%.99'"


class WorkCategory(Base):
    """Статья классификатора видов работ компании (фаза 7, спека Ф1).

    Дерево держится на `parent_id`; `is_bucket` — производное от кода, писать в него
    нельзя (generated column). Уровень статьи не хранится: он выводится из дерева.
    """

    __tablename__ = "work_categories"

    id = Column(BigInteger, primary_key=True)
    code = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    parent_id = Column(
        BigInteger, ForeignKey("work_categories.id", ondelete="RESTRICT"), nullable=True
    )
    is_bucket = Column(
        Boolean, Computed(WORK_CATEGORY_IS_BUCKET_EXPRESSION, persisted=True), nullable=False
    )
    sort_order = Column(Integer, nullable=False)
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (
        UniqueConstraint("code", name="uq_work_categories_code"),
        UniqueConstraint("sort_order", name="uq_work_categories_sort_order"),
        CheckConstraint(f"code ~ '{WORK_CATEGORY_CODE_REGEX}'", name="ck_work_categories_code"),
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="ck_work_categories_not_self_parent"
        ),
        CheckConstraint(
            f"btrim(title, {TITLE_BLANK_CHARS_SQL}) <> ''",
            name="ck_work_categories_title_not_blank",
        ),
    )


# ---------------------------------------------------------------------------
#  Настройки приложения (фаза 6)
# ---------------------------------------------------------------------------

#: Границы «топ-N ключевых расценок» паспорта (AGENTS.md §7.4).
#:
#: Верхняя граница — требование DoD §10 «паспорт печатается на одну страницу А4»,
#: а не произвольное ограничение: ключевые расценки по §7.4 и есть топ-N, поэтому
#: страница полна, когда на ней топ-N, и держать это по построению правильнее, чем
#: обрезать список при печати. Обоснование числа — `docs/phase6-analytics.md` §1.5.
#:
#: Значения продублированы литералами в миграции 0004 (она обязана быть неизменной
#: во времени); расхождение ловит `test_schema_constraints.py::TestAppSettings`.
PASSPORT_TOP_N_DEFAULT = 15
PASSPORT_TOP_N_MIN = 1
PASSPORT_TOP_N_MAX = 20


class AppSettings(Base):
    """Настройки приложения — ОДНА строка с типизированными колонками (решение §6.2).

    Не «ключ→значение»: в такой таблице БД хранила бы `passport_top_n = 'абв'`, и
    ошибка всплыла бы при отрисовке паспорта. Здесь диапазон держит `CHECK`, то есть
    БД, — тот же принцип, что у `ck_rate_standards_rate_positive`.

    `id` — singleton через `CHECK (id = 1)`: вторая строка непредставима, поэтому
    читающий код не выбирает между строками.

    **Правка только через ORM.** `updated_at` обновляется `onupdate` на стороне
    SQLAlchemy, и raw-SQL `UPDATE` метку не тронет (соглашение
    `docs/phase2-schema.md`).
    """
    __tablename__ = "app_settings"

    id = Column(SmallInteger, primary_key=True, server_default=sa_text("1"))
    passport_top_n = Column(
        Integer, nullable=False, server_default=sa_text(str(PASSPORT_TOP_N_DEFAULT))
    )
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_app_settings_singleton"),
        CheckConstraint(
            f"passport_top_n BETWEEN {PASSPORT_TOP_N_MIN} AND {PASSPORT_TOP_N_MAX}",
            name="ck_app_settings_passport_top_n",
        ),
    )
