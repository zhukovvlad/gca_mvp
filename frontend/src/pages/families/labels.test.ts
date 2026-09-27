import { describe, expect, it } from "vitest";

import {
  COMPARABILITY_REASON_VALUES,
  DECISION_SOURCE_VALUES,
  FAMILY_SOURCE_VALUES,
  NAME_ROLE_VALUES,
  SEMANTIC_KIND_VALUES,
  SEMANTIC_STATE_VALUES,
} from "@/types/domain";

import {
  CATEGORY_SOURCE_LABEL,
  CATEGORY_SOURCE_VALUES,
  comparabilityLabel,
  DECISION_SOURCE_LABEL,
  eventLabel,
  EVENT_LABEL,
  EVENT_TYPE_VALUES,
  FAMILY_SOURCE_LABEL,
  NAME_ROLE_LABEL,
  pluralRu,
  SEMANTIC_KIND_LABEL,
  SEMANTIC_STATE_LABEL,
} from "./labels";

/**
 * Словарь подписей (спека §2.2). Каждая таблица теста перебирает
 * значения ТИПА домена (`*_VALUES` из `types/domain.ts` — рантайм-массив,
 * заведённый этой же задачей рядом с типом), а не выборку значений: забытая
 * подпись здесь означает забытый ключ в `Record<T, string>`, и TS не даст
 * этому скомпилироваться в принципе — тест ловит то же самое в рантайме,
 * плюс проверяет ТЕКСТ подписи и то, что он не равен коду.
 *
 * Граница наблюдаемости: значение, УДАЛЁННОЕ из `*_VALUES`, уходит и из типа,
 * и `it.each` просто перебирает на одно меньше — vitest остаётся зелёным.
 * Этот случай ловит только `tsc -b` (лишний ключ в объектном литерале
 * `Record<T, string>` в `labels.ts`), проверено снятием защиты.
 */
describe("labels: словарь подписей §2.2", () => {
  it.each(NAME_ROLE_VALUES)("name_role %s имеет непустую подпись, не равную коду", (value) => {
    const label = NAME_ROLE_LABEL[value];
    expect(label).toBeTruthy();
    expect(label).not.toBe(value);
  });

  it("name_role — точные подписи таблицы спеки", () => {
    expect(NAME_ROLE_LABEL.WORK).toBe("работу");
    expect(NAME_ROLE_LABEL.LOCATION_ONLY).toBe("место");
    expect(NAME_ROLE_LABEL.GENERIC_WORK).toBe("род изделия без состава");
  });

  it.each(SEMANTIC_KIND_VALUES)("semantic_kind %s имеет непустую подпись, не равную коду", (value) => {
    const label = SEMANTIC_KIND_LABEL[value];
    expect(label).toBeTruthy();
    expect(label).not.toBe(value);
  });

  it("semantic_kind — точные подписи таблицы спеки", () => {
    expect(SEMANTIC_KIND_LABEL.WORK).toBe("работа");
    expect(SEMANTIC_KIND_LABEL.SYSTEM).toBe("система");
    expect(SEMANTIC_KIND_LABEL.UNKNOWN).toBe("не определён");
  });

  it.each(SEMANTIC_STATE_VALUES)("semantic_state %s имеет непустую подпись, не равную коду", (value) => {
    const label = SEMANTIC_STATE_LABEL[value];
    expect(label).toBeTruthy();
    expect(label).not.toBe(value);
  });

  it("semantic_state — точные подписи таблицы спеки", () => {
    expect(SEMANTIC_STATE_LABEL.SUGGESTED).toBe("предложен правилом");
    expect(SEMANTIC_STATE_LABEL.CONFIRMED).toBe("подтверждён");
    expect(SEMANTIC_STATE_LABEL.NOT_APPLICABLE).toBe("не применяется");
  });

  it.each(DECISION_SOURCE_VALUES)("*_source %s имеет непустую подпись, не равную коду", (value) => {
    const label = DECISION_SOURCE_LABEL[value];
    expect(label).toBeTruthy();
    expect(label).not.toBe(value);
  });

  it("*_source — точные подписи таблицы спеки", () => {
    expect(DECISION_SOURCE_LABEL.rule).toBe("правило");
    expect(DECISION_SOURCE_LABEL.manual).toBe("оператор");
  });

  it.each(FAMILY_SOURCE_VALUES)("family_source %s имеет непустую подпись, не равную коду", (value) => {
    const label = FAMILY_SOURCE_LABEL[value];
    expect(label).toBeTruthy();
    expect(label).not.toBe(value);
  });

  it("family_source — точные подписи таблицы спеки", () => {
    expect(FAMILY_SOURCE_LABEL.manual).toBe("оператор");
    expect(FAMILY_SOURCE_LABEL.suggestion).toBe("из предложения");
  });

  it.each(CATEGORY_SOURCE_VALUES)("work_category_source %s имеет непустую подпись, не равную коду", (value) => {
    const label = CATEGORY_SOURCE_LABEL[value];
    expect(label).toBeTruthy();
    expect(label).not.toBe(value);
  });

  it("work_category_source — точные подписи таблицы спеки", () => {
    expect(CATEGORY_SOURCE_LABEL.file).toBe('из „Статьи СМР“ в файле');
    expect(CATEGORY_SOURCE_LABEL.manual).toBe("ручной разнос");
  });

  it.each(COMPARABILITY_REASON_VALUES)(
    "comparability_reason %s имеет непустую подпись, не равную коду",
    (value) => {
      const label = comparabilityLabel(value);
      expect(label).toBeTruthy();
      expect(label).not.toBe(value);
    }
  );

  it("comparabilityLabel: null — «да», insufficient_description — отказ сравнения", () => {
    expect(comparabilityLabel(null)).toBe("да");
    expect(comparabilityLabel("insufficient_description")).toBe(
      "нет — сравнение ставок не производится"
    );
  });
});

/**
 * `pluralRu(n, one, few, many)` (спека §2.6) — три формы
 * родительного падежа слова «позиция»: 1/21/… — «позиция», 2-4/22-24/… —
 * «позиции», 5-20/25-30/… — «позиций», с исключением 11-14 (всегда «позиций»,
 * даже у 11 и 14, где последняя цифра — 1/4).
 */
describe("labels: pluralRu — три формы родительного падежа", () => {
  it.each([
    [1, "позиция"],
    [2, "позиции"],
    [5, "позиций"],
    [11, "позиций"],
    [12, "позиций"],
    [14, "позиций"],
    [21, "позиция"],
    [22, "позиции"],
    [25, "позиций"],
    [111, "позиций"],
  ])("pluralRu(%i, …) выбирает форму «%s»", (n, expected) => {
    expect(pluralRu(n, "позиция", "позиции", "позиций")).toBe(expected);
  });
});

/**
 * `EVENT_LABEL` — закрытый список событий фичи 1 (`models.py::SEMANTIC_EVENT_TYPES`,
 * 15 значений). Полнота множества со списком бэкенда проверяется тестом бэкенда
 * (`test_event_labels_sync.py`), который читает этот файл текстом; здесь —
 * подписи не пустые, отличны от кода, и `eventLabel` не падает на неизвестном.
 */
describe("labels: журнал событий (закрытый список фичи 1)", () => {
  it.each(EVENT_TYPE_VALUES)("событие %s имеет непустую подпись, не равную коду", (value) => {
    const label = EVENT_LABEL[value];
    expect(label).toBeTruthy();
    expect(label).not.toBe(value);
  });

  it("EVENT_TYPE_VALUES несёт ровно 15 значений закрытого списка", () => {
    expect(EVENT_TYPE_VALUES).toHaveLength(15);
  });

  // `EVENT_TYPE_VALUES` типизирован `readonly SemanticEventType[]`, а не выведен
  // в тип (`as const`), поэтому `tsc` не держит его полноту: подмена одного
  // значения повтором другого сохраняет длину 15 и молча выводит событие из
  // перебора выше. Ключи `EVENT_LABEL` держит `Record<SemanticEventType, …>`
  // (и тест бэкенда против `SEMANTIC_EVENT_TYPES`) — с ними и сверяемся.
  it("EVENT_TYPE_VALUES — без повторов и ровно те же коды, что ключи EVENT_LABEL", () => {
    expect(new Set(EVENT_TYPE_VALUES).size).toBe(EVENT_TYPE_VALUES.length);
    expect([...EVENT_TYPE_VALUES].sort()).toEqual(Object.keys(EVENT_LABEL).sort());
  });

  it("eventLabel возвращает подпись известного события", () => {
    expect(eventLabel("members_moved")).toBe(EVENT_LABEL.members_moved);
  });

  it("eventLabel на неизвестном коде возвращает сам код, не падает", () => {
    expect(eventLabel("some_future_event_type")).toBe("some_future_event_type");
  });
});
