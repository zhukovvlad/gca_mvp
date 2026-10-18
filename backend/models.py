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
    PrimaryKeyConstraint,
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
from sqlalchemy.dialects.postgresql import UUID as PgUUID
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

#: Отметка победителя тендера (миграция 0020, спека Б2 §2.2). Выражения
#: продублированы в миграции своими литералами; расхождение ловит
#: test_tender_award_schema.py. Обе ветви `TENDER_AWARD_NOT_CONCLUDED` тотальны:
#: сравнивают только `IS [NOT] NULL`, `NULL` в предикат не попадает.
TENDER_AWARD_NOT_CONCLUDED = (
    "(not_concluded_on IS NULL AND not_concluded_by IS NULL "
    "AND not_concluded_at IS NULL AND not_concluded_note IS NULL) "
    "OR (not_concluded_on IS NOT NULL AND not_concluded_by IS NOT NULL "
    "AND not_concluded_at IS NOT NULL)"
)
TENDER_AWARD_NOTE_NOT_BLANK = "not_concluded_note IS NULL OR btrim(not_concluded_note) <> ''"
TENDER_AWARD_KP_INN_CANONICAL = "kp_inn ~ '^[0-9]+$'"
#: Копия КП — только у исходной сметы договора: без договора составной ключ
#: `MATCH SIMPLE` не проверяется, поэтому CHECK требует `contract_id IS NOT NULL`.
ESTIMATE_SOURCE_AWARD_ORIGINAL = (
    "source_award_id IS NULL OR (contract_id IS NOT NULL AND amendment_no IS NULL)"
)
IMPORT_JOB_SOURCE_AWARD_ORIGINAL = (
    "source_award_id IS NULL OR (contract_id IS NOT NULL AND amendment_no IS NULL)"
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
    # Основание «по тендеру» (спека Б2 §2.2): отметка победителя, из которой
    # договор создан или к которой привязан. Одиночного FK нет — составной ключ
    # (id, object_id, contractor_id) держит объект и подрядчика договора равными
    # отметке; при NULL он не проверяется (MATCH SIMPLE), и это задумано.
    tender_award_id = Column(BigInteger, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    object = relationship("ObjectModel")
    contractor = relationship("Contractor")
    rate_class = relationship("RateClass")

    __table_args__ = (
        UniqueConstraint("contract_number", name="uq_contracts_contract_number"),
        UniqueConstraint("tender_award_id", name="uq_contracts_tender_award"),
        UniqueConstraint("id", "tender_award_id", name="uq_contracts_id_tender_award"),
        # use_alter: два цикла метаданных — contracts → tender_awards → estimates →
        # contracts и estimates → import_jobs → tender_awards → estimates; второй
        # разорван use_alter у fk_import_jobs_source_award.
        ForeignKeyConstraint(
            ["tender_award_id", "object_id", "contractor_id"],
            ["tender_awards.id", "tender_awards.object_id", "tender_awards.contractor_id"],
            name="fk_contracts_tender_award", use_alter=True,
        ),
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
        # Цель составного FK отметки победителя (спека Б2 §2.2).
        UniqueConstraint("id", "object_id", name="uq_tenders_id_object"),
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
        # Цель составного FK отметки победителя (спека Б2 §2.2).
        UniqueConstraint("id", "contractor_id", name="uq_offer_packages_id_contractor"),
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
        # Цель составного FK отметки победителя (спека Б2 §2.2).
        UniqueConstraint("id", "tender_id", "package_id", name="uq_offers_id_tender_package"),
        Index("ix_offers_package_id", "package_id"),
    )


class TenderAward(Base):
    """Отметка победителя тендера (спека Б2 §2.2). История не удаляется:
    «договор не заключён» закрывает отметку, а не стирает её; действующая
    отметка тендера — не более одной (`uq_tender_awards_active`).

    Все ключи составные и без одиночных колонок-посредников: оферта, участник,
    КП и подрядчик обязаны принадлежать одной отметке структурно. Все колонки
    ключей NOT NULL — иначе `MATCH SIMPLE` отключил бы проверку."""
    __tablename__ = "tender_awards"

    id = Column(BigInteger, primary_key=True)
    tender_id = Column(BigInteger, nullable=False)
    object_id = Column(BigInteger, nullable=False)
    offer_id = Column(BigInteger, nullable=False)
    package_id = Column(BigInteger, nullable=False)
    contractor_id = Column(BigInteger, nullable=False)
    # КП, по которому принято решение (оферта победителя финального этапа).
    estimate_id = Column(BigInteger, nullable=False)
    # ИНН блока КП в файле этапа, снят с разбора КП при отметке.
    kp_inn = Column(Text, nullable=False)
    awarded_at = Column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))
    awarded_by = Column(Integer, nullable=False)
    # «Договор не заключён»: дата вводится человеком.
    not_concluded_on = Column(Date, nullable=True)
    not_concluded_note = Column(Text, nullable=True)
    not_concluded_by = Column(Integer, nullable=True)
    not_concluded_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tender_id", "object_id"], ["tenders.id", "tenders.object_id"],
            ondelete="CASCADE", name="fk_tender_awards_tender",
        ),
        ForeignKeyConstraint(
            ["offer_id", "tender_id", "package_id"],
            ["offers.id", "offers.tender_id", "offers.package_id"],
            name="fk_tender_awards_offer",
        ),
        ForeignKeyConstraint(
            ["package_id", "contractor_id"],
            ["offer_packages.id", "offer_packages.contractor_id"],
            name="fk_tender_awards_package",
        ),
        ForeignKeyConstraint(
            ["estimate_id", "offer_id"], ["estimates.id", "estimates.offer_id"],
            name="fk_tender_awards_kp_estimate",
        ),
        ForeignKeyConstraint(
            ["awarded_by"], ["users.id"], ondelete="RESTRICT", name="fk_tender_awards_awarded_by"
        ),
        ForeignKeyConstraint(
            ["not_concluded_by"], ["users.id"], ondelete="RESTRICT",
            name="fk_tender_awards_not_concluded_by",
        ),
        CheckConstraint(TENDER_AWARD_KP_INN_CANONICAL, name="ck_tender_awards_kp_inn_canonical"),
        CheckConstraint(TENDER_AWARD_NOT_CONCLUDED, name="ck_tender_awards_not_concluded"),
        CheckConstraint(TENDER_AWARD_NOTE_NOT_BLANK, name="ck_tender_awards_note_not_blank"),
        UniqueConstraint(
            "id", "object_id", "contractor_id", name="uq_tender_awards_id_object_contractor"
        ),
        # Не более одной действующей отметки на тендер.
        Index(
            "uq_tender_awards_active", "tender_id", unique=True,
            postgresql_where=sa_text("not_concluded_on IS NULL"),
        ),
        Index("ix_tender_awards_tender_id", "tender_id"),
        Index("ix_tender_awards_offer_id", "offer_id"),
        Index("ix_tender_awards_package_id", "package_id"),
        Index("ix_tender_awards_estimate_id", "estimate_id"),
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
    # Задание — копия КП отметки победителя (спека Б2 §2.5); только у исходной
    # сметы договора. Удаление отметки обнуляет ссылку, а не отказывает.
    source_award_id = Column(
        BigInteger,
        ForeignKey(
            "tender_awards.id", ondelete="SET NULL", name="fk_import_jobs_source_award",
            use_alter=True,
        ),
        nullable=True,
    )

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
        CheckConstraint(IMPORT_JOB_SOURCE_AWARD_ORIGINAL, name="ck_import_jobs_source_award"),
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
    # Смета — копия КП отметки основания договора (спека Б2 §2.2); только у
    # исходной сметы договора. Составной ключ (contract_id, source_award_id)
    # держит «копия ссылается на отметку-основание своего договора».
    source_award_id = Column(BigInteger, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    # `foreign_keys` обязателен: у смет и договоров теперь два ключа
    # (`contract_id` и составной `(contract_id, source_award_id)`).
    contract = relationship("Contract", foreign_keys=[contract_id])
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
        CheckConstraint(ESTIMATE_SOURCE_AWARD_ORIGINAL, name="ck_estimates_source_award"),
        # Цель составного FK отметки победителя (спека Б2 §2.2).
        UniqueConstraint("id", "offer_id", name="uq_estimates_id_offer"),
        ForeignKeyConstraint(
            ["contract_id", "source_award_id"],
            ["contracts.id", "contracts.tender_award_id"],
            name="fk_estimates_source_award",
        ),
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
    """Происхождение назначения семьи контексту (спека §2.3; `auto_suggestion` —
    автопринятие опубликованного предложения, спека вариантов §2.4)."""
    manual = "manual"
    suggestion = "suggestion"
    auto_suggestion = "auto_suggestion"


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
    # Варианты и схемы семей (миграция 0019, спека вариантов §2.13): три
    # события с предметом контекст и три с предметом семья.
    "context_variant_assigned",
    "context_family_pending",
    "context_not_work",
    "family_schema_frozen",
    "family_schema_value_added",
    "family_variants_merged",
    # Открытие семей (миграция 0021, спека 3б §2.8, §2.14): предмет — контекст.
    "context_reopened",
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
    "'family_archived', 'family_merged', 'family_schema_frozen', "
    "'family_schema_value_added', 'family_variants_merged')) = (family_id IS NOT NULL)"
)
CK_EVENT_PAYLOAD_NOT_EMPTY = "jsonb_typeof(payload) = 'object' AND payload <> '{}'::jsonb"


# ---------------------------------------------------------------------------
#  Варианты работ и схемы параметров семей (миграция 0019)
# ---------------------------------------------------------------------------
#
# Перечисления и CHECK-выражения шести новых таблиц и новых колонок контекстов
# и заданий (спека `2026-10-02-catalog-variants-design.md` §2.4). Продублированы
# литералами в миграции 0019 (та же дисциплина, что у 0017/0018); parity —
# `test_work_variants_schema.py`.

class SchemaStatus(str, enum.Enum):
    """Статус версии схемы семьи: пересборка, текущая, замещённая, отменённая."""
    building = "building"
    frozen = "frozen"
    superseded = "superseded"
    cancelled = "cancelled"


class SchemaOrigin(str, enum.Enum):
    """Кто завёл версию схемы: задание модели или `admin` вручную."""
    model = "model"
    manual = "manual"


class ValueOrigin(str, enum.Enum):
    """Откуда значение закрытого списка параметра."""
    schema = "schema"
    extension = "extension"
    manual = "manual"


class VariantStatus(str, enum.Enum):
    """Статус варианта: действующий или архивный (варианты не удаляются)."""
    active = "active"
    archived = "archived"


class ValueSource(str, enum.Enum):
    """По чему поставлено значение контекста по параметру."""
    name = "name"
    path = "path"
    manual = "manual"
    path_conflict = "path_conflict"
    none = "none"


class SemanticJobKind(str, enum.Enum):
    """Вид задания очереди."""
    family_suggestion = "family_suggestion"
    family_schema = "family_schema"
    context_values = "context_values"
    # Открытие семей единицы (миграция 0021): предмета-контекста и предмета-семьи нет.
    family_discovery = "family_discovery"


SCHEMA_STATUSES = _sql_str_list(SchemaStatus)
SCHEMA_ORIGINS = _sql_str_list(SchemaOrigin)
VALUE_ORIGINS = _sql_str_list(ValueOrigin)
VARIANT_STATUSES = _sql_str_list(VariantStatus)
VALUE_SOURCES = _sql_str_list(ValueSource)
VARIANT_SPLIT_HINTS = _sql_str_list(("path_conflict",))
SEMANTIC_JOB_KINDS = _sql_str_list(SemanticJobKind)

CK_SCHEMA_CANCELLED_PAIR = "(status = 'cancelled') = (cancelled_at IS NOT NULL)"
CK_SCHEMA_FROZEN_AT_PAIR = "(status IN ('frozen', 'superseded')) = (frozen_at IS NOT NULL)"
CK_SCHEMA_SUPERSEDED_AT_PAIR = "(status = 'superseded') = (superseded_at IS NOT NULL)"
CK_SCHEMA_ORIGIN_FROZEN_BY_PAIR = "(origin = 'manual') = (frozen_by IS NOT NULL)"

CK_PARAMETER_ORDINAL_RANGE = "ordinal BETWEEN 1 AND 3"
CK_PARAMETER_NAME_NOT_BLANK = "btrim(name) <> ''"

CK_PARAMETER_VALUE_NOT_BLANK = "btrim(value) <> '' AND value_norm <> ''"
CK_PARAMETER_VALUE_NOT_SELF_MERGED = "merged_into_id IS NULL OR merged_into_id <> id"

CK_VARIANT_ARCHIVED_PAIR = "(status = 'archived') = (archived_at IS NOT NULL)"
CK_VARIANT_MERGED_NEEDS_ARCHIVED = "merged_into_id IS NULL OR status = 'archived'"

CK_CONTEXT_VALUE_SOURCE_PAIR = "(value_id IS NULL) = (source IN ('path_conflict', 'none'))"

#: Закрывает лазейку `MATCH SIMPLE`: составной FK `(work_variant_id,
#: work_family_id)` при пустой семье пару не проверяет вовсе.
CK_CONTEXT_VARIANT_NEEDS_FAMILY = "work_variant_id IS NULL OR work_family_id IS NOT NULL"
CK_CONTEXT_VARIANT_AT_PAIR = "(work_variant_id IS NULL) = (variant_at IS NULL)"
CK_CONTEXT_VARIANT_PATHS_HASH_PAIR = "(work_variant_id IS NULL) = (variant_paths_hash IS NULL)"
CK_CONTEXT_SPLIT_HINT_NEEDS_VARIANT = "variant_split_hint IS NULL OR work_variant_id IS NOT NULL"
#: Ожидание — целиком или никак: ОДИН тотальный предикат из двух полных ветвей,
#: как `CK_CONTEXT_FAMILY_PROVENANCE` (цепочка равенств `a = b = c` пропускала бы
#: строку с пустой семьёй и заполненными источником и временем).
CK_CONTEXT_PENDING = (
    "(pending_family_id IS NULL AND pending_family_source IS NULL AND pending_suggestion_id IS NULL "
    "AND pending_by IS NULL AND pending_threshold IS NULL AND pending_at IS NULL) "
    "OR (pending_family_id IS NOT NULL AND pending_family_source IS NOT NULL "
    "AND pending_at IS NOT NULL "
    "AND (pending_family_source = 'manual') = (pending_by IS NOT NULL) "
    "AND (pending_family_source <> 'manual') = (pending_suggestion_id IS NOT NULL) "
    "AND (pending_family_source = 'auto_suggestion') = (pending_threshold IS NOT NULL))"
)


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
    # Категория семьи (миграция 0021, спека 3б §2.9). CHECK-а «активна ⟹ категория»
    # нет намеренно: у ранее активных семей поле пусто до прохода разметки.
    family_category_id = Column(
        BigInteger, ForeignKey("family_categories.id", ondelete="RESTRICT"), nullable=True
    )

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


# ---------------------------------------------------------------------------
#  Открытие семей и категории семей (миграция 0021, спека 3б §2.2)
# ---------------------------------------------------------------------------
#
# Справочник категорий, черновики групп ответа открытия, члены групп и
# предложения категории активной семье. Перечисления и CHECK продублированы
# литералами в миграции 0021 (та же дисциплина, что у 0017–0019); parity —
# `test_catalog_discovery_schema.py`.

class DraftGroup(str, enum.Enum):
    """Вид группы ответа открытия: новый черновик, «в активную семью», «не работа»."""
    new = "new"
    existing = "existing"
    not_work = "not_work"


class DraftStatus(str, enum.Enum):
    """Состояние черновика группы: открыт, активирован, слит, отброшен, вытеснен."""
    open = "open"
    activated = "activated"
    merged = "merged"
    discarded = "discarded"
    superseded = "superseded"


class CategoryProposalStatus(str, enum.Enum):
    """Состояние предложения категории активной семье."""
    open = "open"
    applied = "applied"
    superseded = "superseded"


#: Ключи трёх строк справочника, заведённых миграцией (`family_categories.seed_key`).
FAMILY_CATEGORY_SEED_KEYS = ("work", "engineering_system", "costs_services")

DRAFT_GROUPS = _sql_str_list(DraftGroup)
DRAFT_STATUSES = _sql_str_list(DraftStatus)
CATEGORY_PROPOSAL_STATUSES = _sql_str_list(CategoryProposalStatus)

CK_FAMILY_CATEGORY_TITLE_NOT_BLANK = "btrim(title) <> ''"
CK_FAMILY_CATEGORY_DEFINITION_NOT_BLANK = "btrim(definition) <> ''"

#: Форма группы — ОДИН тотальный предикат из трёх полных ветвей, а не набор
#: импликаций: `IS NOT NULL` в ветви `new` обязательны, иначе `btrim(NULL) <> ''`
#: давало бы `NULL`, а `CHECK` отвергает только `FALSE` (`docs/pitfalls/db.md`).
CK_DRAFT_SHAPE = (
    "(grp = 'new' AND title IS NOT NULL AND definition IS NOT NULL "
    "AND btrim(title) <> '' AND btrim(definition) <> '' "
    "AND existing_family_id IS NULL) "
    "OR (grp = 'existing' AND existing_family_id IS NOT NULL AND title IS NULL "
    "AND definition IS NULL AND family_category_id IS NULL AND similar_family_id IS NULL) "
    "OR (grp = 'not_work' AND title IS NULL AND definition IS NULL "
    "AND family_category_id IS NULL AND existing_family_id IS NULL "
    "AND similar_family_id IS NULL)"
)
CK_DRAFT_ACTIVATED_PAIR = "(status = 'activated') = (activated_family_id IS NOT NULL)"
CK_DRAFT_MERGED_PAIR = (
    "(status = 'merged') = (num_nonnulls(merged_into_draft_id, merged_into_family_id) = 1)"
)
CK_DRAFT_MERGE_TARGET_AT_MOST_ONE = "num_nonnulls(merged_into_draft_id, merged_into_family_id) <= 1"
CK_DRAFT_NOT_SELF_MERGED = "merged_into_draft_id IS NULL OR merged_into_draft_id <> id"
#: Решения ставятся только новым черновикам; группы «в активную семью» и «не
#: работа» живут `open` до вытеснения.
CK_DRAFT_DECISION_ONLY_NEW = "grp = 'new' OR status IN ('open', 'superseded')"
CK_DRAFT_DECIDED_BY_PAIR = (
    "(status IN ('activated', 'merged', 'discarded')) = (decided_by IS NOT NULL)"
)
CK_DRAFT_DECIDED_AT_PAIR = "(decided_by IS NULL) = (decided_at IS NULL)"
CK_DRAFT_EDITED_PAIR = "(edited_by IS NULL) = (edited_at IS NULL)"

CK_PROPOSAL_APPLIED_PAIR = "(status = 'applied') = (decided_by IS NOT NULL)"
CK_PROPOSAL_DECIDED_AT_PAIR = "(decided_by IS NULL) = (decided_at IS NULL)"


class FamilyCategory(Base):
    """Справочник категорий семей. Три строки заводит миграция (`seed_key` не
    пуст, автора нет), остальные — оператор. Имя уникально без учёта регистра и
    крайних пробелов (raw SQL индекс `uq_family_categories_title`); определение
    видит модель, когда предлагает категорию черновику (спека 3б §2.9)."""
    __tablename__ = "family_categories"

    id = Column(BigInteger, primary_key=True)
    seed_key = Column(Text, nullable=True)
    title = Column(Text, nullable=False)
    definition = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (
        UniqueConstraint("seed_key", name="uq_family_categories_seed_key"),
        CheckConstraint(
            CK_FAMILY_CATEGORY_TITLE_NOT_BLANK, name="ck_family_categories_title_not_blank"
        ),
        CheckConstraint(
            CK_FAMILY_CATEGORY_DEFINITION_NOT_BLANK,
            name="ck_family_categories_definition_not_blank",
        ),
        CheckConstraint(
            CK_FAMILY_AUTHOR_IFF_NOT_SEED, name="ck_family_categories_author_iff_not_seed"
        ),
        # UNIQUE (lower(btrim(title))) — выражение, raw SQL в миграции 0021
        # (alembic/env.py RAW_SQL_INDEXES: uq_family_categories_title).
    )


class FamilyDraft(Base):
    """Группа ответа открытия единицы (спека 3б §2.2): новый черновик семьи,
    группа имён «в активную семью» либо группа «не работа». Форму по виду держит
    `CK_DRAFT_SHAPE`; слияние — только внутри открытия (составной FK на
    `(id, job_id)`), одна группа «не работа» на открытие — частичный уникальный
    индекс `uq_family_drafts_not_work_per_job` (raw SQL, `RAW_SQL_INDEXES`)."""
    __tablename__ = "family_drafts"

    id = Column(BigInteger, primary_key=True)
    job_id = Column(BigInteger, ForeignKey("semantic_jobs.id", ondelete="RESTRICT"), nullable=False)
    unit_id = Column(
        Integer, ForeignKey("units_of_measure.id", ondelete="RESTRICT"), nullable=True
    )
    ordinal = Column(Integer, nullable=False)
    grp = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    definition = Column(Text, nullable=True)
    family_category_id = Column(
        BigInteger, ForeignKey("family_categories.id", ondelete="SET NULL"), nullable=True
    )
    existing_family_id = Column(
        BigInteger, ForeignKey("work_families.id", ondelete="RESTRICT"), nullable=True
    )
    similar_family_id = Column(
        BigInteger, ForeignKey("work_families.id", ondelete="RESTRICT"), nullable=True
    )
    status = Column(Text, nullable=False)
    activated_family_id = Column(
        BigInteger, ForeignKey("work_families.id", ondelete="RESTRICT"), nullable=True
    )
    # Слияние в другой черновик — составным FK `(merged_into_draft_id, job_id)`
    # ниже; одиночного ключа нет: он не удержал бы «только внутри открытия».
    merged_into_draft_id = Column(BigInteger, nullable=True)
    merged_into_family_id = Column(
        BigInteger, ForeignKey("work_families.id", ondelete="RESTRICT"), nullable=True
    )
    edited_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    edited_at = Column(DateTime(timezone=True), nullable=True)
    decided_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = _created_at()

    __table_args__ = (
        UniqueConstraint("job_id", "ordinal", name="uq_family_drafts_job_ordinal"),
        # Цель составных FK: слияние внутри открытия и члены группы.
        UniqueConstraint("id", "job_id", name="uq_family_drafts_id_job"),
        ForeignKeyConstraint(
            ["merged_into_draft_id", "job_id"],
            ["family_drafts.id", "family_drafts.job_id"],
            ondelete="RESTRICT",
            name="fk_family_drafts_merged_into_job",
        ),
        CheckConstraint(f"grp IN ({DRAFT_GROUPS})", name="ck_family_drafts_grp"),
        CheckConstraint(f"status IN ({DRAFT_STATUSES})", name="ck_family_drafts_status"),
        CheckConstraint(CK_DRAFT_SHAPE, name="ck_family_drafts_shape"),
        CheckConstraint(CK_DRAFT_ACTIVATED_PAIR, name="ck_family_drafts_activated_pair"),
        CheckConstraint(CK_DRAFT_MERGED_PAIR, name="ck_family_drafts_merged_pair"),
        CheckConstraint(
            CK_DRAFT_MERGE_TARGET_AT_MOST_ONE, name="ck_family_drafts_merge_target_at_most_one"
        ),
        CheckConstraint(CK_DRAFT_NOT_SELF_MERGED, name="ck_family_drafts_not_self_merged"),
        CheckConstraint(CK_DRAFT_DECISION_ONLY_NEW, name="ck_family_drafts_decision_only_new"),
        CheckConstraint(CK_DRAFT_DECIDED_BY_PAIR, name="ck_family_drafts_decided_by_pair"),
        CheckConstraint(CK_DRAFT_DECIDED_AT_PAIR, name="ck_family_drafts_decided_at_pair"),
        CheckConstraint(CK_DRAFT_EDITED_PAIR, name="ck_family_drafts_edited_pair"),
        # UNIQUE (job_id) WHERE grp = 'not_work' — частичный, raw SQL в миграции 0021
        # (RAW_SQL_INDEXES: uq_family_drafts_not_work_per_job).
    )


class FamilyDraftMember(Base):
    """Контекст за группой открытия. Пишется один раз обработкой ответа и больше
    не переносится; контекст — в одной группе открытия (`UNIQUE(job_id,
    context_id)`), группа — своего открытия (составной FK `(draft_id, job_id)`)."""
    __tablename__ = "family_draft_members"

    draft_id = Column(BigInteger, nullable=False)
    job_id = Column(BigInteger, nullable=False)
    context_id = Column(
        BigInteger, ForeignKey("catalog_contexts.id", ondelete="RESTRICT"), nullable=False
    )
    name_index = Column(Integer, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("draft_id", "context_id", name="pk_family_draft_members"),
        ForeignKeyConstraint(
            ["draft_id", "job_id"],
            ["family_drafts.id", "family_drafts.job_id"],
            ondelete="CASCADE",
            name="fk_family_draft_members_draft_job",
        ),
        UniqueConstraint("job_id", "context_id", name="uq_family_draft_members_job_context"),
    )


class FamilyCategoryProposal(Base):
    """Предложение категории активной семье (спека 3б §2.10): временное —
    удаляется вместе с категорией; применяется шагом активации."""
    __tablename__ = "family_category_proposals"

    job_id = Column(BigInteger, ForeignKey("semantic_jobs.id", ondelete="RESTRICT"), nullable=False)
    family_id = Column(
        BigInteger, ForeignKey("work_families.id", ondelete="RESTRICT"), nullable=False
    )
    family_category_id = Column(
        BigInteger, ForeignKey("family_categories.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(Text, nullable=False)
    decided_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("job_id", "family_id", name="pk_family_category_proposals"),
        CheckConstraint(
            f"status IN ({CATEGORY_PROPOSAL_STATUSES})", name="ck_family_category_proposals_status"
        ),
        CheckConstraint(CK_PROPOSAL_APPLIED_PAIR, name="ck_family_category_proposals_applied_pair"),
        CheckConstraint(
            CK_PROPOSAL_DECIDED_AT_PAIR, name="ck_family_category_proposals_decided_at_pair"
        ),
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
    # Вариант (миграция 0019): составной отложенный FK `(work_variant_id,
    # work_family_id)` в `__table_args__` проверяет, что семья варианта равна
    # семье контекста; одиночного ForeignKey на `work_variant_id` нет.
    work_variant_id = Column(BigInteger, nullable=True)
    variant_at = Column(DateTime(timezone=True), nullable=True)
    variant_paths_hash = Column(Text, nullable=True)
    variant_split_hint = Column(Text, nullable=True)
    # Ожидающее назначение семьи — целиком или никак (`CK_CONTEXT_PENDING`).
    pending_family_id = Column(
        BigInteger, ForeignKey("work_families.id", ondelete="RESTRICT"), nullable=True
    )
    pending_family_source = Column(Text, nullable=True)
    # use_alter: `family_suggestions.context_id` ссылается обратно на контекст.
    pending_suggestion_id = Column(
        BigInteger,
        ForeignKey(
            "family_suggestions.id", ondelete="RESTRICT",
            use_alter=True, name="fk_catalog_contexts_pending_suggestion_id",
        ),
        nullable=True,
    )
    pending_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    pending_threshold = Column(Numeric, nullable=True)
    pending_at = Column(DateTime(timezone=True), nullable=True)

    bucket = relationship("ContextBucket")
    work_family = relationship("WorkFamily", foreign_keys=[work_family_id])

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
        # ОТЛОЖЕННЫЙ до commit: слияние семей меняет семью у контекста, варианта и
        # версии схемы, и ни один порядок немедленных проверок её не проходит.
        ForeignKeyConstraint(
            ["work_variant_id", "work_family_id"],
            ["work_variants.id", "work_variants.family_id"],
            ondelete="RESTRICT", deferrable=True, initially="DEFERRED",
            name="fk_catalog_contexts_work_variant_family",
        ),
        CheckConstraint(
            f"pending_family_source IS NULL OR pending_family_source IN ({FAMILY_SOURCES})",
            name="ck_catalog_contexts_pending_family_source",
        ),
        CheckConstraint(
            f"variant_split_hint IS NULL OR variant_split_hint IN ({VARIANT_SPLIT_HINTS})",
            name="ck_catalog_contexts_variant_split_hint",
        ),
        CheckConstraint(
            CK_CONTEXT_VARIANT_NEEDS_FAMILY, name="ck_catalog_contexts_variant_needs_family"
        ),
        CheckConstraint(CK_CONTEXT_VARIANT_AT_PAIR, name="ck_catalog_contexts_variant_at_pair"),
        CheckConstraint(
            CK_CONTEXT_VARIANT_PATHS_HASH_PAIR, name="ck_catalog_contexts_variant_paths_hash_pair"
        ),
        CheckConstraint(
            CK_CONTEXT_SPLIT_HINT_NEEDS_VARIANT, name="ck_catalog_contexts_split_hint_needs_variant"
        ),
        CheckConstraint(CK_CONTEXT_PENDING, name="ck_catalog_contexts_pending"),
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
#  Очередь семантических предложений (миграция 0018)
# ---------------------------------------------------------------------------
#
# Пять таблиц (спека `2026-09-28-semantic-suggestions-design.md` §2.4): задание
# на семантическое предложение, попытка вызова провайдера, само предложение,
# удержанная пачка сверх потолка события и состояние исполнителя захвата
# (ровно одна строка).
#
# **Цикл FK.** `semantic_jobs.result_suggestion_id` → `family_suggestions` и
# `family_suggestions.job_id` → `semantic_jobs` ссылаются друг на друга;
# `use_alter=True` разрывает цикл для сортировки метаданных (`Base.metadata`),
# миграция создаёт обе таблицы, а затем добавляет эту FK отдельным
# `op.create_foreign_key`.
#
# **Трёхзначная логика CHECK** (`docs/pitfalls/db.md`): каждая равносильность
# ниже безопасна, потому что участвующая NOT NULL колонка (`status`,
# `claim_paused`, `is_published`) сравнивается голым равенством лишь с одной
# стороны, а вторая сторона — всегда `IS [NOT] NULL` (булево, никогда не
# `NULL`); ни одна равносильность не сравнивает две NULLABLE колонки напрямую.

class SemanticJobStatus(str, enum.Enum):
    """Статус задания на семантическое предложение (спека §2.4)."""
    pending = "pending"
    running = "running"
    done = "done"
    error = "error"
    cancelled = "cancelled"
    privacy_hold = "privacy_hold"


class SemanticCancelReason(str, enum.Enum):
    """Причина отмены задания — заполнена ровно при `status='cancelled'`."""
    input_changed = "input_changed"
    not_applicable = "not_applicable"
    privacy_declined = "privacy_declined"
    stale_hold = "stale_hold"


class SemanticAttemptOutcome(str, enum.Enum):
    """Исход попытки вызова провайдера (спека §2.4)."""
    ok = "ok"
    transient_error = "transient_error"
    permanent_error = "permanent_error"
    schema_error = "schema_error"
    lost_claim = "lost_claim"


class SuggestionUnpublishedReason(str, enum.Enum):
    """Причина, по которой предложение не (более) опубликовано."""
    stale_fingerprint = "stale_fingerprint"
    lost_claim = "lost_claim"
    context_not_applicable = "context_not_applicable"
    rejected = "rejected"


class SuggestionDecision(str, enum.Enum):
    """Решение оператора по опубликованному предложению."""
    accepted = "accepted"
    rejected = "rejected"
    other_family = "other_family"
    family_created = "family_created"
    # Миграция 0019: человек подтвердил, смена отложена ожиданием; автопринятие.
    accepted_pending = "accepted_pending"
    auto_accepted = "auto_accepted"
    auto_pending = "auto_pending"
    auto_superseded = "auto_superseded"


class ReconcileBatchSource(str, enum.Enum):
    """Откуда взялась удержанная пачка (спека §2.4). `import_` — зарезервированное
    имя Python (`import`), значение в БД — `'import'`."""
    import_ = "import"
    operation = "operation"
    mass = "mass"
    unit_reask = "unit_reask"
    config_reask = "config_reask"


class ReconcileBatchStatus(str, enum.Enum):
    """Статус удержанной пачки: решения нет либо принято (спека §2.4)."""
    held = "held"
    approved = "approved"
    discarded = "discarded"


#: Семь списков `IN (...)` — литералы дублируют миграцию 0018 (та же
#: дисциплина, что у миграций 0002/0003/0015/0017); расхождение ловит
#: test_semantic_queue_schema.py::TestParityWithMigration.
SEMANTIC_JOB_STATUSES = _sql_str_list(SemanticJobStatus)
SEMANTIC_CANCEL_REASONS = _sql_str_list(SemanticCancelReason)
SEMANTIC_ATTEMPT_OUTCOMES = _sql_str_list(SemanticAttemptOutcome)
SUGGESTION_UNPUBLISHED_REASONS = _sql_str_list(SuggestionUnpublishedReason)
SUGGESTION_DECISIONS = _sql_str_list(SuggestionDecision)
RECONCILE_BATCH_SOURCES = _sql_str_list(ReconcileBatchSource)
RECONCILE_BATCH_STATUSES = _sql_str_list(ReconcileBatchStatus)

#: Равносильности CHECK — продублированы в миграции 0018; parity —
#: test_semantic_queue_schema.py::TestParityWithMigration.
CK_SEMANTIC_JOBS_STATUS_CANCEL_REASON_PAIR = (
    "(status = 'cancelled') = (cancel_reason IS NOT NULL)"
)
CK_SEMANTIC_JOBS_STATUS_CLAIM_TOKEN_PAIR = "(status = 'running') = (claim_token IS NOT NULL)"
CK_SEMANTIC_JOBS_PRIVACY_HOLD_REQUIRES_MATCHES = (
    "status <> 'privacy_hold' OR privacy_matches IS NOT NULL"
)

#: Автор у решения человека есть всегда, у автоматических решений его нет
#: (миграция 0019; до неё — `(decision IS NULL) = (decided_by IS NULL)`).
CK_FAMILY_SUGGESTIONS_DECISION_AUTHOR_PAIR = (
    "(decided_by IS NULL) = "
    "(decision IS NULL OR decision IN ('auto_accepted', 'auto_pending', 'auto_superseded'))"
)
#: Заданиям очереди три вида (миграция 0019): предмет `family_schema` — семья,
#: прочих — контекст; версия схемы обязательна у новых видов; результат-предложение
#: бывает только у `family_suggestion`.
CK_SEMANTIC_JOBS_SCHEMA_SUBJECT = (
    "(kind = 'family_schema') = (context_id IS NULL AND family_id IS NOT NULL)"
)
#: Миграция 0021 переписала обе равносильности под четвёртый вид: предмет-контекст
#: у `family_suggestion` и `context_values`, версия схемы — у `family_schema` и
#: `context_values`; у `family_discovery` нет ни того, ни другого.
CK_SEMANTIC_JOBS_CONTEXT_SUBJECT = (
    "(kind IN ('family_suggestion', 'context_values')) = (context_id IS NOT NULL)"
)
CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND = (
    "(kind IN ('family_schema', 'context_values')) = (schema_id IS NOT NULL)"
)
#: У открытия единицы нет ни контекста, ни семьи (предмет — единица, `unit_id`).
CK_SEMANTIC_JOBS_DISCOVERY_SUBJECT = (
    "kind <> 'family_discovery' OR (context_id IS NULL AND family_id IS NULL)"
)
CK_SEMANTIC_JOBS_RESULT_SUGGESTION_KIND = (
    "result_suggestion_id IS NULL OR kind = 'family_suggestion'"
)

CK_FAMILY_SUGGESTIONS_DECISION_AT_PAIR = "(decision IS NULL) = (decided_at IS NULL)"
CK_FAMILY_SUGGESTIONS_PUBLISHED_NO_UNPUBLISHED_REASON = (
    "NOT is_published OR unpublished_reason IS NULL"
)

CK_RECONCILE_BATCHES_HELD_NO_DECIDED_BY = "(status = 'held') = (decided_by IS NULL)"
CK_RECONCILE_BATCHES_HELD_NO_DECIDED_AT = "(status = 'held') = (decided_at IS NULL)"

CK_WORKER_STATE_PAUSED_REASON_PAIR = "claim_paused = (paused_reason IS NOT NULL)"
CK_WORKER_STATE_PAUSED_ATTEMPT_PAIR = "claim_paused = (paused_attempt_id IS NOT NULL)"
CK_WORKER_STATE_PAUSED_AT_PAIR = "claim_paused = (paused_at IS NOT NULL)"
CK_WORKER_STATE_RESUMED_PAIR = "(last_resumed_by IS NULL) = (last_resumed_at IS NULL)"
CK_WORKER_STATE_SINGLETON_ID = "id = 1"


class SemanticReconcileBatch(Base):
    """Удержанная пачка сверх потолка события (спека §2.4).

    `held_fingerprints` — АУДИТ того, что было удержано на момент создания
    пачки; исполнение сверки (§2.10) идёт по ТЕКУЩИМ отпечаткам, сохранённые
    не перезаписываются. Частичный `UNIQUE (fingerprints_hash) WHERE
    status='held'` — raw SQL в миграции 0018 (`alembic/env.py
    RAW_SQL_INDEXES`): одна удержанная пачка на набор отпечатков, и этот же
    индекс служит арбитром `INSERT ... ON CONFLICT (fingerprints_hash) WHERE
    status='held' DO NOTHING`.
    """
    __tablename__ = "semantic_reconcile_batches"

    id = Column(BigInteger, primary_key=True)
    source = Column(Text, nullable=False)
    import_job_id = Column(
        BigInteger, ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True
    )
    unit_id = Column(BigInteger, nullable=True)
    # none_as_null=True: без него ORM `None` ложится JSON-литералом `null`, и
    # NOT NULL спеки §2.4 не срабатывает — тот же класс дефекта, что у
    # `SemanticJob.privacy_matches` ниже.
    held_fingerprints = Column(JSONB(none_as_null=True), nullable=False)
    fingerprints_hash = Column(Text, nullable=False)
    contexts_count = Column(Integer, nullable=False)
    reserve_estimate_usd = Column(Numeric, nullable=False)
    cached_estimate_usd = Column(Numeric, nullable=False)
    status = Column(Text, nullable=False)
    decided_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = _created_at()

    __table_args__ = (
        CheckConstraint(f"source IN ({RECONCILE_BATCH_SOURCES})", name="ck_semantic_reconcile_batches_source"),
        CheckConstraint(f"status IN ({RECONCILE_BATCH_STATUSES})", name="ck_semantic_reconcile_batches_status"),
        CheckConstraint(
            CK_RECONCILE_BATCHES_HELD_NO_DECIDED_BY,
            name="ck_semantic_reconcile_batches_held_no_decided_by",
        ),
        CheckConstraint(
            CK_RECONCILE_BATCHES_HELD_NO_DECIDED_AT,
            name="ck_semantic_reconcile_batches_held_no_decided_at",
        ),
        # UNIQUE (fingerprints_hash) WHERE status='held' — частичный, raw SQL
        # в миграции 0018 (RAW_SQL_INDEXES: uq_semantic_reconcile_batches_fingerprints_held).
    )


class SemanticJob(Base):
    """Одно задание на (контекст, тело запроса) семантического предложения
    (спека §2.4).

    `result_suggestion_id` → `family_suggestions` — FK, добавленный отдельным
    `op.create_foreign_key` в миграции 0018 (цикл с `family_suggestions.job_id`,
    `use_alter=True` здесь). `unit_id` — не FK (единица контекста, порядок
    захвата, §2.5 спеки), это ось приоритезации, не ссылка.
    """
    __tablename__ = "semantic_jobs"

    id = Column(BigInteger, primary_key=True)
    # Вид задания (миграция 0019). Python-умолчание, а не серверное: строки
    # фичи 2 создаются без вида, а новые виды без явного `kind` отвергаются
    # CHECK-ами предмета.
    kind = Column(Text, nullable=False, default=SemanticJobKind.family_suggestion.value)
    # NULL только у `family_schema` (предмет — семья).
    context_id = Column(
        BigInteger, ForeignKey("catalog_contexts.id", ondelete="RESTRICT"), nullable=True
    )
    family_id = Column(BigInteger, ForeignKey("work_families.id", ondelete="RESTRICT"), nullable=True)
    # use_alter: `family_parameter_schemas.job_id` ссылается обратно на задание.
    schema_id = Column(
        BigInteger,
        ForeignKey(
            "family_parameter_schemas.id", ondelete="RESTRICT",
            use_alter=True, name="fk_semantic_jobs_schema_id",
        ),
        nullable=True,
    )
    paths_hash = Column(Text, nullable=True)
    request_hash = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    cancel_reason = Column(Text, nullable=True)
    retry_generation = Column(Integer, nullable=False, server_default=sa_text("0"))
    attempts_in_generation = Column(Integer, nullable=False, server_default=sa_text("0"))
    next_attempt_at = Column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )
    claim_token = Column(PgUUID(as_uuid=True), nullable=True)
    last_error_class = Column(Text, nullable=True)
    result_suggestion_id = Column(
        BigInteger,
        ForeignKey(
            "family_suggestions.id", ondelete="SET NULL",
            use_alter=True, name="fk_semantic_jobs_result_suggestion_id",
        ),
        nullable=True,
    )
    # none_as_null=True: Python `None` обязан лечь SQL NULL, а не JSON-литералом
    # `null` — иначе `privacy_matches IS NOT NULL` в CHECK видит JSON `null`
    # как «значение есть» и privacy_hold без реальных данных проходит молча.
    privacy_matches = Column(JSONB(none_as_null=True), nullable=True)
    privacy_released_matches = Column(JSONB(none_as_null=True), nullable=True)
    privacy_decided_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    privacy_decided_at = Column(DateTime(timezone=True), nullable=True)
    unit_id = Column(BigInteger, nullable=True)
    batch_id = Column(
        BigInteger, ForeignKey("semantic_reconcile_batches.id", ondelete="SET NULL"), nullable=True
    )
    # Оси запроса для журнала, вне ключа — NOT NULL (решение оркестратора
    # задачи 1: задание всегда создаётся из отрендеренного запроса).
    prompt_version = Column(Text, nullable=False)
    model_requested = Column(Text, nullable=False)
    place_dictionary_version = Column(SmallInteger, nullable=False)
    candidates_hash = Column(Text, nullable=False)
    prefix_hash = Column(Text, nullable=False)
    input_hash = Column(Text, nullable=False)
    response_schema_version = Column(Text, nullable=False)
    serialization_version = Column(Text, nullable=False)
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (
        # UNIQUE (kind, COALESCE(context_id,-1), COALESCE(family_id,-1),
        # COALESCE(schema_id,-1), request_hash) — выражение с COALESCE, raw SQL в
        # миграции 0019 (RAW_SQL_INDEXES: uq_semantic_jobs_subject_request_hash);
        # заменил `uq_semantic_jobs_context_request_hash` миграции 0018.
        CheckConstraint(f"kind IN ({SEMANTIC_JOB_KINDS})", name="ck_semantic_jobs_kind"),
        CheckConstraint(CK_SEMANTIC_JOBS_SCHEMA_SUBJECT, name="ck_semantic_jobs_schema_subject"),
        CheckConstraint(CK_SEMANTIC_JOBS_CONTEXT_SUBJECT, name="ck_semantic_jobs_context_subject"),
        CheckConstraint(CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND, name="ck_semantic_jobs_schema_id_by_kind"),
        CheckConstraint(
            CK_SEMANTIC_JOBS_DISCOVERY_SUBJECT, name="ck_semantic_jobs_discovery_subject"
        ),
        CheckConstraint(
            CK_SEMANTIC_JOBS_RESULT_SUGGESTION_KIND, name="ck_semantic_jobs_result_suggestion_kind"
        ),
        # UNIQUE (COALESCE(unit_id,-1)) WHERE kind = 'family_discovery' AND status IN
        # ('pending','running','privacy_hold') — raw SQL в миграции 0021
        # (RAW_SQL_INDEXES: uq_semantic_jobs_discovery_live): одно живое открытие на единицу.
        CheckConstraint(f"status IN ({SEMANTIC_JOB_STATUSES})", name="ck_semantic_jobs_status"),
        CheckConstraint(
            f"cancel_reason IS NULL OR cancel_reason IN ({SEMANTIC_CANCEL_REASONS})",
            name="ck_semantic_jobs_cancel_reason",
        ),
        CheckConstraint(
            CK_SEMANTIC_JOBS_STATUS_CANCEL_REASON_PAIR, name="ck_semantic_jobs_status_cancel_reason_pair"
        ),
        CheckConstraint(
            CK_SEMANTIC_JOBS_STATUS_CLAIM_TOKEN_PAIR, name="ck_semantic_jobs_status_claim_token_pair"
        ),
        CheckConstraint(
            CK_SEMANTIC_JOBS_PRIVACY_HOLD_REQUIRES_MATCHES,
            name="ck_semantic_jobs_privacy_hold_requires_matches",
        ),
        Index(
            "idx_semantic_jobs_status_unit_next_attempt", "status", "unit_id", "next_attempt_at"
        ),
    )


class SemanticJobAttempt(Base):
    """Одна строка на вызов провайдера (спека §2.4).

    Колонки, известные ДО вызова (`claim_token`, `retry_generation`,
    `started_at`, `reserve_usd`, `reserve_exceeded`, `prefix_hash`,
    `privacy_dictionary_hash`), — NOT NULL; всё, что известно только ПОСЛЕ
    завершения попытки (исход, ошибка, ответ, токены, фактическая
    модель/провайдер, стоимость), — NULL до завершения.
    """
    __tablename__ = "semantic_job_attempts"

    id = Column(BigInteger, primary_key=True)
    job_id = Column(BigInteger, ForeignKey("semantic_jobs.id", ondelete="CASCADE"), nullable=False)
    claim_token = Column(PgUUID(as_uuid=True), nullable=False)
    retry_generation = Column(Integer, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(Text, nullable=True)
    error_class = Column(Text, nullable=True)
    error_text = Column(Text, nullable=True)
    raw_response = Column(Text, nullable=True)
    validation_error = Column(Text, nullable=True)
    actual_model = Column(Text, nullable=True)
    provider = Column(Text, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    cache_write_tokens = Column(Integer, nullable=True)
    cached_tokens = Column(Integer, nullable=True)
    reserve_usd = Column(Numeric, nullable=False)
    cost_usd = Column(Numeric, nullable=True)
    reserve_exceeded = Column(Boolean, nullable=False, server_default=sa_text("false"))
    prefix_hash = Column(Text, nullable=False)
    privacy_dictionary_hash = Column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            f"outcome IS NULL OR outcome IN ({SEMANTIC_ATTEMPT_OUTCOMES})",
            name="ck_semantic_job_attempts_outcome",
        ),
        Index("idx_semantic_job_attempts_prefix_hash", "prefix_hash"),
        Index("idx_semantic_job_attempts_started_at", "started_at"),
    )


class FamilySuggestion(Base):
    """Одна строка на каждый схемно-валидный ответ модели (спека §2.4).

    `family_id IS NULL` — «новая семья» (`new_family_name` тогда заполнено).
    Частичный `UNIQUE (context_id) WHERE is_published` — raw SQL в миграции
    0018 (`RAW_SQL_INDEXES`): не больше одного опубликованного предложения на
    контекст.
    """
    __tablename__ = "family_suggestions"

    id = Column(BigInteger, primary_key=True)
    context_id = Column(
        BigInteger, ForeignKey("catalog_contexts.id", ondelete="RESTRICT"), nullable=False
    )
    job_id = Column(BigInteger, ForeignKey("semantic_jobs.id", ondelete="CASCADE"), nullable=False)
    attempt_id = Column(
        BigInteger, ForeignKey("semantic_job_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    request_hash = Column(Text, nullable=False)
    candidates_hash = Column(Text, nullable=False)
    # none_as_null=True: без него ORM `None` ложится JSON-литералом `null`, и
    # NOT NULL спеки §2.4 не срабатывает.
    candidates_snapshot = Column(JSONB(none_as_null=True), nullable=False)
    family_id = Column(BigInteger, ForeignKey("work_families.id", ondelete="SET NULL"), nullable=True)
    new_family_name = Column(Text, nullable=True)
    confidence = Column(Numeric, nullable=False)
    reason = Column(Text, nullable=False)
    is_published = Column(Boolean, nullable=False, server_default=sa_text("false"))
    unpublished_reason = Column(Text, nullable=True)
    decision = Column(Text, nullable=True)
    decided_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = _created_at()

    __table_args__ = (
        CheckConstraint(
            f"unpublished_reason IS NULL OR unpublished_reason IN ({SUGGESTION_UNPUBLISHED_REASONS})",
            name="ck_family_suggestions_unpublished_reason",
        ),
        CheckConstraint(
            f"decision IS NULL OR decision IN ({SUGGESTION_DECISIONS})",
            name="ck_family_suggestions_decision",
        ),
        CheckConstraint(
            CK_FAMILY_SUGGESTIONS_DECISION_AUTHOR_PAIR, name="ck_family_suggestions_decision_author_pair"
        ),
        CheckConstraint(
            CK_FAMILY_SUGGESTIONS_DECISION_AT_PAIR, name="ck_family_suggestions_decision_at_pair"
        ),
        CheckConstraint(
            CK_FAMILY_SUGGESTIONS_PUBLISHED_NO_UNPUBLISHED_REASON,
            name="ck_family_suggestions_published_no_unpublished_reason",
        ),
        # UNIQUE (context_id) WHERE is_published — частичный, raw SQL в
        # миграции 0018 (RAW_SQL_INDEXES: uq_family_suggestions_context_id_published).
    )


class SemanticWorkerState(Base):
    """Состояние исполнителя захвата — РОВНО одна строка, `id=1` (спека §2.4,
    решение спеки 6: флаг остановки захвата — таблица из одной строки, а не
    настройка, обязан пережить перезапуск).

    Остановлен ⟺ `paused_reason`/`paused_attempt_id`/`paused_at` заполнены ВСЕ
    три; идёт ⟺ все три пусты. `paused_attempt_id` → `semantic_job_attempts` —
    обычный inline FK (никакого цикла с этой таблицей нет); `ON DELETE
    RESTRICT`, а не `SET NULL` — `SET NULL` сработать не может НИКОГДА:
    единственный путь к NULL здесь — `claim_paused=false`, а CHECK
    `ck_semantic_worker_state_paused_attempt_pair` уже требует его NULL в этом
    состоянии, и запрещает NULL при `claim_paused=true`. Строку вставляет сама
    миграция 0018; при очистке доменных таблиц
    (`tests/conftest.py::_truncate_domain_tables`) она пересоздаётся в той же
    транзакции, иначе захват (задача 10) не работает.
    """
    __tablename__ = "semantic_worker_state"

    # autoincrement=False: PK — не суррогатный счётчик, а фиксированный
    # singleton `id=1` (CHECK ниже); без этого SQLAlchemy завёл бы
    # `smallserial`/последовательность, которая здесь не нужна и не по спеке.
    id = Column(SmallInteger, primary_key=True, autoincrement=False)
    claim_paused = Column(Boolean, nullable=False, server_default=sa_text("false"))
    paused_reason = Column(Text, nullable=True)
    paused_attempt_id = Column(
        BigInteger,
        ForeignKey("semantic_job_attempts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    paused_at = Column(DateTime(timezone=True), nullable=True)
    last_resumed_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    last_resumed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(CK_WORKER_STATE_SINGLETON_ID, name="ck_semantic_worker_state_singleton_id"),
        CheckConstraint(
            CK_WORKER_STATE_PAUSED_REASON_PAIR, name="ck_semantic_worker_state_paused_reason_pair"
        ),
        CheckConstraint(
            CK_WORKER_STATE_PAUSED_ATTEMPT_PAIR, name="ck_semantic_worker_state_paused_attempt_pair"
        ),
        CheckConstraint(
            CK_WORKER_STATE_PAUSED_AT_PAIR, name="ck_semantic_worker_state_paused_at_pair"
        ),
        CheckConstraint(CK_WORKER_STATE_RESUMED_PAIR, name="ck_semantic_worker_state_resumed_pair"),
    )


# ---------------------------------------------------------------------------
#  Варианты работ и схемы параметров семей: таблицы (миграция 0019)
# ---------------------------------------------------------------------------
#
# Шесть таблиц спеки `2026-10-02-catalog-variants-design.md` §2.4. Перечисления
# и CHECK-выражения — в блоке констант выше, перед `WorkFamily`.
#
# **Отложенных FK ровно два** (`DEFERRABLE INITIALLY DEFERRED`): контекст →
# вариант (`CatalogContext`) и вариант → версия схемы (`WorkVariant`). Слияние
# семей меняет семью у контекста, варианта и версии схемы в одной транзакции;
# все прочие ссылки проверяются немедленно.
#
# **Ничего не удаляется:** ссылки `RESTRICT`, кроме каскада версия → параметры →
# значения и `job_id → SET NULL`; варианты и значения архивируются.

class FamilyParameterSchema(Base):
    """Версия схемы семьи: от 0 до 3 параметров с закрытыми списками значений.

    Не больше одной `frozen` (текущая) и не больше одной `building`
    (пересборка) на семью — частичные уникальные индексы, raw SQL в миграции
    0019 (`RAW_SQL_INDEXES`: `uq_family_parameter_schemas_frozen`,
    `uq_family_parameter_schemas_building`).
    """
    __tablename__ = "family_parameter_schemas"

    id = Column(BigInteger, primary_key=True)
    family_id = Column(
        BigInteger, ForeignKey("work_families.id", ondelete="RESTRICT"), nullable=False
    )
    version = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)
    origin = Column(Text, nullable=False)
    # use_alter: цикл `semantic_jobs` → `catalog_contexts` → `work_variants` →
    # `family_parameter_schemas` → `semantic_jobs` без разрыва метаданные
    # отсортировать не могут.
    job_id = Column(
        BigInteger,
        ForeignKey(
            "semantic_jobs.id", ondelete="SET NULL",
            use_alter=True, name="fk_family_parameter_schemas_job_id",
        ),
        nullable=True,
    )
    frozen_by = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at = _created_at()
    frozen_at = Column(DateTime(timezone=True), nullable=True)
    superseded_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    family = relationship("WorkFamily")

    __table_args__ = (
        UniqueConstraint("family_id", "version", name="uq_family_parameter_schemas_family_version"),
        # Цель составных FK `work_variants (schema_id, family_id)`.
        UniqueConstraint("id", "family_id", name="uq_family_parameter_schemas_id_family"),
        CheckConstraint(f"status IN ({SCHEMA_STATUSES})", name="ck_family_parameter_schemas_status"),
        CheckConstraint(f"origin IN ({SCHEMA_ORIGINS})", name="ck_family_parameter_schemas_origin"),
        CheckConstraint(CK_SCHEMA_CANCELLED_PAIR, name="ck_family_parameter_schemas_cancelled_pair"),
        CheckConstraint(CK_SCHEMA_FROZEN_AT_PAIR, name="ck_family_parameter_schemas_frozen_at_pair"),
        CheckConstraint(
            CK_SCHEMA_SUPERSEDED_AT_PAIR, name="ck_family_parameter_schemas_superseded_at_pair"
        ),
        CheckConstraint(
            CK_SCHEMA_ORIGIN_FROZEN_BY_PAIR, name="ck_family_parameter_schemas_origin_frozen_by_pair"
        ),
        # UNIQUE (family_id) WHERE status = 'frozen' и UNIQUE (family_id) WHERE
        # status = 'building' — частичные, raw SQL в миграции 0019.
    )


class FamilyParameter(Base):
    """Параметр версии схемы: порядковый номер 1..3 и имя."""
    __tablename__ = "family_parameters"

    id = Column(BigInteger, primary_key=True)
    schema_id = Column(
        BigInteger, ForeignKey("family_parameter_schemas.id", ondelete="CASCADE"), nullable=False
    )
    ordinal = Column(SmallInteger, nullable=False)
    name = Column(Text, nullable=False)
    name_norm = Column(Text, nullable=False)

    schema = relationship("FamilyParameterSchema")

    __table_args__ = (
        UniqueConstraint("schema_id", "ordinal", name="uq_family_parameters_schema_ordinal"),
        # Цель составных FK значений варианта и значений контекста.
        UniqueConstraint("id", "schema_id", name="uq_family_parameters_id_schema"),
        CheckConstraint(CK_PARAMETER_ORDINAL_RANGE, name="ck_family_parameters_ordinal_range"),
        CheckConstraint(CK_PARAMETER_NAME_NOT_BLANK, name="ck_family_parameters_name_not_blank"),
    )


class FamilyParameterValue(Base):
    """Значение закрытого списка параметра. «Не уточнено» — отсутствие строки,
    а не пустая строка (`CK_PARAMETER_VALUE_NOT_BLANK`)."""
    __tablename__ = "family_parameter_values"

    id = Column(BigInteger, primary_key=True)
    parameter_id = Column(
        BigInteger, ForeignKey("family_parameters.id", ondelete="CASCADE"), nullable=False
    )
    value = Column(Text, nullable=False)
    value_norm = Column(Text, nullable=False)
    origin = Column(Text, nullable=False)
    # Слияние только внутри параметра — составной FK ниже.
    merged_into_id = Column(BigInteger, nullable=True)
    created_at = _created_at()

    parameter = relationship("FamilyParameter")

    __table_args__ = (
        UniqueConstraint(
            "parameter_id", "value_norm", name="uq_family_parameter_values_parameter_value_norm"
        ),
        # Цель составных FK значений варианта, значений контекста и слияния.
        UniqueConstraint("id", "parameter_id", name="uq_family_parameter_values_id_parameter"),
        ForeignKeyConstraint(
            ["merged_into_id", "parameter_id"],
            ["family_parameter_values.id", "family_parameter_values.parameter_id"],
            ondelete="RESTRICT", name="fk_family_parameter_values_merged_into",
        ),
        CheckConstraint(f"origin IN ({VALUE_ORIGINS})", name="ck_family_parameter_values_origin"),
        CheckConstraint(
            CK_PARAMETER_VALUE_NOT_BLANK, name="ck_family_parameter_values_value_not_blank"
        ),
        CheckConstraint(
            CK_PARAMETER_VALUE_NOT_SELF_MERGED, name="ck_family_parameter_values_not_self_merged"
        ),
    )


class WorkVariant(Base):
    """Вариант: семья, версия схемы и упорядоченный набор значений. Одинаковый
    набор — один вариант (`UNIQUE (schema_id, values_key)`, и архивный тоже)."""
    __tablename__ = "work_variants"

    id = Column(BigInteger, primary_key=True)
    family_id = Column(
        BigInteger, ForeignKey("work_families.id", ondelete="RESTRICT"), nullable=False
    )
    # Ссылка на версию схемы — составной отложенный FK в `__table_args__`.
    schema_id = Column(BigInteger, nullable=False)
    values_key = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    merged_into_id = Column(
        BigInteger, ForeignKey("work_variants.id", ondelete="RESTRICT"), nullable=True
    )
    created_at = _created_at()
    archived_at = Column(DateTime(timezone=True), nullable=True)

    family = relationship("WorkFamily")

    __table_args__ = (
        ForeignKeyConstraint(
            ["schema_id", "family_id"],
            ["family_parameter_schemas.id", "family_parameter_schemas.family_id"],
            ondelete="RESTRICT", deferrable=True, initially="DEFERRED",
            name="fk_work_variants_schema_family",
        ),
        UniqueConstraint("schema_id", "values_key", name="uq_work_variants_schema_values_key"),
        # Цели составных FK контекста и значений варианта.
        UniqueConstraint("id", "family_id", name="uq_work_variants_id_family"),
        UniqueConstraint("id", "schema_id", name="uq_work_variants_id_schema"),
        CheckConstraint(f"status IN ({VARIANT_STATUSES})", name="ck_work_variants_status"),
        CheckConstraint(CK_VARIANT_ARCHIVED_PAIR, name="ck_work_variants_archived_pair"),
        CheckConstraint(
            CK_VARIANT_MERGED_NEEDS_ARCHIVED, name="ck_work_variants_merged_needs_archived"
        ),
    )


class WorkVariantValue(Base):
    """Набор значений варианта построчно (для запросов поверхностей).
    `value_id IS NULL` — «не уточнено»; расхождение с `work_variants.values_key`
    — дефект сервиса."""
    __tablename__ = "work_variant_values"

    variant_id = Column(BigInteger, primary_key=True)
    schema_id = Column(BigInteger, nullable=False)
    parameter_id = Column(BigInteger, primary_key=True)
    value_id = Column(BigInteger, nullable=True)

    __table_args__ = (
        # Параметр — из схемы варианта; значение — этого параметра. При
        # `value_id IS NULL` FK значения не проверяется (`MATCH SIMPLE`).
        ForeignKeyConstraint(
            ["variant_id", "schema_id"], ["work_variants.id", "work_variants.schema_id"],
            ondelete="CASCADE", name="fk_work_variant_values_variant_schema",
        ),
        ForeignKeyConstraint(
            ["parameter_id", "schema_id"],
            ["family_parameters.id", "family_parameters.schema_id"],
            ondelete="RESTRICT", name="fk_work_variant_values_parameter_schema",
        ),
        ForeignKeyConstraint(
            ["value_id", "parameter_id"],
            ["family_parameter_values.id", "family_parameter_values.parameter_id"],
            ondelete="RESTRICT", name="fk_work_variant_values_value_parameter",
        ),
    )


class ContextParameterValue(Base):
    """Значения контекста по текущей версии схемы его семьи — текущее состояние,
    а не история (история — в событии `context_variant_assigned`)."""
    __tablename__ = "context_parameter_values"

    context_id = Column(
        BigInteger, ForeignKey("catalog_contexts.id", ondelete="RESTRICT"), primary_key=True
    )
    schema_id = Column(BigInteger, nullable=False)
    parameter_id = Column(BigInteger, primary_key=True)
    value_id = Column(BigInteger, nullable=True)
    source = Column(Text, nullable=False)
    job_id = Column(BigInteger, ForeignKey("semantic_jobs.id", ondelete="SET NULL"), nullable=True)
    created_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["parameter_id", "schema_id"],
            ["family_parameters.id", "family_parameters.schema_id"],
            ondelete="RESTRICT", name="fk_context_parameter_values_parameter_schema",
        ),
        ForeignKeyConstraint(
            ["value_id", "parameter_id"],
            ["family_parameter_values.id", "family_parameter_values.parameter_id"],
            ondelete="RESTRICT", name="fk_context_parameter_values_value_parameter",
        ),
        CheckConstraint(f"source IN ({VALUE_SOURCES})", name="ck_context_parameter_values_source"),
        CheckConstraint(
            CK_CONTEXT_VALUE_SOURCE_PAIR, name="ck_context_parameter_values_source_value_pair"
        ),
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
