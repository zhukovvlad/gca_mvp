import enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
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
