**Когда читать:** сверяешься с устройством схемы БД: таблицы и FK, идентичность входного написания в каталоге, семантический контур (семьи и контексты), нормативы, служебные поля импорта, очередь семантических предложений, варианты работ и схемы параметров семей.

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
  tender_award_id bigint NULL          # с 0020: основание «по тендеру» (спека Б2 §2.2)
  UNIQUE (tender_award_id), UNIQUE (id, tender_award_id)
  FK (tender_award_id, object_id, contractor_id)
    → tender_awards(id, object_id, contractor_id)   # fk_contracts_tender_award; NO ACTION

estimates                    # смета (бывш. tenders); 1 договор : N смет
  id, contract_id NULL → contracts   # с 0015 (тендерный контур) — один из трёх
                                     #   владельцев, см. блок ниже: contract_id | offer_id | round_id
  amendment_no int NULL      # NULL = исходная смета, иначе номер доп. соглашения; только у contract_id
  title, data_prepared_on_date date
  import_job_id → import_jobs
  UNIQUE NULLS NOT DISTINCT (contract_id, amendment_no)   # PG16
  source_award_id bigint NULL          # с 0020: смета — копия КП отметки основания договора
  UNIQUE (id, offer_id)                # цель ключа отметки на КП
  CHECK ck_estimates_source_award: source_award_id IS NULL
    OR (contract_id IS NOT NULL AND amendment_no IS NULL)
  FK (contract_id, source_award_id) → contracts(id, tender_award_id)   # fk_estimates_source_award

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

tender_awards                # с 0020: отметки победителя; история не удаляется (спека Б2 §2.2)
  id bigint PK
  tender_id, object_id NOT NULL → tenders(id, object_id) ON DELETE CASCADE   # fk_tender_awards_tender
  offer_id, package_id NOT NULL → offers(id, tender_id, package_id)          # fk_tender_awards_offer
  contractor_id NOT NULL → offer_packages(id, contractor_id)    # fk_tender_awards_package
  estimate_id NOT NULL → estimates(id, offer_id)                # fk_tender_awards_kp_estimate: КП решения
  kp_inn text NOT NULL       # ИНН блока КП в файле этапа; CHECK kp_inn ~ '^[0-9]+$'
  awarded_at timestamptz NOT NULL DEFAULT now(), awarded_by integer NOT NULL → users RESTRICT
  not_concluded_on date, not_concluded_note text, not_concluded_by → users RESTRICT,
    not_concluded_at timestamptz      # «договор не заключён»: все NULL либо on/by/at заданы
                                      #   (ck_tender_awards_not_concluded); note — без пустой строки
  UNIQUE (id, object_id, contractor_id)
  UNIQUE INDEX uq_tender_awards_active (tender_id) WHERE not_concluded_on IS NULL
  # Ключи без явного ON DELETE — NO ACTION. uq_tenders_id_object (id, object_id),
  # uq_offer_packages_id_contractor (id, contractor_id), uq_offers_id_tender_package
  # (id, tender_id, package_id) — цели этих ключей. MATCH SIMPLE: при NULL в части
  # ключа он не проверяется, поэтому у source_award_id есть CHECK на contract_id.

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
  work_family_id NULL → work_families, family_source ('manual|suggestion|auto_suggestion') NULL,
    family_by NULL → users, family_at NULL
    # происхождение семьи — ОДИН тотальный предикат из двух полных ветвей
    # (не пара равносильностей, `docs/pitfalls/db.md`); с 0019 источник `auto_suggestion`
    # (автопринятие) — без автора: `family_by IS NULL`
  # + колонки варианта и ожидающего назначения семьи (0019) — см. раздел 7 этого файла
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
  event_type text NOT NULL       # закрытый список: пятнадцать типов 0017 и шесть типов 0019 (раздел 7 этого файла)
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
  source_award_id bigint NULL → tender_awards ON DELETE SET NULL   # с 0020: задание — копия КП;
                                # CHECK: NULL либо contract_id NOT NULL и amendment_no NULL
  created_at, started_at, finished_at
  # Локи — два частичных уникальных индекса:
  #   uq_import_jobs_active_pair: UNIQUE (contract_id, COALESCE(amendment_no,-1))
  #     WHERE contract_id IS NOT NULL AND status NOT IN ('done','error')
  #   uq_import_jobs_active_round: UNIQUE (round_id)
  #     WHERE round_id IS NOT NULL AND status NOT IN ('done','error')
  # Блокируется одна ПАРА (contract_id, amendment_no) либо один round_id;
  # параллельный импорт РАЗНЫХ допсоглашений одного договора — разрешён.
```

## 6. Очередь семантических предложений

Пять таблиц (`docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.4), миграция `0018`: задание на семантическое предложение, попытка вызова
провайдера, само предложение, удержанная сверх потолка события пачка и
состояние исполнителя захвата (ровно одна строка). Миграция `0019` расширила
задание (вид, предмет, ключ), предложение (решения) и пачку (формат отпечатков):
строки ниже описывают форму после `0019`, детали новых колонок — в разделе 7 этого файла.

```
semantic_reconcile_batches     -- удержанная пачка сверх потолка события
  id, source ('import|operation|mass|unit_reask|config_reask')
  import_job_id NULL → import_jobs, unit_id NULL   # не FK — ось приоритезации
  held_fingerprints jsonb NOT NULL    # с 0019: [{kind, context_id, family_id, schema_id, request_hash}, …]
                                       #   (отсутствующий предмет — null), отсортирован по (kind, context_id or -1,
                                       #   family_id or -1, schema_id or -1, request_hash); до 0019 — пары
                                       #   [context_id, request_hash]. АУДИТ на момент удержания —
                                       #   исполнение сверки идёт по ТЕКУЩИМ отпечаткам, это поле не перезаписывается
  fingerprints_hash text NOT NULL, contexts_count int NOT NULL
  # fingerprints_hash — sha256(json.dumps(список, ensure_ascii=False, sort_keys=True,
  #   separators=(",", ":"))) отсортированного списка отпечатков; 0019 пересчитала хэши существующих пачек
  reserve_estimate_usd numeric NOT NULL, cached_estimate_usd numeric NOT NULL
  status ('held|approved|discarded'), decided_by NULL → users, decided_at NULL
  # CHECK (status='held') = (decided_by IS NULL); CHECK (status='held') = (decided_at IS NULL)
  # UNIQUE (fingerprints_hash) WHERE status='held' — raw SQL (RAW_SQL_INDEXES);
  #   тот же индекс — арбитр INSERT ... ON CONFLICT (fingerprints_hash) WHERE status='held'

semantic_jobs                   -- одно задание на (вид, предмет, тело запроса)
  id, context_id NULL → catalog_contexts ON DELETE RESTRICT, request_hash text NOT NULL
  # context_id NULL-able с 0019: пуст у вида `family_schema` (предмет — семья); вид и предмет — раздел 7 этого файла
  status ('pending|running|done|error|cancelled|privacy_hold')
  cancel_reason ('input_changed|not_applicable|privacy_declined|stale_hold') NULL
  retry_generation int NOT NULL DEFAULT 0, attempts_in_generation int NOT NULL DEFAULT 0
  next_attempt_at timestamptz NOT NULL DEFAULT now(), claim_token uuid NULL
  last_error_class text NULL
  result_suggestion_id NULL → family_suggestions ON DELETE SET NULL   # цикл FK, см. ниже
  privacy_matches jsonb NULL, privacy_released_matches jsonb NULL
  privacy_decided_by NULL → users, privacy_decided_at NULL
  unit_id NULL   # единица контекста, порядок захвата — не FK
  batch_id NULL → semantic_reconcile_batches ON DELETE SET NULL
  # Оси запроса для журнала, вне ключа, NOT NULL (задание всегда из отрендеренного запроса):
  prompt_version, model_requested, place_dictionary_version smallint,
  candidates_hash, prefix_hash, input_hash, response_schema_version, serialization_version
  created_at, updated_at
  # С 0019 ключ — raw SQL uq_semantic_jobs_subject_request_hash (раздел 7 этого файла); прежний
  #   UNIQUE (context_id, request_hash) снят. CHECK (status='cancelled') = (cancel_reason IS NOT NULL)
  # CHECK (status='running') = (claim_token IS NOT NULL)
  # CHECK status<>'privacy_hold' OR privacy_matches IS NOT NULL
  # INDEX (status, unit_id, next_attempt_at)

semantic_job_attempts            -- одна строка на вызов провайдера
  id, job_id NOT NULL → semantic_jobs ON DELETE CASCADE
  claim_token uuid NOT NULL, retry_generation int NOT NULL, started_at timestamptz NOT NULL
  # Известно ТОЛЬКО ПОСЛЕ завершения попытки — все NULL до этого момента:
  finished_at, outcome ('ok|transient_error|permanent_error|schema_error|lost_claim') NULL,
  error_class, error_text, raw_response, validation_error,
  actual_model, provider, prompt_tokens, completion_tokens, cache_write_tokens, cached_tokens
  reserve_usd numeric NOT NULL, cost_usd numeric NULL, reserve_exceeded bool NOT NULL DEFAULT false
  prefix_hash text NOT NULL, privacy_dictionary_hash text NOT NULL
  # INDEX (prefix_hash), INDEX (started_at)

family_suggestions               -- одна строка на каждый схемно-валидный ответ модели
  id, context_id NOT NULL → catalog_contexts ON DELETE RESTRICT
  job_id NOT NULL → semantic_jobs ON DELETE CASCADE, attempt_id NOT NULL → semantic_job_attempts
  request_hash, candidates_hash text NOT NULL, candidates_snapshot jsonb NOT NULL
  family_id NULL → work_families ON DELETE SET NULL     # NULL = «новая семья» (new_family_name заполнено)
  new_family_name NULL, confidence numeric NOT NULL, reason text NOT NULL
  is_published bool NOT NULL DEFAULT false
  unpublished_reason ('stale_fingerprint|lost_claim|context_not_applicable|rejected') NULL
  decision ('accepted|rejected|other_family|family_created|accepted_pending|auto_accepted|auto_pending|auto_superseded') NULL
  #   четыре последних решения — 0019, раздел 7 этого файла
  decided_by NULL → users, decided_at NULL, created_at
  # UNIQUE (context_id) WHERE is_published — raw SQL (RAW_SQL_INDEXES); не больше одного опубликованного на контекст
  # CHECK (decided_by IS NULL) = (decision IS NULL OR decision IN ('auto_accepted','auto_pending','auto_superseded'))
  #   — с 0019 у автоматических решений автора нет; CHECK (decision IS NULL) = (decided_at IS NULL)
  # CHECK NOT is_published OR unpublished_reason IS NULL

semantic_worker_state             -- РОВНО одна строка (id=1); флаг остановки захвата, не настройка
  id smallint PK CHECK (id=1), claim_paused bool NOT NULL DEFAULT false
  paused_reason NULL, paused_attempt_id NULL → semantic_job_attempts ON DELETE RESTRICT, paused_at NULL
  last_resumed_by NULL → users, last_resumed_at NULL
  # Остановлен ⟺ paused_reason/paused_attempt_id/paused_at заполнены ВСЕ три; идёт ⟺ все три пусты.
  # CHECK claim_paused = (paused_reason IS NOT NULL) — и так же для paused_attempt_id, paused_at
  # CHECK (last_resumed_by IS NULL) = (last_resumed_at IS NULL)
  # Миграция 0018 вставляет строку (id=1, claim_paused=false); tests/conftest.py
  #   пересоздаёт её после TRUNCATE доменных таблиц (_truncate_domain_tables) — без неё захват не работает.
```

**Цикл FK.** `semantic_jobs.result_suggestion_id` → `family_suggestions` и
`family_suggestions.job_id` → `semantic_jobs` ссылаются друг на друга; в
миграции обе таблицы создаются без первой FK, она добавляется отдельным
`op.create_foreign_key` после того, как обе таблицы существуют
(`use_alter=True` в `models.py`, чтобы не ловить `CircularDependencyError`
сортировки `Base.metadata`). `semantic_worker_state.paused_attempt_id` →
`semantic_job_attempts` цикла НЕ образует (`semantic_job_attempts` создана
раньше) и объявлена обычным inline FK, `ON DELETE RESTRICT` — `SET NULL`
здесь недостижим: единственный путь к NULL — `claim_paused=false`, где CHECK
уже требует его NULL, а при `claim_paused=true` CHECK запрещает NULL вовсе.

**Трёхзначная логика CHECK** (`docs/pitfalls/db.md`): каждая равносильность
выше безопасна, потому что колонки, стоящие голым равенством (`status`,
`claim_paused`, `is_published`), — `NOT NULL`; вторая сторона равносильности
всегда `IS [NOT] NULL` (булево, никогда `NULL`).

## 7. Варианты работ и схемы параметров семей

Шесть таблиц и расширение четырёх существующих (`docs/superpowers/specs/2026-10-02-catalog-variants-design.md`
§2.4), миграция `0019`. **Схема** семьи — от 0 до 3 параметров с закрытыми
списками значений, у схемы есть версии; **вариант** — семья, версия схемы и
набор значений; контекст получает вариант и, отдельно, ожидающее назначение
семьи.

```
family_parameter_schemas         -- версия схемы семьи
  id, family_id NOT NULL → work_families ON DELETE RESTRICT, version int NOT NULL
  status ('building|frozen|superseded|cancelled'), origin ('model|manual')
  job_id NULL → semantic_jobs ON DELETE SET NULL, frozen_by NULL → users ON DELETE RESTRICT
  created_at, frozen_at NULL, superseded_at NULL, cancelled_at NULL
  # UNIQUE (family_id, version); UNIQUE (id, family_id) — цель составного FK варианта
  # UNIQUE (family_id) WHERE status='frozen'   — raw SQL uq_family_parameter_schemas_frozen: одна текущая
  # UNIQUE (family_id) WHERE status='building' — raw SQL uq_family_parameter_schemas_building: одна пересборка
  # CHECK (status='cancelled') = (cancelled_at IS NOT NULL)
  # CHECK (status IN ('frozen','superseded')) = (frozen_at IS NOT NULL)   # supersede не стирает заморозку
  # CHECK (status='superseded') = (superseded_at IS NOT NULL)
  # CHECK (origin='manual') = (frozen_by IS NOT NULL)

family_parameters                -- параметр версии схемы
  id, schema_id NOT NULL → family_parameter_schemas ON DELETE CASCADE
  ordinal smallint NOT NULL, name text NOT NULL, name_norm text NOT NULL
  # UNIQUE (schema_id, ordinal); CHECK ordinal BETWEEN 1 AND 3; CHECK btrim(name) <> ''
  # UNIQUE (id, schema_id) — цель составных FK значений варианта и контекста

family_parameter_values          -- значение закрытого списка; «не уточнено» — отсутствие значения, не пустая строка
  id, parameter_id NOT NULL → family_parameters ON DELETE CASCADE
  value text NOT NULL, value_norm text NOT NULL, origin ('schema|extension|manual')
  merged_into_id NULL, created_at
  # CHECK btrim(value) <> '' AND value_norm <> ''
  # UNIQUE (parameter_id, value_norm) — атомарное расширение списка
  # UNIQUE (id, parameter_id) — цель составных FK
  # FK (merged_into_id, parameter_id) → (id, parameter_id) ON DELETE RESTRICT — слияние только внутри параметра
  # CHECK merged_into_id IS NULL OR merged_into_id <> id

work_variants                    -- вариант: семья + версия схемы + набор значений
  id, family_id NOT NULL → work_families ON DELETE RESTRICT, schema_id NOT NULL
  values_key text NOT NULL       # «1=17|2=?|3=42» (ordinal=value_id, ? — пусто); для нулевой схемы — ''
  status ('active|archived'), merged_into_id NULL → work_variants ON DELETE RESTRICT
  created_at, archived_at NULL
  # FK (schema_id, family_id) → family_parameter_schemas (id, family_id)
  #   DEFERRABLE INITIALLY DEFERRED — отложен до commit (слияние семей меняет семью у вариантов и версий)
  # UNIQUE (schema_id, values_key) — одинаковый набор один вариант (и архивный тоже)
  # UNIQUE (id, family_id), UNIQUE (id, schema_id) — цели составных FK контекста и значений
  # CHECK (status='archived') = (archived_at IS NOT NULL); CHECK merged_into_id IS NULL OR status='archived'

work_variant_values              -- набор значений варианта построчно
  variant_id, schema_id, parameter_id NOT NULL, value_id NULL    # NULL = не уточнено
  # PK (variant_id, parameter_id)
  # FK (variant_id, schema_id) → work_variants (id, schema_id) ON DELETE CASCADE
  # FK (parameter_id, schema_id) → family_parameters (id, schema_id)       # параметр из схемы варианта
  # FK (value_id, parameter_id) → family_parameter_values (id, parameter_id)   # значение этого параметра

context_parameter_values         -- значения контекста по текущей версии схемы его семьи (состояние, не история)
  context_id NOT NULL → catalog_contexts ON DELETE RESTRICT, schema_id, parameter_id NOT NULL, value_id NULL
  source ('name|path|manual|path_conflict|none'), job_id NULL → semantic_jobs ON DELETE SET NULL, created_at
  # PK (context_id, parameter_id)
  # FK (parameter_id, schema_id) → family_parameters (id, schema_id)
  # FK (value_id, parameter_id) → family_parameter_values (id, parameter_id)
  # CHECK (value_id IS NULL) = (source IN ('path_conflict','none'))

catalog_contexts                 -- новые колонки (0019)
  work_variant_id NULL, variant_at NULL, variant_paths_hash NULL   # заголовок результата значений
  variant_split_hint ('path_conflict') NULL
  pending_family_id NULL → work_families ON DELETE RESTRICT
  pending_family_source ('manual|suggestion|auto_suggestion') NULL
  pending_suggestion_id NULL → family_suggestions ON DELETE RESTRICT, pending_by NULL → users ON DELETE RESTRICT
  pending_threshold numeric NULL, pending_at NULL
  # FK (work_variant_id, work_family_id) → work_variants (id, family_id) DEFERRABLE INITIALLY DEFERRED
  #   — семья варианта равна семье контекста; отложен до commit
  # CHECK work_variant_id IS NULL OR work_family_id IS NOT NULL   — закрывает MATCH SIMPLE: пара проверяется целиком
  # CHECK (work_variant_id IS NULL) = (variant_at IS NULL); то же для variant_paths_hash
  # CHECK variant_split_hint IS NULL OR work_variant_id IS NOT NULL
  # CK_CONTEXT_PENDING — ОДИН тотальный предикат из двух полных ветвей: либо все шесть pending_* пусты,
  #   либо заполнены family/source/at и (source='manual') = (by IS NOT NULL),
  #   (source<>'manual') = (suggestion_id IS NOT NULL), (source='auto_suggestion') = (threshold IS NOT NULL)

semantic_jobs                    -- новые колонки и ключ (0019)
  kind ('family_suggestion|family_schema|context_values') NOT NULL   # у ORM Python-умолчание family_suggestion,
                                                                      #   серверного умолчания нет
  family_id NULL → work_families ON DELETE RESTRICT, schema_id NULL → family_parameter_schemas ON DELETE RESTRICT
  paths_hash text NULL, context_id — NULL-able
  # CHECK (kind='family_schema') = (context_id IS NULL AND family_id IS NOT NULL)
  # CHECK (kind<>'family_schema') = (context_id IS NOT NULL)
  # CHECK (kind<>'family_suggestion') = (schema_id IS NOT NULL)   # схема: версия building; значения: текущая версия
  # CHECK result_suggestion_id IS NULL OR kind='family_suggestion'
  # UNIQUE (kind, COALESCE(context_id,-1), COALESCE(family_id,-1), COALESCE(schema_id,-1), request_hash)
  #   — raw SQL uq_semantic_jobs_subject_request_hash, заменил uq_semantic_jobs_context_request_hash;
  #   версия схемы — часть предмета (пересборка без смены имён даёт тот же request_hash)

family_suggestions               -- расширение (0019): четыре новых решения
  decision += accepted_pending (человек подтвердил, смена отложена ожиданием; decided_by NOT NULL),
    auto_accepted, auto_pending, auto_superseded (автоматические; decided_by IS NULL)
  # CHECK (decided_by IS NULL) = (decision IS NULL OR decision IN ('auto_accepted','auto_pending','auto_superseded'))

semantic_events                  -- расширение (0019): шесть типов
  # предмет контекст: context_variant_assigned, context_family_pending, context_not_work
  # предмет семья:    family_schema_frozen, family_schema_value_added, family_variants_merged
  # CK_EVENT_SUBJECT_BY_TYPE — по-прежнему явное множество типов; в него вошли три семейных

semantic_reconcile_batches       -- формат held_fingerprints и хэша — раздел 6 этого файла
```

**Отложенных FK ровно два** (оба `DEFERRABLE INITIALLY DEFERRED`): контекст →
вариант и вариант → версия схемы. Слияние семей меняет семью у контекста,
варианта и версии схемы в одной транзакции, и ни один порядок немедленных
проверок её не проходит; перед `commit` оно ставит `SET CONSTRAINTS` двух
отложенных FK по имени в `IMMEDIATE` и тут же обратно в `DEFERRED`, чтобы нарушение всплыло в своей транзакции. Закоммиченное
состояние инвариант выдерживает всегда. Все прочие FK проверяются немедленно.

**Цикл FK.** `family_parameter_schemas.job_id` → `semantic_jobs`,
`semantic_jobs.schema_id` → `family_parameter_schemas` и
`catalog_contexts.pending_suggestion_id` → `family_suggestions` замыкают циклы
с таблицами очереди и контекстов. В миграции `job_id` объявлен inline в
`create_table` (`semantic_jobs` уже существует), а `semantic_jobs.schema_id` и
`catalog_contexts.pending_suggestion_id` добавляются отдельными
`op.create_foreign_key` после создания целевых таблиц. В `models.py` все три
FK объявлены с `use_alter=True`, чтобы сортировка метаданных не ловила цикл.

**Ничего не удаляется:** ссылки `RESTRICT`, кроме каскада версия → параметры →
значения (версия удаляется только в тесте), вариант → его значения и
`job_id → SET NULL`. Варианты и значения архивируются. `context_parameter_values`
— текущее состояние контекста, а не история: набор заменяется целиком и
удаляется при снятии семьи, «не работа» и глобальной пометке.

**`downgrade` отказывает при данных** в любом из десяти носителей: варианты,
версии схем, значения контекстов, контексты с вариантом, контексты с
ожиданием, контексты с `family_source = 'auto_suggestion'`, задания новых видов, предложения с новыми решениями, события шести
новых типов, пачки с отпечатками не `family_suggestion`. На пустых носителях
откат возвращает прежний ключ `uq_semantic_jobs_context_request_hash` и
пачкам — пары `[context_id, request_hash]` с прежним хэшем.

**Трёхзначная логика CHECK** (`docs/pitfalls/db.md`): равносильности стоят
голым равенством только на NOT NULL стороне; `CK_CONTEXT_PENDING` и
`CK_CONTEXT_FAMILY_PROVENANCE` — один тотальный предикат из двух полных ветвей.
