# Neon Postgres setup для UDP

> **Neon — один из вариантов, а не «база проекта».** Развёртывание возможно на локальном
> Postgres, в Docker или на managed-хостинге; дев-цикл по умолчанию идёт на локальном
> кластере (`docs/testing.md`, раздел «Локальная dev-БД»). Этот документ нужен, только если
> вы выбрали Neon.

## 1. Регистрация и проект

1. Открыть https://neon.tech и зарегистрироваться (Google/GitHub быстрее).
2. После логина создать новый проект:
   - **Project name:** `udp`
   - **Postgres version:** 16
   - **Region:** ближайший (например, `Frankfurt` или `Stockholm`).
3. Neon создаст дефолтную базу `neondb` и owner-пользователя — для dev этого достаточно, отдельные user/БД не создаём.
4. Auth-фичу (Stack Auth) на этом шаге **не включаем** — её обсудим отдельно, когда дойдём до авторизации в приложении.

## 2. Получить connection string

В Neon Console → проект → Dashboard → Connection details:
- Скопировать **Connection string** (поле «Direct connection», НЕ pooled — для backend с долгоживущими коннектами лучше прямое соединение).

Формат:
```
postgresql://<owner>:<password>@ep-<adjective>-<noun>-<hash>.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

Преобразовать схему URL для psycopg 3 (добавить `+psycopg`):
```
postgresql+psycopg://<owner>:<password>@ep-<adjective>-<noun>-<hash>.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

## 3. Сохранить в `backend/.env`

В `backend/.env` (от корня репо, не коммитится) заменить строку `DATABASE_URL`
(по умолчанию — DSN локального кластера, см. `backend/.env.example`) на
полученную выше:

```
DATABASE_URL=postgresql+psycopg://<owner>:<password>@ep-<adjective>-<noun>-<hash>.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

## 4. Включить pgvector

В Neon Console → SQL Editor (БД `neondb` выбрана по умолчанию):

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

То же самое сделает миграция Alembic в Task 5 плана; здесь руками — чтобы сразу убедиться, что расширение доступно в вашем Neon-проекте.

## Заметки

- **Auto-suspend:** на free tier Neon усыпляет compute после 5 минут неактивности. Первый запрос после паузы — холодный старт ~1–2 сек. Для dev нормально.
- **Лимиты free tier:** 0.5 GB storage, 191 compute hour/мес. Для проекта с парой тысяч документов — с большим запасом.
- **Backups:** Neon делает point-in-time restore (7 дней на free) автоматически, ничего настраивать не нужно.
- **Hardening для прода (на будущее):** перед выкаткой на VPS создадим отдельного app-пользователя с ограниченными правами (`CREATE USER udp_app ...; GRANT CONNECT ON DATABASE ...; GRANT USAGE ON SCHEMA public ...; GRANT SELECT/INSERT/UPDATE/DELETE ON ALL TABLES ...`) и переключим backend на него. На dev owner-доступ оправдан простотой.
