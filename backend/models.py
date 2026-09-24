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
    # Полезная площадь (миграция 0013): ЧАСТЬ общей, а не третье слагаемое —
    # выражение area_total_sp не меняется (спека 2026-08-15 §2.2). Парой с
    # надземной и подземной НЕ связана (§2.4), поэтому при NULL-паре сравнение
    # с суммой даёт NULL и пропускает — принятая граница.
    area_useful_sp = Column(Numeric, nullable=True)
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
        CheckConstraint(
            "area_useful_sp IS NULL OR area_useful_sp >= 0",
            name="ck_objects_area_useful_sp_non_negative",
        ),
        # Через СЛАГАЕМЫЕ, не через area_total_sp (спека §2.3): тот же результат
        # без зависимости от того, разрешает ли PostgreSQL ссылку на генерируемую
        # колонку в CHECK другой колонки.
        CheckConstraint(
            "area_useful_sp IS NULL "
            "OR area_useful_sp <= area_aboveground_sp + area_underground_sp",
            name="ck_objects_area_useful_sp_within_total",
        ),
    )


#: Дублирует миграцию 0015; расхождение ловит test_tenders_schema.py.
CONTRACTOR_INN_CANONICAL = "inn ~ '^[0-9]+$'"


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

    __table_args__ = (
        UniqueConstraint("inn", name="uq_contractors_inn"),
        # Канон ИНН — только ASCII-цифры (миграция 0015, спека контура §2.7):
        # `utils.canonicalize_inn` пишет ровно эту форму, CHECK стережёт правку
        # мимо приложения. Длина не проверяется — юрисдикции разные.
        CheckConstraint(CONTRACTOR_INN_CANONICAL, name="ck_contractors_inn_canonical"),
    )


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


# ---------------------------------------------------------------------------
#  Тендерный контур (спека 2026-08-26-tenders-contour-design.md §2.1)
# ---------------------------------------------------------------------------

#: Выражения CHECK продублированы в миграции 0015 намеренно (та же дисциплина,
#: что у 0004–0014); расхождение ловит test_tenders_schema.py.
TENDER_TITLE_NOT_BLANK = "btrim(title) <> ''"
TENDER_NUMBER_NOT_BLANK = "btrim(tender_number) <> ''"
TENDER_ROUND_STAGE_NO_POSITIVE = "stage_no > 0"
ESTIMATE_OWNER_EXACTLY_ONE = "num_nonnulls(contract_id, offer_id, round_id) = 1"
ESTIMATE_AMENDMENT_ONLY_WITH_CONTRACT = "contract_id IS NOT NULL OR amendment_no IS NULL"
PROPOSAL_BASELINE_CONTRACTOR = (
    "(is_baseline = true AND contractor_id IS NULL) "
    "OR (is_baseline = false AND contractor_id IS NOT NULL)"
)
IMPORT_JOB_OWNER_EXACTLY_ONE = "num_nonnulls(contract_id, round_id) = 1"
IMPORT_JOB_AMENDMENT_ONLY_WITH_CONTRACT = "contract_id IS NOT NULL OR amendment_no IS NULL"
IMPORT_JOB_PARSED_PAIR = "(parsed_data IS NULL) = (parser_version IS NULL)"
IMPORT_JOB_ESTIMATES_CREATED_POSITIVE = "estimates_created IS NULL OR estimates_created > 0"


class Tender(Base):
    """Тендер: объект, предмет торга, номер. `rate_class_id` — СНИМОК класса на
    момент торга, по тому же правилу, что `contracts.rate_class_id` (§4):
    переклассификация объекта не меняет прошлое."""
    __tablename__ = "tenders"

    id = Column(BigInteger, primary_key=True)
    object_id = Column(BigInteger, ForeignKey("objects.id"), nullable=False)
    title = Column(Text, nullable=False)
    tender_number = Column(Text, nullable=False)
    rate_class_id = Column(BigInteger, ForeignKey("rate_classes.id"), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    object = relationship("ObjectModel")
    rate_class = relationship("RateClass")
    rounds = relationship(
        "TenderRound", back_populates="tender", cascade="all, delete-orphan",
        order_by="TenderRound.stage_no",
    )
    packages = relationship("OfferPackage", back_populates="tender", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tender_number", name="uq_tenders_tender_number"),
        CheckConstraint(TENDER_TITLE_NOT_BLANK, name="ck_tenders_title_not_blank"),
        CheckConstraint(TENDER_NUMBER_NOT_BLANK, name="ck_tenders_number_not_blank"),
        Index("ix_tenders_object_id", "object_id"),
        Index("ix_tenders_rate_class_id", "rate_class_id"),
    )


class TenderRound(Base):
    """Раунд (этап) торга. `UNIQUE (id, tender_id)` — цель составного FK из
    `offers`: так раунд и участник ячейки обязаны принадлежать одному тендеру
    (спека §2.1)."""
    __tablename__ = "tender_rounds"

    id = Column(BigInteger, primary_key=True)
    tender_id = Column(BigInteger, ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    stage_no = Column(Integer, nullable=False)
    label = Column(Text, nullable=True)
    held_on = Column(Date, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    tender = relationship("Tender", back_populates="rounds")
    offers = relationship(
        "Offer", back_populates="round", cascade="all, delete-orphan", overlaps="offers"
    )

    __table_args__ = (
        UniqueConstraint("tender_id", "stage_no", name="uq_tender_rounds_tender_stage"),
        UniqueConstraint("id", "tender_id", name="uq_tender_rounds_id_tender"),
        CheckConstraint(TENDER_ROUND_STAGE_NO_POSITIVE, name="ck_tender_rounds_stage_no"),
    )


class OfferPackage(Base):
    """Участник тендера — «пакет» его предложений по всем раундам. Удаляется
    ТОЛЬКО командой (спека §2.11): `offers.package_id` объявлен RESTRICT, чтобы
    история участника не исчезала от случайного DELETE."""
    __tablename__ = "offer_packages"

    id = Column(BigInteger, primary_key=True)
    tender_id = Column(BigInteger, ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    contractor_id = Column(BigInteger, ForeignKey("contractors.id", ondelete="RESTRICT"), nullable=False)
    created_at = _created_at()

    tender = relationship("Tender", back_populates="packages")
    contractor = relationship("Contractor")
    offers = relationship("Offer", back_populates="package", overlaps="offers")

    __table_args__ = (
        UniqueConstraint("tender_id", "contractor_id", name="uq_offer_packages_tender_contractor"),
        UniqueConstraint("id", "tender_id", name="uq_offer_packages_id_tender"),
        Index("ix_offer_packages_contractor_id", "contractor_id"),
    )


class Offer(Base):
    """Ячейка решётки «раунд × участник». `tender_id` продублирован намеренно:
    два составных FK держат раунд и пакет в одном тендере структурно, а не
    триггером. Все три колонки NOT NULL — иначе MATCH SIMPLE отключил бы
    проверку (спека §2.1)."""
    __tablename__ = "offers"

    id = Column(BigInteger, primary_key=True)
    tender_id = Column(BigInteger, nullable=False)
    round_id = Column(BigInteger, nullable=False)
    package_id = Column(BigInteger, nullable=False)
    created_at = _created_at()

    round = relationship("TenderRound", back_populates="offers", foreign_keys=[round_id, tender_id],
                         overlaps="package,offers")
    package = relationship("OfferPackage", back_populates="offers", foreign_keys=[package_id, tender_id],
                           overlaps="round,offers")

    __table_args__ = (
        ForeignKeyConstraint(
            ["round_id", "tender_id"], ["tender_rounds.id", "tender_rounds.tender_id"],
            ondelete="CASCADE", name="fk_offers_round",
        ),
        ForeignKeyConstraint(
            ["package_id", "tender_id"], ["offer_packages.id", "offer_packages.tender_id"],
            ondelete="RESTRICT", name="fk_offers_package",
        ),
        UniqueConstraint("round_id", "package_id", name="uq_offers_round_package"),
        Index("ix_offers_package_id", "package_id"),
    )


class ImportJob(Base):
    """Задание импорта сметы (§4, §5).

    Записи не удаляются ретенцией и заменой сметы — это аудит; удаление самого
    договора уносит их вместе с ним (спека 2026-08-16, ревизия §5).
    """
    __tablename__ = "import_jobs"

    id = Column(BigInteger, primary_key=True)
    # Два владельца — договор либо раунд, ровно один (спека контура §2.1).
    contract_id = Column(BigInteger, ForeignKey("contracts.id"), nullable=True)
    round_id = Column(BigInteger, ForeignKey("tender_rounds.id", ondelete="CASCADE"), nullable=True)
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

    # Три факта об одном файле (спека контура §2.3): XLSX в storage,
    # ТОЧНЫЙ ParseResult.data этого разбора — здесь, проекция под смету — в
    # estimate_raw_data. Пишется сессией A сразу после парсинга независимо от
    # исхода импорта; у jobs до 0015 законно NULL.
    parsed_data = Column(JSONB, nullable=True)
    parser_version = Column(Text, nullable=True)
    # Сколько смет создал успешный job: 1 у договора, N(+1) у раунда. Нужен
    # правилу «текущий job раунда» (спека §2.12); у старых jobs NULL.
    estimates_created = Column(Integer, nullable=True)

    created_at = _created_at()
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    contract = relationship("Contract")
    round = relationship("TenderRound")

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
        CheckConstraint(IMPORT_JOB_OWNER_EXACTLY_ONE, name="ck_import_jobs_owner"),
        CheckConstraint(IMPORT_JOB_AMENDMENT_ONLY_WITH_CONTRACT, name="ck_import_jobs_amendment_owner"),
        CheckConstraint(IMPORT_JOB_PARSED_PAIR, name="ck_import_jobs_parsed_pair"),
        CheckConstraint(IMPORT_JOB_ESTIMATES_CREATED_POSITIVE, name="ck_import_jobs_estimates_created"),
        Index("ix_import_jobs_contract_id", "contract_id", "amendment_no"),
        Index("ix_import_jobs_round_id", "round_id"),
        # Очередь startup-recovery (§5): все незавершённые задания.
        Index(
            "ix_import_jobs_active",
            "id",
            postgresql_where=sa_text(f"status NOT IN ({_sql_str_list(TERMINAL_IMPORT_JOB_STATUSES)})"),
        ),
        # uq_import_jobs_active_pair — частичный уникальный индекс по
        # (contract_id, COALESCE(amendment_no,-1)); выражение Alembic не
        # выражает декларативно, создаётся raw SQL в миграции 0002.
        # с 0015 — частичный, WHERE contract_id IS NOT NULL.
        # uq_import_jobs_active_round — UNIQUE (round_id) WHERE round_id IS NOT NULL
        # AND status NOT IN (terminal); raw SQL в миграции 0015, как active_pair.
    )


class Estimate(Base):
    """Смета к договору (бывш. tenders). 1 договор : N смет, различаются
    номером допсоглашения; NULL = исходная смета (§4)."""
    __tablename__ = "estimates"

    id = Column(BigInteger, primary_key=True)
    # Три владельца — договор, предложение раунда, раунд (baseline); ровно один
    # (спека контура §2.1). round_id на смете и ЕСТЬ признак baseline.
    contract_id = Column(BigInteger, ForeignKey("contracts.id"), nullable=True)
    offer_id = Column(BigInteger, ForeignKey("offers.id", ondelete="CASCADE"), nullable=True)
    round_id = Column(BigInteger, ForeignKey("tender_rounds.id", ondelete="CASCADE"), nullable=True)
    amendment_no = Column(Integer, nullable=True)
    title = Column(String, nullable=True)
    data_prepared_on_date = Column(Date, nullable=True)
    import_job_id = Column(
        BigInteger, ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True
    )
    # Ручные ставки НДС (спека пересчёта §2.1). Решение человека о СМЕТЕ, а не
    # факт файла: `proposals.vat_rate` остаётся неприкосновенным. База
    # перекрываема всегда, в том числе поверх заявленной файлом, — файл умеет
    # ошибиться, и это обязано лечиться приложением.
    vat_rate_base_override = Column(Numeric, nullable=True)
    vat_rate_target = Column(Numeric, nullable=True)
    # `users.id` — integer, не bigint; тип повторяет его (то же, что в 0011).
    vat_rate_updated_by_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    vat_rate_updated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    contract = relationship("Contract")
    offer = relationship("Offer")
    round = relationship("TenderRound")
    import_job = relationship("ImportJob")
    raw_data = relationship(
        "EstimateRawData", back_populates="estimate", uselist=False, cascade="all, delete-orphan"
    )
    lots = relationship("Lot", back_populates="estimate", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "amendment_no IS NULL OR amendment_no > 0", name="ck_estimates_amendment_no"
        ),
        CheckConstraint(
            "vat_rate_base_override IS NULL "
            "OR (vat_rate_base_override >= 0 AND vat_rate_base_override <= 100)",
            name="ck_estimates_vat_rate_base_override",
        ),
        CheckConstraint(
            "vat_rate_target IS NULL OR (vat_rate_target >= 0 AND vat_rate_target <= 100)",
            name="ck_estimates_vat_rate_target",
        ),
        CheckConstraint(ESTIMATE_OWNER_EXACTLY_ONE, name="ck_estimates_owner"),
        CheckConstraint(ESTIMATE_AMENDMENT_ONLY_WITH_CONTRACT, name="ck_estimates_amendment_owner"),
        # Отдельного индекса по contract_id нет намеренно: выборки по договору
        # обслуживает uq_estimates_contract_amendment — полный уникальный индекс
        # с ведущей колонкой contract_id (создаётся raw SQL в миграции 0002).
        Index("ix_estimates_import_job_id", "import_job_id"),
        # uq_estimates_contract_amendment — UNIQUE NULLS NOT DISTINCT
        # (contract_id, amendment_no), синтаксис PG16; создаётся raw SQL
        # в миграции 0002. Обычный UNIQUE не годится: NULL-ы в нём различны,
        # и исходную смету можно было бы загрузить дважды.
        # uq_estimates_offer / uq_estimates_round — частичные UNIQUE, raw SQL 0015;
        # uq_estimates_contract_amendment с 0015 — WHERE contract_id IS NOT NULL.
    )


class EstimateRawData(Base):
    """Проекция разобранного JSON под ЭТУ смету — вход материализации и
    резолвера статей (спека контура §2.3). Полный результат разбора файла —
    `import_jobs.parsed_data`."""
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
    инвариант закреплён уникальным индексом по lot_id. У baseline подрядчика
    нет (спека контура §1.2)."""
    __tablename__ = "proposals"

    id = Column(BigInteger, primary_key=True)
    lot_id = Column(BigInteger, ForeignKey("lots.id", ondelete="CASCADE"), nullable=False)
    contractor_id = Column(BigInteger, ForeignKey("contractors.id"), nullable=True)
    # В сметах ГП baseline-колонки нет — поле унаследовано 1:1 переносом JSON
    # парсера. С тендерным контуром (0015) поле стало опорным: у baseline-
    # proposal оно true и contractor_id обязан быть NULL, у offer-proposal —
    # наоборот (CHECK `ck_proposals_baseline_contractor`, спека §2.1); тот же
    # флаг ветвит импорт раунда на baseline- и offer-путь (§2.5).
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
        CheckConstraint(PROPOSAL_BASELINE_CONTRACTOR, name="ck_proposals_baseline_contractor"),
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

    Висит на `proposal_id`, а не на `estimate_id` (спека
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
            "category_source IS NULL OR category_source IN ('file','manual')",
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


class EstimateCategoryOverride(Base):
    """Ручное решение о статье строки-раздела (миграция 0011).

    Первичный ключ — сама строка-раздел: «одно решение на раздел» держит схема.
    `RESTRICT` на статью и на автора: у решения, попадающего в паспорт для банка,
    и статья, и автор должны оставаться живыми.
    """

    __tablename__ = "estimate_category_overrides"

    position_item_id = Column(
        BigInteger,
        ForeignKey(
            "position_items.id",
            ondelete="CASCADE",
            name="fk_estimate_category_overrides_position_item_id",
        ),
        primary_key=True,
    )
    work_category_id = Column(
        BigInteger,
        ForeignKey(
            "work_categories.id",
            ondelete="RESTRICT",
            name="fk_estimate_category_overrides_work_category_id",
        ),
        nullable=False,
    )
    assigned_by = Column(
        Integer,
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            name="fk_estimate_category_overrides_assigned_by",
        ),
        nullable=False,
    )
    assigned_at = _created_at()
    note = Column(Text, nullable=True)

    __table_args__ = (
        Index(
            "idx_estimate_category_overrides_work_category_id",
            "work_category_id",
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
#  Семантический контур (спека 2026-09-22-catalog-families-design.md §2.3)
#
#  Шесть таблиц: work_families (типовые семьи работ), context_buckets
#  (идемпотентная точка входа «строка × эффективная статья»), catalog_contexts
#  (устойчивая группа с принятым семантическим решением), context_routing_rules
#  (упорядоченные правила корзины), context_members (явное членство «позиция →
#  контекст», ровно одно) и semantic_events (журнал с ровно одним предметом).
#  Форма таблиц, каждый CHECK, составные FK и частичные индексы — из спеки
#  §2.3, здесь не изобретаются и не «улучшаются».
# ---------------------------------------------------------------------------

class FamilyStatus(str, enum.Enum):
    """Жизненный цикл семьи работ (спека §2.3)."""
    draft = "draft"
    active = "active"
    archived = "archived"


class SemanticKind(str, enum.Enum):
    """Семантический тип контекста: работа, система(вспомогательная) или пока
    не решено (спека §2.3)."""
    WORK = "WORK"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


class NameRole(str, enum.Enum):
    """Роль названия внутри контекста по словарю мест (спека §2.3)."""
    WORK = "WORK"
    LOCATION_ONLY = "LOCATION_ONLY"
    GENERIC_WORK = "GENERIC_WORK"


class SemanticState(str, enum.Enum):
    """Состояние семантического решения контекста — ровно три значения
    (Global Constraints плана фичи «Семьи и контексты»)."""
    SUGGESTED = "SUGGESTED"
    CONFIRMED = "CONFIRMED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class MembershipState(str, enum.Enum):
    """Членство позиции в контексте: действующее или устаревшее после разноса
    статьи (спека §2.3)."""
    CURRENT = "CURRENT"
    STALE = "STALE"


class DecisionSource(str, enum.Enum):
    """Происхождение решения по правилу или вручную — общий тип для
    `semantic_kind_source` и `name_role_source` (спека §2.3)."""
    rule = "rule"
    manual = "manual"


class FamilySource(str, enum.Enum):
    """Происхождение назначения семьи контексту (спека §2.3)."""
    manual = "manual"
    suggestion = "suggestion"


class RoutedBy(str, enum.Enum):
    """Как позиция попала в свой текущий контекст (спека §2.3)."""
    default = "default"
    rule = "rule"
    manual = "manual"


class ComparabilityReason(str, enum.Enum):
    """Причина, по которой контекст пока не сравним — единственное значение
    спеки §2.3."""
    insufficient_description = "insufficient_description"


#: Закрытый список событий журнала (спека §2.14). НЕ Python str-Enum
#: намеренно: план задачи заводит для журнала только константу для CHECK,
#: не отдельный класс (Task 1 «Имена»).
SEMANTIC_EVENT_TYPES = (
    "context_created",
    "context_split",
    "context_merged",
    "members_moved",
    "members_marked_stale",
    "kind_set",
    "name_role_set",
    "context_family_assigned",
    "context_archived",
    "routing_rules_dropped",
    "family_created",
    "family_updated",
    "family_activated",
    "family_archived",
    "family_merged",
)

#: Десять списков значений `IN (...)` — тоже продублированы в миграции 0017
#: литералом (та же дисциплина, что у CK_*: миграция не импортирует models.py,
#: см. докстринг модуля миграции), и та же parity-проверка их сравнивает
#: (список `IN (...)` — тоже CHECK, и расхождение в
#: нём миграция/models.py прежде ничем не ловилось).
FAMILY_STATUSES = _sql_str_list(FamilyStatus)
SEMANTIC_KINDS = _sql_str_list(SemanticKind)
NAME_ROLES = _sql_str_list(NameRole)
SEMANTIC_STATES = _sql_str_list(SemanticState)
MEMBERSHIP_STATES = _sql_str_list(MembershipState)
DECISION_SOURCES = _sql_str_list(DecisionSource)
FAMILY_SOURCES = _sql_str_list(FamilySource)
ROUTED_BY_VALUES = _sql_str_list(RoutedBy)
COMPARABILITY_REASONS = _sql_str_list(ComparabilityReason)
SEMANTIC_EVENT_TYPES_SQL = _sql_str_list(SEMANTIC_EVENT_TYPES)

#: Выражения CHECK продублированы в миграции 0017 намеренно (та же дисциплина,
#: что у 0002, 0003, 0015); расхождение ловит
#: test_semantic_schema.py::TestParityWithMigration.
CK_FAMILY_ACTIVE_NEEDS_DEFINITION = (
    "status <> 'active' OR (definition IS NOT NULL AND btrim(definition) <> '')"
)
CK_FAMILY_ACTIVATION_PAIR = "(activated_at IS NULL) = (activated_by IS NULL)"
CK_FAMILY_AUTHOR_IFF_NOT_SEED = "(created_by IS NULL) = (seed_key IS NOT NULL)"
#: ОДИН тотальный предикат из двух полных ветвей — НЕ пара равносильностей.
#: Пара `num_nonnulls(work_family_id, family_source, family_at) IN (0, 3)` плюс
#: `(family_source = 'manual') = (family_by IS NOT NULL)` пропускала одинокий
#: `family_by` при трёх пустых полях: `NULL = 'manual'` даёт `NULL`, а `CHECK`
#: отвергает только `FALSE` (`docs/pitfalls/db.md`).
CK_CONTEXT_FAMILY_PROVENANCE = (
    "(work_family_id IS NULL AND family_source IS NULL AND family_at IS NULL AND family_by IS NULL) "
    "OR (work_family_id IS NOT NULL AND family_source IS NOT NULL AND family_at IS NOT NULL "
    "AND (family_by IS NOT NULL) = (family_source = 'manual'))"
)
CK_CONTEXT_KIND_SOURCE_PAIR = "(semantic_kind_source = 'manual') = (semantic_kind_by IS NOT NULL)"
CK_CONTEXT_NAME_ROLE_SOURCE_PAIR = "(name_role_source = 'manual') = (name_role_by IS NOT NULL)"
CK_MEMBER_RULE_PAIR = "(routed_by = 'rule') = (routing_rule_id IS NOT NULL)"
CK_MEMBER_CONFLICT_PAIR = "(conflict_at IS NULL) = (conflict_from_context_id IS NULL)"
CK_EVENT_ONE_SUBJECT = "num_nonnulls(context_id, family_id) = 1"
#: Предмет журнала — ЯВНОЕ множество типов, а не префикс `family_%` (спека
#: §2.14). На вставках в таблицу это неразличимо: переименованное событие
#: `context_family_assigned` не начинается с `family_`, и оба предиката
#: согласны на нём. Свидетель против префикса — историческое имя
#: `family_assigned`, вычисленное СТАНДАЛОНОМ (вне таблицы и вне закрытого
#: списка `event_type`) в `test_semantic_schema.py::TestEventSubjectByTypeExpression`.
CK_EVENT_SUBJECT_BY_TYPE = (
    "(event_type IN ('family_created', 'family_updated', 'family_activated', "
    "'family_archived', 'family_merged')) = (family_id IS NOT NULL)"
)
CK_EVENT_PAYLOAD_NOT_EMPTY = "jsonb_typeof(payload) = 'object' AND payload <> '{}'::jsonb"


class WorkFamily(Base):
    """Семья работ: тип, объединяющий сравнимые варианты написания (спека §2.3).

    `seed_key` — стабильный ключ строки seed; у ручных семей всегда `NULL`, и
    автор (`created_by`) обязан быть НЕ `NULL` ровно тогда, когда семья не
    заведена seed-командой (`CK_FAMILY_AUTHOR_IFF_NOT_SEED`). Активация
    (`status='active'`) требует непустого `definition`
    (`CK_FAMILY_ACTIVE_NEEDS_DEFINITION`); имя семьи ключом не является —
    уникальность (`lower(btrim(title)), COALESCE(unit_id,-1)`) держится только
    среди `active` строк, raw SQL индексом миграции 0017.
    """
    __tablename__ = "work_families"

    id = Column(BigInteger, primary_key=True)
    seed_key = Column(Text, nullable=True)
    title = Column(Text, nullable=False)
    unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="RESTRICT"), nullable=True
    )
    definition = Column(Text, nullable=True)
    status = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()
    activated_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)

    unit = relationship("UnitOfMeasure")

    __table_args__ = (
        UniqueConstraint("seed_key", name="uq_work_families_seed_key"),
        CheckConstraint("btrim(title) <> ''", name="ck_work_families_title_not_blank"),
        CheckConstraint(f"status IN ({FAMILY_STATUSES})", name="ck_work_families_status"),
        CheckConstraint(
            CK_FAMILY_ACTIVE_NEEDS_DEFINITION, name="ck_work_families_active_needs_definition"
        ),
        CheckConstraint(CK_FAMILY_ACTIVATION_PAIR, name="ck_work_families_activation_pair"),
        CheckConstraint(CK_FAMILY_AUTHOR_IFF_NOT_SEED, name="ck_work_families_author_iff_not_seed"),
        # UNIQUE (lower(btrim(title)), COALESCE(unit_id,-1)) WHERE status = 'active' —
        # частичный уникальный индекс по выражению, raw SQL в миграции 0017
        # (alembic/env.py RAW_SQL_INDEXES: uq_work_families_active_name_unit).
    )


class ContextBucket(Base):
    """Идемпотентная точка входа «каталожная строка × эффективная статья»
    (спека §2.2, §2.3). Решений не несёт — это только ключ группировки."""
    __tablename__ = "context_buckets"

    id = Column(BigInteger, primary_key=True)
    catalog_position_id = Column(
        BigInteger, ForeignKey("catalog_positions.id", ondelete="RESTRICT"), nullable=False
    )
    work_category_id = Column(
        BigInteger, ForeignKey("work_categories.id", ondelete="RESTRICT"), nullable=True
    )
    created_at = _created_at()
    updated_at = _updated_at()

    catalog_position = relationship("CatalogPosition")

    __table_args__ = (
        # UNIQUE (catalog_position_id, COALESCE(work_category_id,-1)) — выражение
        # с COALESCE, raw SQL в миграции 0017 (alembic/env.py RAW_SQL_INDEXES:
        # uq_context_buckets_position_category). «Отсутствие статьи — своя
        # корзина, одна на строку» (спека §2.2): COALESCE делает NULL обычным
        # сравнимым значением, поэтому вторая безстатейная корзина той же
        # строки отвергается как дубль, а не молча считается отдельной (без
        # COALESCE PostgreSQL не увидел бы совпадения NULL с NULL).
    )


class CatalogContext(Base):
    """Устойчивая группа с принятым семантическим решением (спека §2.3).

    Происхождение семьи (`work_family_id` + `family_source`/`family_at`/
    `family_by`) держит ОДИН тотальный предикат из двух полных ветвей
    (`CK_CONTEXT_FAMILY_PROVENANCE`), а не пара равносильностей — см.
    `docs/pitfalls/db.md` (трёхзначная логика `CHECK`).
    """
    __tablename__ = "catalog_contexts"

    id = Column(BigInteger, primary_key=True)
    bucket_id = Column(
        BigInteger, ForeignKey("context_buckets.id", ondelete="RESTRICT"), nullable=False
    )
    is_default = Column(Boolean, nullable=False, server_default=sa_text("false"))
    work_family_id = Column(
        BigInteger, ForeignKey("work_families.id", ondelete="RESTRICT"), nullable=True
    )
    family_source = Column(Text, nullable=True)
    family_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    family_at = Column(DateTime(timezone=True), nullable=True)
    semantic_kind = Column(Text, nullable=False)
    semantic_kind_source = Column(Text, nullable=False)
    semantic_kind_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    semantic_kind_at = Column(DateTime(timezone=True), nullable=False)
    name_role = Column(Text, nullable=False)
    name_role_source = Column(Text, nullable=False)
    name_role_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    name_role_at = Column(DateTime(timezone=True), nullable=False)
    place_dictionary_version = Column(SmallInteger, nullable=False)
    semantic_state = Column(Text, nullable=False)
    comparability_reason = Column(Text, nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    bucket = relationship("ContextBucket")
    work_family = relationship("WorkFamily")

    __table_args__ = (
        # Цель составных FK context_routing_rules и context_members ниже —
        # проверяют разом существование контекста и его принадлежность корзине.
        UniqueConstraint("bucket_id", "id", name="uq_catalog_contexts_bucket_id"),
        CheckConstraint(
            f"semantic_kind IN ({SEMANTIC_KINDS})", name="ck_catalog_contexts_semantic_kind"
        ),
        CheckConstraint(
            f"semantic_kind_source IN ({DECISION_SOURCES})",
            name="ck_catalog_contexts_semantic_kind_source",
        ),
        CheckConstraint(f"name_role IN ({NAME_ROLES})", name="ck_catalog_contexts_name_role"),
        CheckConstraint(
            f"name_role_source IN ({DECISION_SOURCES})",
            name="ck_catalog_contexts_name_role_source",
        ),
        CheckConstraint(
            f"semantic_state IN ({SEMANTIC_STATES})", name="ck_catalog_contexts_semantic_state"
        ),
        CheckConstraint(
            f"family_source IS NULL OR family_source IN ({FAMILY_SOURCES})",
            name="ck_catalog_contexts_family_source",
        ),
        CheckConstraint(
            f"comparability_reason IS NULL OR comparability_reason IN "
            f"({COMPARABILITY_REASONS})",
            name="ck_catalog_contexts_comparability_reason",
        ),
        CheckConstraint(CK_CONTEXT_KIND_SOURCE_PAIR, name="ck_catalog_contexts_kind_source_pair"),
        CheckConstraint(
            CK_CONTEXT_NAME_ROLE_SOURCE_PAIR, name="ck_catalog_contexts_name_role_source_pair"
        ),
        CheckConstraint(CK_CONTEXT_FAMILY_PROVENANCE, name="ck_catalog_contexts_family_provenance"),
        # UNIQUE (bucket_id) WHERE is_default AND archived_at IS NULL — частичный
        # уникальный индекс, raw SQL в миграции 0017 (alembic/env.py
        # RAW_SQL_INDEXES: uq_catalog_contexts_default_per_bucket). Половина
        # `archived_at IS NULL` — намеренно: архивный контекст по умолчанию
        # обязан сосуществовать с действующим (§2.3 плана).
    )


class ContextRoutingRule(Base):
    """Упорядоченное правило маршрутизации корзины (спека §2.3, §2.4)."""
    __tablename__ = "context_routing_rules"

    id = Column(BigInteger, primary_key=True)
    bucket_id = Column(
        BigInteger, ForeignKey("context_buckets.id", ondelete="CASCADE"), nullable=False
    )
    ordinal = Column(Integer, nullable=False)
    predicate = Column(JSONB, nullable=False)
    # Составной FK ниже проверяет и существование, и принадлежность корзине —
    # одиночного ForeignKey на context_id намеренно нет.
    context_id = Column(BigInteger, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = _created_at()

    bucket = relationship("ContextBucket")

    __table_args__ = (
        ForeignKeyConstraint(
            ["bucket_id", "context_id"],
            ["catalog_contexts.bucket_id", "catalog_contexts.id"],
            name="fk_context_routing_rules_bucket_context",
        ),
        UniqueConstraint("bucket_id", "ordinal", name="uq_context_routing_rules_bucket_ordinal"),
    )


class ContextMember(Base):
    """Явное членство позиции в контексте — ровно одно на позицию (спека §2.3).

    `position_item_id` — первичный ключ: структурная гарантия «ровно один
    контекст на позицию», а не инвариант, который пришлось бы стеречь тестом.
    """
    __tablename__ = "context_members"

    position_item_id = Column(
        BigInteger, ForeignKey("position_items.id", ondelete="CASCADE"), primary_key=True
    )
    # Составной FK ниже проверяет и существование контекста, и принадлежность
    # ИМЕННО этой корзине (bucket_id — дубль под составной FK).
    context_id = Column(BigInteger, nullable=False)
    bucket_id = Column(BigInteger, nullable=False)
    membership_state = Column(Text, nullable=False)
    routed_by = Column(Text, nullable=False)
    routing_rule_id = Column(
        BigInteger, ForeignKey("context_routing_rules.id", ondelete="SET NULL"), nullable=True
    )
    # Конфликт решений при слиянии в Review — СВОЯ ось, не значение membership_state:
    conflict_at = Column(DateTime(timezone=True), nullable=True)
    conflict_from_context_id = Column(
        BigInteger, ForeignKey("catalog_contexts.id", ondelete="RESTRICT"), nullable=True
    )
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["bucket_id", "context_id"],
            ["catalog_contexts.bucket_id", "catalog_contexts.id"],
            ondelete="RESTRICT",
            name="fk_context_members_bucket_context",
        ),
        CheckConstraint(
            f"membership_state IN ({MEMBERSHIP_STATES})",
            name="ck_context_members_membership_state",
        ),
        CheckConstraint(f"routed_by IN ({ROUTED_BY_VALUES})", name="ck_context_members_routed_by"),
        CheckConstraint(CK_MEMBER_RULE_PAIR, name="ck_context_members_rule_pair"),
        CheckConstraint(CK_MEMBER_CONFLICT_PAIR, name="ck_context_members_conflict_pair"),
        Index("idx_context_members_context_id", "context_id"),
        Index(
            "idx_context_members_context_id_stale",
            "context_id",
            postgresql_where=sa_text("membership_state = 'STALE'"),
        ),
        Index(
            "idx_context_members_context_id_conflict",
            "context_id",
            postgresql_where=sa_text("conflict_at IS NOT NULL"),
        ),
    )


class SemanticEvent(Base):
    """Журнал семантического контура: у события ровно один предмет (спека
    §2.3, §2.14).

    Предмет определяется ЗАКРЫТЫМ множеством типов (`CK_EVENT_SUBJECT_BY_TYPE`),
    а не префиксом имени `family_%`: пример — `family_assigned` (переименовано
    в `context_family_assigned`, предмет — контекст).
    """
    __tablename__ = "semantic_events"

    id = Column(BigInteger, primary_key=True)
    context_id = Column(
        BigInteger, ForeignKey("catalog_contexts.id", ondelete="RESTRICT"), nullable=True
    )
    family_id = Column(
        BigInteger, ForeignKey("work_families.id", ondelete="RESTRICT"), nullable=True
    )
    event_type = Column(Text, nullable=False)
    payload = Column(JSONB, nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = _created_at()

    __table_args__ = (
        CheckConstraint(
            f"event_type IN ({SEMANTIC_EVENT_TYPES_SQL})",
            name="ck_semantic_events_event_type",
        ),
        CheckConstraint(CK_EVENT_ONE_SUBJECT, name="ck_semantic_events_one_subject"),
        CheckConstraint(CK_EVENT_SUBJECT_BY_TYPE, name="ck_semantic_events_subject_by_type"),
        CheckConstraint(CK_EVENT_PAYLOAD_NOT_EMPTY, name="ck_semantic_events_payload_not_empty"),
    )


# ---------------------------------------------------------------------------
#  Настройки приложения (фаза 6)
# ---------------------------------------------------------------------------

#: Границы «топ-N ключевых расценок» ПАСПОРТА ФАЗЫ 6 (AGENTS.md §7.4).
#:
#: Верхняя граница подобрана под раскладку экрана паспорта фазы 6
#: (`GET /api/v1/analytics/passport/{contract_id}`), а не взята произвольно: топ-N
#: ключевых расценок по §7.4 занимает страницу целиком при бОльших N, и держать
#: границу по построению правильнее, чем обрезать список при печати. Паспорт
#: проекта Ф6 фазы 7 (`analytics/project-passport`) от этой настройки не зависит —
#: спека §2.6. Обоснование числа — `docs/phase6-analytics.md` §1.5.
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


class InflationSeries(Base):
    """Именованный ряд годовых индексов инфляции (спека инфляции §2.6, §2.11).

    Название несёт КОНКРЕТНЫЙ показатель — «Росстат, ИПЦ, декабрь к декабрю», а
    не «официальный»: у названного ряда подпись на поверхности выходит из данных,
    тогда как булево поле потребовало бы зашить две подписи в код. Побочно
    снимается вопрос об агентстве и стране — второй официальный ряд по другой
    стране есть ещё одна строка справочника, а не правка кода.

    `note` — примечание ряда; именно оно показывается на полосе уровней `/compare`
    (§2.12), тогда как источники ПО ГОДАМ на экране не показываются и печатаются
    только на листе выгрузки.

    Удаления нет (§2.10): ненужный ряд архивируется `is_active = false` и остаётся
    читаемым по старой ссылке, но не предлагается для нового выбора. Версионности
    у ряда тоже нет — компромисс назван в спеке и вынесен на поверхность датой
    `updated_at`.

    Выражения `CHECK` дублируют миграцию 0014 намеренно; parity-тесты
    `test_schema_constraints.py` ловят расхождение — `alembic check` его не видит.
    """
    __tablename__ = "inflation_series"

    id = Column(BigInteger, primary_key=True)
    name = Column(Text, nullable=False)
    note = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default=sa_text("true"))
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (
        UniqueConstraint("name", name="uq_inflation_series_name"),
        CheckConstraint("btrim(name) <> ''", name="ck_inflation_series_name_not_blank"),
    )


class InflationIndexValue(Base):
    """Значение ряда за один год: `k(y)` — декабрь года `y` к декабрю `y−1`.

    Цепной коэффициент, а не уровень цен (§2.3): число сверяется с публикацией
    напрямую, поэтому опечатку видно глазами, и правка одного года не трогает
    соседние. Обратная сторона названа вслух — правка одного звена сдвигает все
    последующие годы, и это верное поведение.

    `source` обязателен и непуст: вопрос «откуда 8,3 %» (§1 п. 4 `AGENTS.md`)
    задают к артефакту защиты перед банком, то есть к листу выгрузки, где источник
    и печатается.

    `is_forecast` — год, ещё не завершившийся: коэффициент за 2026-й станет фактом
    только в конце года, а цель по умолчанию именно в нём (§2.7). Поверхность
    называет год прогнозным; автоматической экстраполяции нет — число, выдуманное
    кодом, некому подписать.

    Непрерывность ряда схемой НЕ проверяется (§2.9): покрытие считается при чтении
    объединением требуемых годов выборки, и это точнее любого запрета пропусков.
    """
    __tablename__ = "inflation_index_values"

    id = Column(BigInteger, primary_key=True)
    # `ondelete` не задан: `DELETE` не предусмотрен ни для рядов, ни для значений
    # (§2.10), и каскад описывал бы удаление, которого в контракте нет.
    series_id = Column(BigInteger, ForeignKey("inflation_series.id"), nullable=False)
    year = Column(Integer, nullable=False)
    coefficient = Column(Numeric, nullable=False)
    is_forecast = Column(Boolean, nullable=False, server_default=sa_text("false"))
    source = Column(Text, nullable=False)
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (
        UniqueConstraint("series_id", "year", name="uq_inflation_index_values_series_year"),
        CheckConstraint("coefficient > 0", name="ck_inflation_index_values_coefficient_positive"),
        CheckConstraint("btrim(source) <> ''", name="ck_inflation_index_values_source_not_blank"),
    )
