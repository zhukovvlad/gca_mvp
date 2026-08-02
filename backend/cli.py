"""CLI для управления пользователями и организациями.

Использование:
    python -m cli create-superuser --email admin@example.com
    python -m cli create-org --name "Стройтрест" --inn 7701234567
"""
import click

from config import settings
from database import SessionLocal
from db_guard import ensure_mutation_allowed
from models import Organization, OrgRole, User
from security import hash_password


@click.group()
def cli():
    """УПД Трекер — инструменты администрирования."""
    pass


def _guard(action: str) -> None:
    """Отказаться мутировать БД, если цель не разрешена для текущего APP_ENV."""
    ensure_mutation_allowed(settings.DATABASE_URL, f"cli {action}")


@cli.command("create-superuser")
@click.option("--email", required=True, help="Email суперюзера")
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Пароль (вводится интерактивно)",
)
def create_superuser(email: str, password: str) -> None:
    """Создать суперюзера системы (без привязки к организации)."""
    _guard("create-superuser")
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            click.echo(f"Пользователь {email} уже существует", err=True)
            return
        user = User(
            email=email,
            password_hash=hash_password(password),
            is_superuser=True,
            org_id=None,
            org_role=None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        click.echo(f"Суперюзер создан: id={user.id} email={user.email}")
    finally:
        db.close()


@cli.command("create-org")
@click.option("--name", required=True, help="Название организации")
@click.option("--inn", default=None, help="ИНН организации (необязательно)")
def create_org(name: str, inn: str | None) -> None:
    """Создать организацию."""
    _guard("create-org")
    db = SessionLocal()
    try:
        org = Organization(name=name, inn=inn or None)
        db.add(org)
        db.commit()
        db.refresh(org)
        click.echo(f"Организация создана: id={org.id} name={org.name}")
    finally:
        db.close()


@cli.command("create-user")
@click.option("--email", required=True, help="Email пользователя")
@click.option("--org-id", required=True, type=int, help="ID организации")
@click.option("--role", default="member", type=click.Choice(["superadmin", "admin", "member"]))
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
)
def create_user(email: str, org_id: int, role: str, password: str) -> None:
    """Создать обычного пользователя в организации."""
    _guard("create-user")
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org:
            click.echo(f"Организация id={org_id} не найдена", err=True)
            return
        if db.query(User).filter(User.email == email).first():
            click.echo(f"Пользователь {email} уже существует", err=True)
            return
        user = User(
            email=email,
            password_hash=hash_password(password),
            is_superuser=False,
            org_id=org_id,
            org_role=OrgRole(role),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        click.echo(f"Пользователь создан: id={user.id} email={user.email} org_id={org_id} role={role}")
    finally:
        db.close()


if __name__ == "__main__":
    cli()
