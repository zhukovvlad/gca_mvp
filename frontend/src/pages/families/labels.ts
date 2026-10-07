import type {
  BatchSource,
  ComparabilityReason,
  ContextValueSource,
  DecisionSource,
  FamilyChangeKind,
  FamilySource,
  FamilyVariantStatus,
  NameRole,
  SchemaValueOrigin,
  SemanticKind,
  SemanticState,
  SuggestionBand,
  VariantState,
  WorkFamilyStatus,
} from "@/types/domain";

/**
 * Словарь подписей экрана «Семьи и контексты» (спека
 * `2026-09-25-families-screen-design.md` §2.2). Коды полей не печатаются
 * нигде на экране, кроме подсказки журнала (спека §2.2, Global Constraints
 * ветки) — эта таблица единственное место, где код превращается в текст, и
 * тесты читают именно её, а не компонент.
 *
 * Каждый `Record<T, string>` объявлен по типу из `types/domain.ts`: забытое
 * значение не компилируется (`tsc` требует все ключи), и та же дисциплина
 * действует у `EVENT_LABEL` ниже — только там закрытый список не в
 * `domain.ts`, а в `backend/models.py::SEMANTIC_EVENT_TYPES`, и сверяет его
 * `backend/tests/unit/test_event_labels_sync.py`.
 */

export const NAME_ROLE_LABEL: Record<NameRole, string> = {
  WORK: "работу",
  LOCATION_ONLY: "место",
  GENERIC_WORK: "род изделия без состава",
};

export const SEMANTIC_KIND_LABEL: Record<SemanticKind, string> = {
  WORK: "работа",
  SYSTEM: "система",
  UNKNOWN: "не определён",
};

export const SEMANTIC_STATE_LABEL: Record<SemanticState, string> = {
  SUGGESTED: "предложен правилом",
  CONFIRMED: "подтверждён",
  NOT_APPLICABLE: "не применяется",
};

/** `*_source` — общий словарь `rule`/`manual` для видов и ролей имени. */
export const DECISION_SOURCE_LABEL: Record<DecisionSource, string> = {
  rule: "правило",
  manual: "оператор",
};

export const FAMILY_SOURCE_LABEL: Record<FamilySource, string> = {
  manual: "оператор",
  suggestion: "из предложения",
  auto_suggestion: "принято автоматически",
};

/**
 * Источник значения параметра у варианта контекста (`context_parameter_values.source`).
 * `none` и `path_conflict` значения не несут — экран печатает «не уточнено», а `path_conflict`
 * дополнительно называет причину.
 */
export const VARIANT_VALUE_SOURCE_LABEL: Record<ContextValueSource, string> = {
  name: "по наименованию",
  path: "по разделам",
  manual: "вручную",
  path_conflict: "разделы расходятся",
  none: "не уточнено",
};

/** Фильтр очереди контекстов по наличию варианта. */
export const VARIANT_STATE_LABEL: Record<VariantState, string> = {
  with: "С вариантом",
  without: "Без варианта",
};

/** Исход смены семьи контексту, `family_id = null` у `assigned` значит снятие семьи. */
export const FAMILY_CHANGE_OUTCOME_LABEL: Record<FamilyChangeKind, string> = {
  assigned: "Семья назначена.",
  pending: "Семья будет назначена после значений по схеме новой семьи.",
  unchanged: "Семья та же.",
};

export const FAMILY_REMOVED_LABEL = "Семья снята.";

/**
 * Отказы действий над контекстом и строкой каталога (`services/work_families.py`,
 * `services/family_change.py`, `services/review.py`): код ответа превращается в подпись, сам код
 * и текст сервера на экран не выходят. Неизвестный код — общая подпись.
 */
export const CONTEXT_REFUSAL_LABEL: Record<string, string> = {
  context_not_found: "Контекст не найден: обновите экран.",
  context_archived: "Контекст в архиве: менять его нельзя.",
  context_not_applicable: "Контекст уже отмечен как не работа.",
  family_not_found: "Семья не найдена: обновите экран.",
  family_not_active: "Семья не активна: назначать можно только активную семью.",
  unit_mismatch: "Единица семьи не совпадает с единицей контекста.",
  family_lock_mismatch: "Состояние изменилось, пока шло действие. Обновите экран и повторите.",
  position_not_found: "Строка каталога не найдена: обновите экран.",
  position_not_position: "Строка уже не ждёт решения: обновите экран.",
  position_has_standards: "У строки есть нормативы: пометить её нельзя, пока они действуют.",
  invalid_kind: "Такую пометку поставить нельзя.",
};

export function contextRefusalLabel(code: string | undefined): string {
  return (code !== undefined && CONTEXT_REFUSAL_LABEL[code]) || SCHEMA_REFUSAL_FALLBACK;
}

/**
 * Статус семьи (`WorkFamily.status`, сверка с макетом 27.09.2026, спека §2.8):
 * печатается словом, код `draft`/`active`/`archived` на экран не выходит —
 * тот же список подписей, что несёт цветной `Badge` строки/панели
 * (`FamiliesTab.tsx`) и фильтр статуса.
 */
export const FAMILY_STATUS_LABEL: Record<WorkFamilyStatus, string> = {
  draft: "черновик",
  active: "активна",
  archived: "в архиве",
};

/** Происхождение значения параметра схемы (`family_parameter_values.origin`). */
export const SCHEMA_VALUE_ORIGIN_LABEL: Record<SchemaValueOrigin, string> = {
  schema: "из схемы",
  extension: "добавлено при разборе",
  manual: "вручную",
};

/** Статус варианта семьи (`work_variants.status`): печатается словом, не кодом. */
export const VARIANT_STATUS_LABEL: Record<FamilyVariantStatus, string> = {
  active: "активен",
  archived: "в архиве",
};

/** Значение параметра, которого у варианта нет (значение «не уточнено»). */
export const VARIANT_VALUE_UNSPECIFIED = "не уточнено";

const SCHEMA_REFUSAL_FALLBACK = "Не удалось выполнить действие. Обновите экран и повторите.";

/**
 * Отказы схемы, вариантов и пересборки (`services/work_variants.py`,
 * `routers/semantic.py`): код ответа превращается в подпись, сам код и текст
 * сервера на экран не выходят. Неизвестный код — общая подпись.
 */
export const SCHEMA_REFUSAL_LABEL: Record<string, string> = {
  schema_parameter_renamed:
    "Параметр нельзя переименовать по смыслу: это новый параметр. Допустима только правка написания.",
  schema_value_removed: "Значение нельзя удалить — только слить с другим.",
  schema_blank: "Имя параметра и его значения не должны быть пустыми.",
  schema_bad_ordinals: "Параметров может быть от одного до трёх, номера не повторяются.",
  schema_building: "У семьи идёт пересборка схемы: сначала отмените её.",
  schema_no_building: "Пересборка уже не идёт: отменять нечего.",
  schema_no_current: "У семьи ещё нет схемы.",
  merge_values_other_parameter: "Источник и цель должны быть значениями одного параметра.",
  merge_source_merged: "Источник уже слит с другим значением.",
  merge_value_cycle: "Это слияние замкнуло бы цепочку синонимов: выберите другую цель.",
  parameter_not_found: "Параметр не найден: обновите экран.",
  value_not_found: "Значение не найдено: обновите экран.",
  family_not_found: "Семья не найдена: обновите экран.",
  family_not_active: "Семья не активна: схему можно менять только у активной семьи.",
  preview_changed: "Состояние изменилось, откройте предпросмотр заново.",
};

export function schemaRefusalLabel(code: string | undefined): string {
  return (code !== undefined && SCHEMA_REFUSAL_LABEL[code]) || SCHEMA_REFUSAL_FALLBACK;
}

/**
 * `work_category_source` не заведён отдельным типом в `types/domain.ts`
 * (поле там — `string | null`, см. `ContextCard`), поэтому значения — свой
 * `as const`-массив здесь же, а не импорт из домена (спека §2.2:
 * `Record<"file" | "manual", string>`).
 */
export const CATEGORY_SOURCE_VALUES = ["file", "manual"] as const;
export type CategorySource = (typeof CATEGORY_SOURCE_VALUES)[number];

export const CATEGORY_SOURCE_LABEL: Record<CategorySource, string> = {
  file: 'из „Статьи СМР“ в файле',
  manual: "ручной разнос",
};

/**
 * Пояснения плашек источника подписи (спека §2.3) — «статья СМР»
 * (классификатор `work_categories`, общий для всех смет) / «в смете»
 * (раздел конкретной сметы). Живут здесь, а не в `SourceChip.tsx`:
 * компонент экспортирует ТОЛЬКО компонент (`react-refresh/only-export-components`
 * не выключен для `src/pages/**`, `eslint.config.js` выключает его лишь для
 * `src/components/ui/**` и `src/test/**`), а легенда экрана (спека §2.3)
 * обязана показывать ТОТ ЖЕ текст, не свою копию.
 */
export type SourceKind = "classifier" | "estimate";

export const SOURCE_EXPLANATION: Record<SourceKind, string> = {
  classifier: "классификатор, общий для всех смет",
  estimate: "разделы конкретной сметы",
};

/**
 * Подпись «Состав описан» (спека §2.2), не «Сравнимость»: пустая причина
 * значит только «сравнение не запрещено», а не «есть с чем сравнить»
 * (разбор 24.09.2026 — строка «есть с чем сравнить» отложена в фичу 4).
 */
export function comparabilityLabel(reason: ComparabilityReason | null): string {
  return reason === null ? "да" : "нет — сравнение ставок не производится";
}

/**
 * Число → одна из трёх русских форм слова в родительном падеже (1 / 2-4 /
 * 5+, с исключением 11-14) — не суффикс к общему стволу
 * (`lib/format.ts::pluralRu` для этого не годится: «позиция»/«позиции»/
 * «позиций» меняют не только окончание). Тот же приём, что
 * `pluralDecision` в `pages/tenders/summary/StageSummaryTable.tsx` —
 * отдельная копия здесь, а не импорт оттуда: страничная функция не
 * экспортируется модулем чужого экрана.
 */
export function pluralRu(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

/**
 * Закрытый список событий журнала фичи 1 — 15 значений
 * (`backend/models.py::SEMANTIC_EVENT_TYPES`). Не Python str-Enum на
 * бэкенде намеренно (докстрока `models.py`), поэтому здесь список
 * продублирован литералом; полноту и точное совпадение множеств в обе
 * стороны проверяет `backend/tests/unit/test_event_labels_sync.py`, который
 * читает этот файл текстом и извлекает ключи `EVENT_LABEL` регэкспом.
 * Сверка идёт МНОЖЕСТВАМИ (порядок ключей не важен), а регэксп принимает
 * голый и кавыченный ключ и значение в любых из трёх кавычек TS — значит,
 * бэкенду важно только одно: один ключ на строке, не важны ни порядок, ни
 * пробелы вокруг `:`, ни стиль кавычек.
 */
export type SemanticEventType =
  | "context_created"
  | "context_split"
  | "context_merged"
  | "members_moved"
  | "members_marked_stale"
  | "kind_set"
  | "name_role_set"
  | "context_family_assigned"
  | "context_archived"
  | "routing_rules_dropped"
  | "family_created"
  | "family_updated"
  | "family_activated"
  | "family_archived"
  | "family_merged"
  | "context_variant_assigned"
  | "context_family_pending"
  | "context_not_work"
  | "family_schema_frozen"
  | "family_schema_value_added"
  | "family_variants_merged";

/** Рантайм-список значений {@link SemanticEventType} — тот же порядок, для перебора тестами. */
export const EVENT_TYPE_VALUES: readonly SemanticEventType[] = [
  "context_created",
  "context_split",
  "context_merged",
  "members_moved",
  "members_marked_stale",
  "kind_set",
  "name_role_set",
  "context_family_assigned",
  "context_archived",
  "routing_rules_dropped",
  "family_created",
  "family_updated",
  "family_activated",
  "family_archived",
  "family_merged",
  "context_variant_assigned",
  "context_family_pending",
  "context_not_work",
  "family_schema_frozen",
  "family_schema_value_added",
  "family_variants_merged",
];

export const EVENT_LABEL: Record<SemanticEventType, string> = {
  context_created: "контекст создан",
  context_split: "контекст разделён",
  context_merged: "контексты объединены",
  members_moved: "позиции перенесены",
  members_marked_stale: "позиции помечены устаревшими",
  kind_set: "вид задан",
  name_role_set: "роль имени задана",
  context_family_assigned: "семья назначена",
  context_archived: "контекст в архиве",
  routing_rules_dropped: "правила маршрутизации сброшены",
  family_created: "семья создана",
  family_updated: "семья изменена",
  family_activated: "семья активирована",
  family_archived: "семья в архиве",
  family_merged: "семьи объединены",
  context_variant_assigned: "вариант назначен",
  context_family_pending: "семья ожидает назначения",
  context_not_work: "отмечено: не работа",
  family_schema_frozen: "схема заморожена",
  family_schema_value_added: "значение схемы добавлено",
  family_variants_merged: "значения схемы слиты",
};

/** Подпись события журнала; неизвестный код (будущее событие) — сам код, не падает. */
export function eventLabel(eventType: string): string {
  return (EVENT_LABEL as Record<string, string>)[eventType] ?? eventType;
}

/**
 * Виды предиката правила разноса (`services/context_routing.py`,
 * `PREDICATE_NEAREST_CHAPTER_EQUALS`/`PREDICATE_CHAPTER_CHAIN_CONTAINS`/
 * `PREDICATE_CHAPTER_LEVEL_EQUALS`; спека `2026-09-22-catalog-families-
 * design.md` §2.2) — коды печатались диалогом «Разделить…» карточки
 * контекста напрямую (`ContextCard.tsx`), Global Constraints этой ветки
 * запрещают коды на экране вне подсказки журнала.
 */
export const ROUTING_RULE_KIND_VALUES = [
  "nearest_chapter_equals",
  "chapter_chain_contains",
  "chapter_level_equals",
] as const;
export type RoutingRuleKind = (typeof ROUTING_RULE_KIND_VALUES)[number];

export const ROUTING_RULE_KIND_LABEL: Record<RoutingRuleKind, string> = {
  nearest_chapter_equals: "ближайший раздел равен",
  chapter_chain_contains: "раздел встречается в цепочке",
  chapter_level_equals: "раздел на уровне равен",
};

/**
 * Полоса уверенности группы очереди «Семья из списка» (спека
 * semantic-suggestions §2.12): границы те же, что у `band_of` бэкенда
 * (`crud/semantic_queue.py`) — 0,9 входит в верхнюю полосу, 0,7 в среднюю.
 */
export const BAND_LABEL: Record<SuggestionBand, string> = {
  high: "≥ 0,9",
  mid: "0,7–0,9",
  low: "< 0,7",
};

/** Источник удержанной пачки для плашки шапки «Удержано: …». */
export const BATCH_SOURCE_LABEL: Record<BatchSource, string> = {
  import: "Импорт сметы",
  operation: "Операция над контекстами",
  mass: "Массовая постановка",
  unit_reask: "Перезапрос единицы",
  config_reask: "Перезапрос по конфигурации",
};

/**
 * Место совпадения проверки приватности (`where` задания в `privacy_hold`,
 * спека semantic-suggestions §2.3): `context` — строка контекста,
 * `family:<id>` — строка семьи в списке кандидатов, `prompt` — текст промпта.
 * Неизвестное место (будущее) печатается нейтрально, код на экран не выходит.
 */
export function matchPlaceLabel(where: string): string {
  if (where === "context") return "в строке";
  if (where === "prompt") return "в тексте промпта";
  const familyId = /^family:(\d+)$/.exec(where);
  if (familyId) return `в списке семей (семья ${familyId[1]})`;
  return "в теле запроса";
}

/**
 * Классы ошибок задания, которые выставляют клиент модели и восстановление при
 * старте (`services/semantic_client.py`, `semantic_worker.py`, `semantic_runner.py`).
 */
export const JOB_ERROR_CLASS_LABEL: Record<string, string> = {
  timeout: "таймаут вызова",
  transport: "сетевая ошибка",
  bad_response: "ответ провайдера не разобран",
  empty_response: "пустой ответ провайдера",
  schema_error: "ответ не по схеме",
  interrupted: "прервано остановкой сервера",
};

/**
 * Класс ошибки задания: известный — словом (`http_429` — «HTTP 429»), любой
 * другой (исполнитель подставляет имя класса исключения) печатается как пришёл.
 */
export function jobErrorClassLabel(errorClass: string | null): string {
  if (errorClass === null) return "не указан";
  const known = JOB_ERROR_CLASS_LABEL[errorClass];
  if (known !== undefined) return known;
  const http = /^http_(\d{3})$/.exec(errorClass);
  if (http) return `HTTP ${http[1]}`;
  return errorClass;
}
