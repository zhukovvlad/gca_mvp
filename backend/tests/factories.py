"""factory_boy фабрики для интеграционных тестов.

Использование: `user = UserFactory.create()` в тесте, который имеет
фикстуру `db_session`. Фабрики привязываются к session через `_register_session`.
"""
import factory
from factory.alchemy import SQLAlchemyModelFactory

from models import UnitOfMeasure, User, UserRole
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
