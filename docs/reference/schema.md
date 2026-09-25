**Когда читать:** сверяешься с устройством схемы БД: таблицы и FK, идентичность входного написания в каталоге, семантический контур (семьи и контексты), нормативы, служебные поля импорта.

## 1. Доменный контур

```
rate_classes                 # классы объектов (справочник)

objects                      # из tenders-go + rate_class_id bigint NULL → rate_classes
                             #   (значение по умолчанию для новых договоров)
contractors                  # из tenders-go

contracts                    # договор ГП
  id, object_id NOT NULL → objects
  contractor_id NOT NULL → contractors
  rate_class_id NOT NULL → rate_classes    # СНИМОК класса на момент создания договора;
                                           # переклассификация объекта не меняет прошлое
  contract_number, title, signer, signed_date date NOT NULL, total_amount numeric, notes
  # signed_date обязателен: это фолбэк даты сравнения с нормативом (AGENTS.md §4), обе даты
  # не могут быть NULL одновременно
  UNIQUE (contract_number)

estimates                    # смета (бывш. tenders); 1 договор : N смет
  id, contract_id NULL → contracts   # с 0015 (тендерный контур) — один из трёх
                                     #   владельцев, см. блок ниже: contract_id | offer_id | round_id
  amendment_no int NULL      # NULL = исходная смета, иначе номер доп. соглашения; только у contract_id
  title, data_prepared_on_date date
  import_job_id → import_jobs
  UNIQUE NULLS NOT DISTINCT (contract_id, amendment_no)   # PG16

tenders / tender_rounds / offer_packages / offers    # тендерный контур (спека 2026-08-26)
  # offers — ячейка решётки «раунд × участник»; составные FK с продублированным
  # tender_id держат раунд и участника в одном тендере структурно.
estimates: contract_id NULL | offer_id NULL | round_id NULL — РОВНО ОДИН (CHECK);
  # round_id на смете = baseline раунда. uq_estimates_contract_amendment — частичный.
proposals.contractor_id NULL у baseline (CHECK против is_baseline).
position_items.deviation_from_baseline_cost: у смет договора NULL; у offer-смет — из файла (§2.10).
estimate_raw_data — ПРОЕКЦИЯ разобранного JSON под смету; точный результат разбора файла —
  import_jobs.parsed_data + parser_version (три факта об одном файле, спека §2.3).
contractors.inn — канон, только ASCII-цифры (CHECK); одна canonicalize_inn().

estimate_raw_data
  estimate_id PK → estimates ON DELETE CASCADE
  raw_data jsonb NOT NULL    # проекция parsed_data под смету; точный результат разбора —
                             #   import_jobs.parsed_data + parser_version (см. выше)
  parser_version text NOT NULL
  created_at

lots / proposals / position_items
  # перенос из tenders-go. proposals — РОВНО ОДНО на лот (единственный подрядчик);
  # слой сохраняем: JSON парсера ложится 1:1, на proposal висят summary_lines и
  # additional_info. is_baseline — с 0015 опорное поле (не задел): у baseline-
  # proposal true и contractor_id NULL, у offer-proposal — наоборот
  # (CHECK ck_proposals_baseline_contractor), и тот же флаг ветвит импорт раунда.
  # position_items: quantity, suggested_quantity,
  #   unit_cost_{materials,works,indirect_costs,total}, total_cost_{...},
  #   is_chapter, chapter_ref_in_proposal, catalog_position_id NULL, unit_id,
  #   комментарии. deviation_from_baseline_cost — NULL у смет договора; у offer-смет — из файла.
```

## 2. Каталожный контур

```
catalog_positions
  standard_job_title text NOT NULL        # отображаемое название (человекочитаемое)
  normalized_job_title text NOT NULL      # НОВАЯ колонка: normalize(standard_job_title),
                                          # вычисляется в Python при insert/update
  unit_id NULL → units_of_measurement
  kind ('POSITION|HEADER|LOT_HEADER|TRASH|TO_REVIEW'), status,
  embedding vector(768) NULL, FTS по standard_job_title (из миграции 000002)
  # Нормализованная пара (normalized_job_title, unit_id) — с этой ревизии
  # идентичность ВХОДНОГО НАПИСАНИЯ и ключ каскада матчинга, а не идентичность
  # работы: она остаётся техническим носителем существующих матрицы и
  # нормативов (kind='POSITION') до фичи вариантов работ — идентичностью
  # СРАВНИМОЙ РАБОТЫ станет `work_variant.id`, но это принятое НАПРАВЛЕНИЕ, а
  # не факт этой ревизии, и таблица `work_variant` этой фичей не заводится.
  # Группировка вариантов одной работы идёт через контексты и семьи (раздел
  # «Семантический контур» ниже), а не через эту пару напрямую.
  # УНИКАЛЬНОСТЬ — по нормализованной паре, но в индексе от названия лежит
  # sha256 (миграция 0003, уточнение v6.3):
  #   UNIQUE (sha256(replace(normalized_job_title,'\','\\')::bytea),
  #           COALESCE(unit_id, -1))                                -- raw SQL
  # Причина: btree не индексирует значения длиннее 2704 байт, а в наименование
  # сметы попадают спецификации на килобайты (реальный файл — 5077 символов).
  # Идентичность написания по хэшу не изменилась: сравнение идёт по полному
  # тексту пары, каждая выборка по хэшу дополнена проверкой самой пары,
  # коллизия — явный отказ.
  # Обрезать название нельзя: две спецификации с общим началом — разные работы.
  # Обычного индекса по standard_job_title НЕТ: поиск в UI — ILIKE '%…%', его
  # btree не обслуживает (нужен был бы GIN pg_trgm, вне MVP).
  # Частичные индексы (WHERE kind = 'POSITION' и т.п.) — raw SQL через op.execute().
  # Матчинг и get-or-create TO_REVIEW работают по ТОЙ ЖЕ нормализованной паре,
  # что и matcher, — никаких вторых представлений строки.

matching_cache
  cache_key text PK           # sha256(norm_version || '|' || normalized_title || '|' || unit_norm)
                              # unit_norm = каноническое имя единицы после разрешения алиасов,
                              # '' если единицы нет. ЕДИНИЦА ОБЯЗАТЕЛЬНА В КЛЮЧЕ.
  norm_version smallint NOT NULL
  job_title_text text NOT NULL           # исходное название — нужно для перевыпуска ключей
  unit_text text                          # исходная единица — то же
  catalog_position_id NOT NULL → catalog_positions
  source text NOT NULL CHECK (source IN ('auto','manual'))
  expires_at timestamptz NULL
  # ПРАВИЛО TTL: source='auto' → expires_at = now()+30 дней, продлевается при hit;
  #             source='manual' (решение из Review) → expires_at = NULL, НЕ истекает.
  # Инкремент norm_version: миграция обязана перевыпустить ключи всех source='manual'
  # записей из (job_title_text, unit_text) новой нормализацией; 'auto' можно отбросить.

units_of_measurement          # слить с UnitOfMeasure + UnitAlias из udp-tenders;
                              # алиасы «м2»/«кв.м»/«м²» обязательны
```

## 3. Семантический контур

Шесть таблиц (`docs/superpowers/specs/2026-09-22-catalog-families-design.md`
§2.3): семьи работ, идемпотентная точка входа «строка × эффективная статья»,
устойчивый контекст с принятым семантическим решением, правила маршрутизации
корзины, явное членство позиции и журнал с ровно одним предметом.

```
work_families                 # семья: тип работы, объединяющий сравнимые варианты
  id, seed_key text NULL      # ключ строки seed; у ручных семей NULL
  title text NOT NULL, unit_id NULL → units_of_measurement
  definition text NULL        # обязательно для активации (CHECK)
  status ('draft|active|archived')
  created_by NULL → users     # NULL = заведено seed-командой (CHECK: автор ⟺ не seed)
  activated_by, activated_at, archived_at
  # UNIQUE (seed_key) — NULL-ы различны, ручные семьи не сталкиваются
  # UNIQUE (lower(btrim(title)), COALESCE(unit_id,-1)) WHERE status='active' — raw SQL;
  #   имя семьи не ключ, но две АКТИВНЫЕ семьи с одним именем и единицей — ошибка оператора

context_buckets                # решений не несёт, только ключ группировки
  id, catalog_position_id NOT NULL → catalog_positions
  work_category_id NULL → work_categories
  # UNIQUE (catalog_position_id, COALESCE(work_category_id,-1)) — raw SQL;
  #   отсутствие статьи — своя корзина, одна на строку

catalog_contexts               # устойчивая группа с семантическим решением
  id, bucket_id NOT NULL → context_buckets, is_default boolean NOT NULL DEFAULT false
  work_family_id NULL → work_families, family_source ('manual|suggestion') NULL,
    family_by NULL → users, family_at NULL
    # происхождение семьи — ОДИН тотальный предикат из двух полных ветвей
    # (не пара равносильностей, `docs/pitfalls/db.md`)
  semantic_kind ('WORK|SYSTEM|UNKNOWN'), semantic_kind_source ('rule|manual'),
    semantic_kind_by NULL → users, semantic_kind_at NOT NULL
  name_role ('WORK|LOCATION_ONLY|GENERIC_WORK'), name_role_source ('rule|manual'),
    name_role_by NULL → users, name_role_at NOT NULL, place_dictionary_version smallint NOT NULL
  semantic_state ('SUGGESTED|CONFIRMED|NOT_APPLICABLE')   # РОВНО три значения
  comparability_reason ('insufficient_description') NULL, archived_at NULL
  # UNIQUE (bucket_id, id) — цель составных FK ниже
  # UNIQUE (bucket_id) WHERE is_default AND archived_at IS NULL — raw SQL;
  #   архивный контекст по умолчанию сосуществует с действующим

context_routing_rules          # упорядоченные правила корзины
  id, bucket_id NOT NULL → context_buckets ON DELETE CASCADE, ordinal int NOT NULL
  predicate jsonb NOT NULL, context_id bigint NOT NULL, created_by NOT NULL → users
  # составной FK (bucket_id, context_id) → catalog_contexts (bucket_id, id):
  #   правило не может указать на контекст чужой корзины
  # UNIQUE (bucket_id, ordinal)

context_members                # явное членство: позиция → контекст, ровно одно
  position_item_id PK → position_items ON DELETE CASCADE
  context_id, bucket_id (дубль под составной FK)
  membership_state ('CURRENT|STALE'), routed_by ('default|rule|manual')
  routing_rule_id NULL → context_routing_rules ON DELETE SET NULL
  conflict_at NULL, conflict_from_context_id NULL → catalog_contexts    # своя ось, не membership_state
  # составной FK (bucket_id, context_id) → catalog_contexts (bucket_id, id) ON DELETE RESTRICT
  # Index(context_id); частичные Index(context_id) WHERE membership_state='STALE'
  #   и WHERE conflict_at IS NOT NULL

semantic_events                 # журнал; предмет РОВНО ОДИН, состав закрыт
  id, context_id NULL → catalog_contexts, family_id NULL → work_families
  event_type text NOT NULL       # закрытый список из пятнадцати типов
  payload jsonb NOT NULL, actor_id NULL → users
  # CHECK num_nonnulls(context_id, family_id) = 1
  # CHECK предмет по ТИПУ — явное множество типов, а не префикс `family_%`
  # CHECK payload непуст
```

`ON DELETE RESTRICT` трижды подряд одной цепочкой
(`context_members.context_id` → `catalog_contexts.bucket_id` →
`context_buckets.catalog_position_id`) — снести принятое семантическое решение
мимоходом нельзя, слияние в Review обязано разобрать каждое звено явно.
`context_members.position_item_id` уходит `CASCADE` вместе со сметой; контекст
при этом не удаляется и не архивируется.

## 4. Нормативы

```
rate_standards
  id, catalog_position_id NOT NULL, rate_class_id NOT NULL
  standard_unit_rate numeric NOT NULL CHECK (standard_unit_rate > 0)
  valid_from date NOT NULL, valid_to date NULL      # период = [valid_from, valid_to)
  CHECK (valid_to IS NULL OR valid_to > valid_from)
  inflation_index numeric NULL, approved_by, approved_at, note
  # Запрет пересечений (btree_gist):
  #   EXCLUDE USING gist (catalog_position_id WITH =, rate_class_id WITH =,
  #                       daterange(valid_from, valid_to, '[)') WITH &&)
  #   NULL в valid_to даёт бесконечную верхнюю границу — COALESCE не нужен.
  # Переутверждение = UPDATE valid_to старой строки + INSERT новой. Историю не мутировать.
```

## 5. Служебное

```
import_jobs
  id, contract_id NULL, round_id NULL → tender_rounds   # с 0015 — два владельца
                                                         #   (CHECK: ровно один из двух)
  amendment_no int NULL      # только у contract_id; у round_id — NULL (CHECK)
  filename, file_key, file_sha256 text NOT NULL,
  status ('pending|parsing|importing|matching|done|error'), error_text,
  warnings jsonb NOT NULL DEFAULT '[]',
  counters (positions_total, matched_cache, matched_exact, matched_nonposition, to_review),
  parsed_data jsonb NULL, parser_version text NULL   # пара — вместе NULL либо вместе заданы (CHECK)
  estimates_created int NULL   # только у round_id; NULL либо > 0 (CHECK); число offer-смет
                                # этого job (AGENTS.md §5, «Для раунда тендера»)
  created_at, started_at, finished_at
  # Локи — два частичных уникальных индекса:
  #   uq_import_jobs_active_pair: UNIQUE (contract_id, COALESCE(amendment_no,-1))
  #     WHERE contract_id IS NOT NULL AND status NOT IN ('done','error')
  #   uq_import_jobs_active_round: UNIQUE (round_id)
  #     WHERE round_id IS NOT NULL AND status NOT IN ('done','error')
  # Блокируется одна ПАРА (contract_id, amendment_no) либо один round_id;
  # параллельный импорт РАЗНЫХ допсоглашений одного договора — разрешён.
```
