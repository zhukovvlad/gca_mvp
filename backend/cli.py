"""CLI для управления пользователями.

Использование:
    python -m cli create-user --email admin@example.com --role admin
"""
import click

from config import settings
from crud.section_chains import backfill_chains
from database import SessionLocal
from db_guard import ensure_mutation_allowed
from models import SemanticReconcileBatch, User, UserRole
from security import hash_password
from services.catalog_backfill import etc_category_share, run_backfill
from services.family_change import AutoAcceptError, apply_auto_accept, preview_auto_accept
from services.releasable import enqueue_all_releasable
from services.semantic_decisions import backfill_family_schemas, enqueue_all
from services.simplify_backfill import (
    Step1Report,
    backfill_family_values,
    backfill_path_conflicts,
    families_for_backfill,
)
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


@cli.command("backfill-contexts")
def backfill_contexts() -> None:
    """Разовый проход по существующему каталогу: маршрутизировать все ещё
    не обработанные строки сметы в контексты (`services/catalog_backfill.
    run_backfill`, план фичи «Семьи и контексты», задача 11).

    Идемпотентно: повторный запуск на неизменных данных не создаёт ни одной
    строки и не меняет ни одного поля. Порядок запуска — миграция → seed
    (`seed-work-families`) → этот проход (спека §2.9): семьи контекстам не
    назначаются автоматически, проход от seed не зависит, но обратный
    порядок запутал бы отчёты.
    """
    _guard("backfill-contexts")
    db = SessionLocal()
    try:
        report = run_backfill(db)
        etc_count, etc_total = etc_category_share(db)
        etc_pct = (etc_count / etc_total * 100) if etc_total else 0
        click.echo(
            f"Корзин создано={report.buckets_created}, "
            f"контекстов создано={report.contexts_created}, "
            f"членств создано={report.members_created}, "
            f"строк без корзины={report.rows_without_bucket}, "
            f"изменено полей при пересчёте словаря={report.fields_changed}"
        )
        click.echo(f"Контексты по роли имени: {report.by_name_role}")
        click.echo(f"Контексты по виду: {report.by_semantic_kind}")
        click.echo(
            f"Членств в корзинах статьи «Прочее»: {etc_count} из {etc_total} "
            f"({etc_pct:.1f}%)"
        )
    finally:
        db.close()


@cli.command("semantic-enqueue-all")
def semantic_enqueue_all() -> None:
    """Массовая постановка семантических предложений: набор по всем неархивным
    контекстам записывается удержанной пачкой (`services/semantic_decisions.
    enqueue_all`), даже под потолком события. Задания не ставятся — поставить
    пачку можно только с экрана `admin` после preview (спека §2.10)."""
    _guard("semantic-enqueue-all")
    db = SessionLocal()
    try:
        batch_id = enqueue_all(db)
        if batch_id is None:
            click.echo("Ставить нечего: у всех контекстов уже есть задания текущего отпечатка")
            return
        batch = db.get(SemanticReconcileBatch, batch_id)
        db.commit()
        click.echo(
            f"Удержана пачка №{batch_id}: контекстов={batch.contexts_count}, "
            f"резерв=${batch.reserve_estimate_usd:.4f}"
        )
    finally:
        db.close()


def _threshold_label(threshold) -> str:
    return "не задан" if threshold is None else str(threshold)


@cli.command("semantic-auto-accept")
@click.option("--yes", is_flag=True, help="Не спрашивать подтверждения (развёртывание без терминала)")
def semantic_auto_accept(yes: bool) -> None:
    """Массовое автопринятие опубликованных предложений по порогу
    `SEMANTIC_AUTO_ACCEPT_THRESHOLD` (порог систем -
    `SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD`; `services/family_change.apply_auto_accept`,
    спека §2.12): показ числа решений по исходам таблицы публикации,
    подтверждение, применение с хэшем показа одной транзакцией. Состояние
    изменилось между показом и применением - ничего не применено."""
    _guard("semantic-auto-accept")
    db = SessionLocal()
    try:
        try:
            preview = preview_auto_accept(db)
        except AutoAcceptError as exc:
            click.echo(f"Отказ: {exc}", err=True)
            raise click.exceptions.Exit(1) from exc
        db.rollback()
        click.echo(f"Порог работ: {_threshold_label(preview.threshold)}")
        click.echo(f"Порог систем: {_threshold_label(preview.system_threshold)}")
        for outcome, count in preview.by_outcome.items():
            click.echo(f"  {outcome}: {count}")
        click.echo(f"Кандидатов всего: {preview.total}")
        if not any(count for outcome, count in preview.by_outcome.items() if outcome != "none"):
            click.echo("Применять нечего")
            return
        if not yes:
            click.confirm("Применить?", abort=True)
        try:
            applied = apply_auto_accept(db, preview_hash=preview.preview_hash)
            db.commit()
        except AutoAcceptError as exc:
            click.echo(f"Отказ: {exc}", err=True)
            raise click.exceptions.Exit(1) from exc
        click.echo("Применено: " + ", ".join(f"{k}={v}" for k, v in applied.items()))
    finally:
        db.close()


@cli.command("semantic-schemas-backfill")
def semantic_schemas_backfill() -> None:
    """Постановка схем активным семьям без текущей или с «не построенной» версии
    (при наличии наименований) одной пачкой
    `mass` (`services/semantic_decisions.backfill_family_schemas`); сверх
    потолка события пачка удерживается и ставится с экрана `admin`."""
    _guard("semantic-schemas-backfill")
    db = SessionLocal()
    try:
        report = backfill_family_schemas(db)
        db.commit()
        if report.held_batch_id is not None:
            click.echo(f"Удержана пачка №{report.held_batch_id}")
        click.echo(f"Заданий схем поставлено={report.created}, возвращено={report.revived}")
    finally:
        db.close()


@cli.command("zones-backfill")
@click.option(
    "--enqueue", is_flag=True,
    help="Поставить задания зоны цепочкам без зоны, без предложения и без задания",
)
@click.option(
    "--reapply-rule", is_flag=True,
    help="Пересчитать правилом цепочки с источником «правило» и нерешённые с предложением модели",
)
def zones_backfill(enqueue: bool, reapply_rule: bool) -> None:
    """Цепочки разделов и зона для уже загруженных смет (`crud/section_chains.
    backfill_chains`, спека лестницы аналогов §2.19): цепочки для строк без
    `section_chain_id`, правило для цепочек без зоны и без предложения; с
    `--reapply-rule` — пересчёт цепочек с источником `rule` и зона правилом у нерешённых
    цепочек с предложением модели (предложение снимается); с `--enqueue` — задания
    `section_zone`. Счёт и оценка резерва печатаются ДО постановки заданий.

    Идемпотентна: повторный запуск на неизменных данных ничего не создаёт и не ставит."""
    _guard("zones-backfill")
    db = SessionLocal()
    try:
        def _announce(count, estimate) -> None:
            click.echo(
                f"К постановке заданий зоны: {count}, оценка резерва: ${estimate:.4f}"
            )

        report = backfill_chains(
            db, enqueue=enqueue, reapply_rule=reapply_rule, before_enqueue=_announce
        )
        click.echo(
            f"Цепочек создано={report.chains_created}, строк привязано={report.rows_assigned}, "
            f"зон по правилу={report.rule_set}, зон пересчитано правилом={report.rule_changed}"
        )
        if enqueue:
            click.echo(f"Заданий зоны поставлено={report.enqueued}")
    finally:
        db.close()


@cli.command("releasable-enqueue-all")
def releasable_enqueue_all() -> None:
    """Задания «можно отпустить» (`services/releasable.enqueue_all_releasable`, спека
    лестницы аналогов §2.7) для всех действующих версий схем, где есть неразмеченный
    активный главный параметр и нет задания с текущим отпечатком. Счёт и оценка
    резерва печатаются ДО постановки. Идемпотентна: повторный запуск на неизменных
    данных ничего не ставит."""
    _guard("releasable-enqueue-all")
    db = SessionLocal()
    try:
        def _announce(count, estimate) -> None:
            click.echo(f"К постановке заданий: {count}, оценка резерва: ${estimate:.4f}")

        report = enqueue_all_releasable(db, before_enqueue=_announce)
        click.echo(f"Заданий поставлено={report.enqueued}")
    finally:
        db.close()


def _ids_line(label: str, ids) -> str:
    return f"{label}=" + (",".join(str(i) for i in ids) if ids else "-")


@cli.command("catalog-simplify-backfill")
@click.option("--dry-run", is_flag=True, help="Ничего не писать, напечатать, что было бы сделано")
@click.option("--family", "family_id", type=int, default=None, help="Только эта семья (оба шага)")
@click.option(
    "--limit", type=click.IntRange(min=0), default=None,
    help="Не больше N контекстов «к делению» в шаге 2",
)
def catalog_simplify_backfill(dry_run: bool, family_id: int | None, limit: int | None) -> None:
    """Первый прогон упрощения каталога на накопленных данных (`services/simplify_backfill`,
    спека упрощения каталога §2.12). Шаг 1: склейки в списках значений становятся составными
    на месте, совпавшие по составу сливаются, состав под вето и склейки с частью не из списка
    остаются целыми. Шаг 2: контексты «к делению» получают задание значений заново (промпт v2);
    резерв стоимости печатается по тарифам `context_values`. Платные вызовы — только по явному
    запуску: без `--dry-run` задания ставятся, и их выполняет обычная очередь.

    Печать — только латиница, числа и id. Идемпотентна: повторный запуск шаг 1 не меняет, а
    задания, уже ждущие или идущие, не дублирует."""
    _guard("catalog-simplify-backfill")
    db = SessionLocal()
    try:
        step1 = Step1Report()
        for family in families_for_backfill(db, family_id):
            step1.extend(backfill_family_values(db, family, apply=not dry_run))
            if dry_run:
                db.rollback()
            else:
                db.commit()
        step2 = backfill_path_conflicts(db, family_id=family_id, limit=limit, apply=not dry_run)
        if dry_run:
            db.rollback()
        else:
            db.commit()
        click.echo(f"dry_run={int(dry_run)}")
        click.echo(
            f"step1 converted={len(step1.converted)} "
            f"merged_into_composite={len(step1.merged_into_composite)} "
            f"collapsed={len(step1.collapsed)} under_veto={len(step1.under_veto)} "
            f"kept_whole={len(step1.kept_whole)} text_kept={len(step1.text_kept)} "
            f"skipped_families={len(step1.skipped_families)}"
        )
        click.echo(
            f"step1 jobs_queued={step1.jobs_queued} reserve_usd={step1.reserve_usd:.4f}"
        )
        for label, ids in (
            ("converted_ids", step1.converted),
            ("merged_into_composite_ids", step1.merged_into_composite),
            ("collapsed_ids", step1.collapsed),
            ("under_veto_ids", step1.under_veto),
            ("kept_whole_ids", step1.kept_whole),
            ("text_kept_ids", step1.text_kept),
        ):
            click.echo(_ids_line(f"step1 {label}", ids))
        click.echo(
            f"step2 candidates={step2.candidates} already_asked={step2.already_asked} queued={len(step2.queued)} "
            f"jobs_created={step2.jobs_created} jobs_revived={step2.jobs_revived} "
            f"without_model={step2.free}"
        )
        click.echo(_ids_line("step2 queued_context_ids", step2.queued))
        click.echo(f"step2 reserve_usd={step2.reserve_usd:.4f} cached_usd={step2.cached_usd:.4f}")
        click.echo(_ids_line("step2 auto_split_queued", step2.auto_split_queued))
        click.echo(f"step2 auto_split_reserve_usd={step2.auto_split_reserve_usd:.4f}")
        total = step1.reserve_usd + step2.reserve_usd + step2.auto_split_reserve_usd
        click.echo(f"total reserve_usd={total:.4f}")
    finally:
        db.close()


if __name__ == "__main__":
    cli()
