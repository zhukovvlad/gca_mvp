# УПД Трекер цен — Инструкция

**Дата:** 2026-05-06
**Статус:** Реализован

---

## Запуск

### Локально (разработка)

```bash
# Бэкенд
cd backend
pip install -r requirements.txt
cp .env.example .env  # заполнить ANTHROPIC_API_KEY
uvicorn main:app --reload --port 8000

# Фронтенд
cd frontend
npm install
npm run dev
```

Фронтенд: http://localhost:5173
Бэкенд (Swagger): http://localhost:8000/docs

### Docker (продакшн)

```bash
cp backend/.env.example backend/.env  # заполнить ключ
docker-compose up --build
```

Приложение: http://localhost:8080

---

## Стек

| Слой | Технология |
|------|-----------|
| Бэкенд | Python 3.11+, FastAPI, SQLAlchemy, SQLite |
| Фронтенд | TypeScript, React 18, Vite, shadcn/ui, Tailwind CSS v4, Recharts |
| PDF-парсинг | Claude API (vision) + pdf2image |
| Выгрузка | openpyxl (Excel) |
| Деплой | Docker Compose (backend + nginx) |

---

## Структура проекта

```
UDP/
├── backend/
│   ├── main.py              — FastAPI + dotenv + CORS
│   ├── database.py          — SQLAlchemy подключение (SQLite)
│   ├── models.py            — ORM: Supplier, Material, Invoice, InvoiceItem, PriceStat
│   ├── crud.py              — CRUD + recalculate_price_stats
│   ├── pdf_parser.py        — парсер УПД через Claude Vision API
│   ├── routers/
│   │   ├── invoices.py      — загрузка PDF, отдача PDF, CRUD УПД
│   │   ├── dashboard.py     — KPI, динамика цен, фильтры
│   │   ├── export.py        — выгрузка Excel
│   │   ├── settings.py      — API-ключ, модель, порог
│   │   ├── suppliers.py     — CRUD поставщиков
│   │   └── materials.py     — CRUD + объединение материалов
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx  — графики, KPI, таблица
│   │   │   ├── Upload.tsx     — drag-and-drop, авто/ручной режим
│   │   │   ├── Review.tsx     — PDF-просмотр + редактирование
│   │   │   ├── Suppliers.tsx  — список + добавление/редактирование
│   │   │   ├── Materials.tsx  — справочник + объединение дублей
│   │   │   ├── Reports.tsx    — скачивание Excel
│   │   │   └── Settings.tsx   — API-ключ, модель, порог
│   │   ├── components/ui/     — shadcn/ui компоненты
│   │   ├── lib/api.ts         — axios instance
│   │   ├── App.tsx            — навигация + роутинг
│   │   └── main.tsx
│   ├── nginx.conf
│   ├── Dockerfile
│   ├── vite.config.ts
│   └── package.json
├── docker-compose.yml
└── .gitignore
```

---

## Требования

- Python 3.11+
- Node.js 20+
- Poppler (`apt install poppler-utils` или в Docker)
- API-ключ Anthropic (вводится через Settings или .env)
