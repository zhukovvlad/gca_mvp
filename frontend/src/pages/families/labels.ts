import type {
  ComparabilityReason,
  DecisionSource,
  FamilySource,
  NameRole,
  SemanticKind,
  SemanticState,
} from "@/types/domain";

/**
 * Словарь подписей экрана «Семьи и контексты» (спека
 * `2026-09-25-families-screen-design.md` §2.2, план Task 6). Коды полей не
 * печатаются нигде на экране, кроме подсказки журнала (Global Constraints
 * плана) — эта таблица единственное место, где код превращается в текст, и
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
 * `work_category_source` не заведён отдельным типом в `types/domain.ts`
 * (поле там — `string | null`, см. `ContextCard`), поэтому значения — свой
 * `as const`-массив здесь же, а не импорт из домена (план Task 6,
 * Interfaces: `Record<"file" | "manual", string>`).
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
 * `src/components/ui/**` и `src/test/**`), а легенда Task 7 обязана
 * показывать ТОТ ЖЕ текст, не свою копию.
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
