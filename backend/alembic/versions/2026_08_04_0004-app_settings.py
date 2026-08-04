"""Таблица настроек приложения: топ-N ключевых расценок паспорта.

`AGENTS.md` §7.4 требует хранить N **в БД**, а не в конфиге приложения: «N хранится
в БД (таблица настроек, экран Settings из udp-tenders), не в конфиге приложения».
`routers/settings.py` источника непереносим — он писал значения в `.env` через
`set_key`, то есть у настройки не было ни аудита, ни истории, а при нескольких
развёртываниях она расходилась.

**Форма таблицы — решение §6.2 фазы 6** (обоснование целиком —
`docs/phase6-analytics.md` §1.2): одна строка с **типизированными** колонками, а не
«ключ→значение». Коротко: у «ключ→значение» нет типов, и БД спокойно хранила бы
`passport_top_n = 'абв'` — ошибка всплыла бы при отрисовке паспорта, перед
человеком, который его печатает. Здесь это состояние непредставимо, ровно как
неположительная ставка норматива или пересекающиеся периоды в 0002.

**Singleton через `CHECK (id = 1)`.** Вторая строка настроек непредставима, поэтому
читающему коду не приходится выбирать между строками — а значит, не приходится и
ошибаться в выборе.

**Верхняя граница `passport_top_n` — это требование DoD, а не вкус.** §10 требует:
«Паспорт печатается на одну страницу А4 со всеми полями». Ключевые расценки по §7.4
— это и есть топ-N, значит страница полна, когда на ней топ-N, и правильный ответ —
не дать N вырасти настолько, чтобы топ перестал влезать. Граница 20 взята с запасом
к расчёту раскладки: при полях 10 мм на строки остаётся ~206 мм, а строка с
наименованием, зажатым в две печатные строки 9 pt, занимает ~8,6 мм — то есть
худший случай упирается около 24. Запас нужен именно потому, что худший случай
реален: в наименование работы попадает спецификация на километры (§11 AGENTS.md,
реальный файл — 5077 символов).

Downgrade — обычный `DROP TABLE`: настройка ничего не хранит, кроме себя, и на неё
не ссылается ни один FK. Круговой рейс `downgrade base` → `upgrade head` проверен
(см. отчёт фазы 6), а не объявлен: §7 брифинга требует именно проверки.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-04
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Литералы дублируют models.PASSPORT_TOP_N_* намеренно: миграция обязана быть
# неизменной во времени, поэтому значения зафиксированы здесь, а не импортированы
# (то же правило, что у списков статусов в 0002). Расхождение ловит
# test_schema_constraints.py::TestAppSettings.
PASSPORT_TOP_N_DEFAULT = 15
PASSPORT_TOP_N_MIN = 1
PASSPORT_TOP_N_MAX = 20


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.SmallInteger(), primary_key=True, server_default=sa.text("1")),
        sa.Column(
            "passport_top_n",
            sa.Integer(),
            nullable=False,
            server_default=sa.text(str(PASSPORT_TOP_N_DEFAULT)),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.CheckConstraint("id = 1", name="ck_app_settings_singleton"),
        sa.CheckConstraint(
            f"passport_top_n BETWEEN {PASSPORT_TOP_N_MIN} AND {PASSPORT_TOP_N_MAX}",
            name="ck_app_settings_passport_top_n",
        ),
    )
    # Сев строки настроек. `ON CONFLICT DO NOTHING` — чтобы миграция оставалась
    # идемпотентной при повторном накате на базу, где строка уже есть (например
    # после `downgrade`/`upgrade` без пересоздания схемы).
    op.execute("INSERT INTO app_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING")


def downgrade() -> None:
    op.drop_table("app_settings")
