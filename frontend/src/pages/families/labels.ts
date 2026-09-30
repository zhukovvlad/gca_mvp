import type {
  BatchSource,
  ComparabilityReason,
  DecisionSource,
  FamilySource,
  NameRole,
  SemanticKind,
  SemanticState,
  SuggestionBand,
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
};

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
  | "family_merged";

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
