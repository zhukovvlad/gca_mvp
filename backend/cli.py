"""CLI для управления пользователями.

Использование:
    python -m cli create-user --email admin@example.com --role admin
"""
import click

from config import settings
from database import SessionLocal
from db_guard import ensure_mutation_allowed
from models import User, UserRole
from security import hash_password
from services.work_families import load_seed


@click.group()
def cli():
    """База расценок ГП — инструменты администрирования."""
    pass


def _guard(action: str) -> None:
    """Отказаться мутировать БД, если цель не разрешена для текущего APP_ENV."""
    ensure_mutation_allowed(settings.DATABASE_URL, f"cli {action}")


@cli.command("create-user")
@click.option("--email", required=True, help="Email пользователя")
@click.option("--role", default="member", type=click.Choice(["admin", "member"]))
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Пароль (вводится интерактивно)",
)
def create_user(email: str, role: str, password: str) -> None:
    """Создать пользователя."""
    _guard("create-user")
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            click.echo(f"Пользователь {email} уже существует", err=True)
            return
        user = User(
            email=email,
            password_hash=hash_password(password),
            role=UserRole(role),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        click.echo(f"Пользователь создан: id={user.id} email={user.email} role={role}")
    finally:
        db.close()


@cli.command("seed-work-families")
def seed_work_families() -> None:
    """Загрузить 42 начальные семьи работ из `backend/seeds/work_families_initial.json`.

    Идемпотентно: повторный запуск не создаёт дублей и не трогает правок
    пользователя (`services/work_families.load_seed`, план фичи «Семьи и
    контексты», задача 7).
    """
    _guard("seed-work-families")
    db = SessionLocal()
    try:
        report = load_seed(db)
        db.commit()
        click.echo(
            f"Семьи работ: создано={report.created}, "
            f"пропущено (уже существуют)={report.skipped_existing}, "
            f"с определением в файле={report.with_definition}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    cli()
