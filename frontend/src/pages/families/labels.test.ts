import { describe, expect, it } from "vitest";

import {
  COMPARABILITY_REASON_VALUES,
  DECISION_SOURCE_VALUES,
  FAMILY_SOURCE_VALUES,
  NAME_ROLE_VALUES,
  SEMANTIC_KIND_VALUES,
  SEMANTIC_STATE_VALUES,
  WORK_FAMILY_STATUS_VALUES,
} from "@/types/domain";

import {
  CATALOG_REVIEW_KIND_LABEL,
  CATEGORY_SOURCE_LABEL,
  CATEGORY_SOURCE_VALUES,
  comparabilityLabel,
  CONTEXT_REFUSAL_LABEL,
  contextRefusalLabel,
  DECISION_SOURCE_LABEL,
  DISCOVERY_REFUSAL_TEMPLATE,
  discoveryNamesLabel,
  discoveryRefusalLabel,
  eventLabel,
  EVENT_LABEL,
  EVENT_TYPE_VALUES,
  FAMILY_SOURCE_LABEL,
  FAMILY_STATUS_LABEL,
  jobErrorClassLabel,
  matchPlaceLabel,
  NAME_ROLE_LABEL,
  pluralRu,
  SEMANTIC_KIND_LABEL,
  SCHEMA_REFUSAL_LABEL,
  SCHEMA_VALUE_ORIGIN_LABEL,
  schemaRefusalLabel,
  SEMANTIC_STATE_LABEL,
  VARIANT_STATUS_LABEL,
  VARIANT_VALUE_SOURCE_LABEL,
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
    expect(FAMILY_SOURCE_LABEL.auto_suggestion).toBe("принято автоматически");
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

  it.each(WORK_FAMILY_STATUS_VALUES)(
    "статус семьи %s имеет непустую подпись, не равную коду",
    (value) => {
      const label = FAMILY_STATUS_LABEL[value];
      expect(label).toBeTruthy();
      expect(label).not.toBe(value);
    }
  );

  it("статус семьи — точные подписи сверки с макетом (27.09.2026)", () => {
    expect(FAMILY_STATUS_LABEL.draft).toBe("черновик");
    expect(FAMILY_STATUS_LABEL.active).toBe("активна");
    expect(FAMILY_STATUS_LABEL.archived).toBe("в архиве");
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

  it("EVENT_TYPE_VALUES несёт ровно 22 значения закрытого списка", () => {
    expect(EVENT_TYPE_VALUES).toHaveLength(22);
  });

  // `EVENT_TYPE_VALUES` типизирован `readonly SemanticEventType[]`, а не выведен
  // в тип (`as const`), поэтому `tsc` не держит его полноту: подмена одного
  // значения повтором другого сохраняет длину 22 и молча выводит событие из
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

describe("labels: место совпадения и класс ошибки задания", () => {
  it("context подписан «в строке»", () => {
    expect(matchPlaceLabel("context")).toBe("в строке");
  });

  it("family:<id> подписан «в списке семей (семья N)» с номером из кода", () => {
    expect(matchPlaceLabel("family:501")).toBe("в списке семей (семья 501)");
    expect(matchPlaceLabel("family:7")).toBe("в списке семей (семья 7)");
  });

  it("prompt подписан «в тексте промпта»", () => {
    expect(matchPlaceLabel("prompt")).toBe("в тексте промпта");
  });

  it("неизвестное место не выводит код на экран", () => {
    expect(matchPlaceLabel("future_place")).toBe("в теле запроса");
    expect(matchPlaceLabel("family:abc")).toBe("в теле запроса");
  });

  it.each([
    ["timeout", "таймаут вызова"],
    ["transport", "сетевая ошибка"],
    ["bad_response", "ответ провайдера не разобран"],
    ["empty_response", "пустой ответ провайдера"],
    ["schema_error", "ответ не по схеме"],
    ["interrupted", "прервано остановкой сервера"],
    ["http_429", "HTTP 429"],
    ["http_503", "HTTP 503"],
  ])("класс ошибки %s печатается словом «%s»", (code, label) => {
    expect(jobErrorClassLabel(code)).toBe(label);
  });

  it("неизвестный класс (имя исключения исполнителя) печатается как пришёл, отсутствие — «не указан»", () => {
    expect(jobErrorClassLabel("ConnectionResetError")).toBe("ConnectionResetError");
    expect(jobErrorClassLabel("http_4290")).toBe("http_4290");
    expect(jobErrorClassLabel(null)).toBe("не указан");
  });
});

describe("подписи схемы и вариантов", () => {
  it("происхождение значения и статус варианта печатаются словом", () => {
    expect(SCHEMA_VALUE_ORIGIN_LABEL).toEqual({
      schema: "из схемы",
      extension: "добавлено при разборе",
      manual: "вручную",
    });
    expect(VARIANT_STATUS_LABEL).toEqual({ active: "активен", archived: "в архиве" });
  });

  it("известный код отказа превращается в подпись, не содержащую кода", () => {
    for (const [code, label] of Object.entries(SCHEMA_REFUSAL_LABEL)) {
      expect(schemaRefusalLabel(code)).toBe(label);
      expect(label).not.toContain(code);
      expect(label).not.toMatch(/[a-z]_[a-z]/);
    }
  });

  it("неизвестный или отсутствующий код — общая подпись, не сам код", () => {
    const fallback = "Не удалось выполнить действие. Обновите экран и повторите.";
    expect(schemaRefusalLabel("some_future_code")).toBe(fallback);
    expect(schemaRefusalLabel(undefined)).toBe(fallback);
  });
});

describe("подписи вариантов и отказов контекста", () => {
  it.each(["name", "path", "manual", "path_conflict", "none"] as const)(
    "источник значения %s имеет непустую подпись, не равную коду",
    (source) => {
      const label = VARIANT_VALUE_SOURCE_LABEL[source];
      expect(label).toBeTruthy();
      expect(label).not.toBe(source);
    }
  );

  it.each(Object.keys(CONTEXT_REFUSAL_LABEL))("код отказа %s — подпись без самого кода", (code) => {
    const label = contextRefusalLabel(code);
    expect(label.length).toBeGreaterThan(10);
    expect(label).not.toContain(code);
  });

  it("неизвестный и пустой код — общая подпись", () => {
    expect(contextRefusalLabel("some_future_code")).toBe("Не удалось выполнить действие. Обновите экран и повторите.");
    expect(contextRefusalLabel(undefined)).toBe("Не удалось выполнить действие. Обновите экран и повторите.");
  });
});

/**
 * Тексты отказов фичи 3б (спека 3б §2.12). Литерал ниже переписан из таблицы спеки отдельно от
 * `labels.ts`: проверка сверяет подписи с ним, а не с самой собой. Каждая строка — код, значения
 * подстановки («…», «N», «M» таблицы) и полный текст, который увидит человек.
 */
const REFUSAL_TABLE: Array<[string, Record<string, string | number>, string]> = [
  ["discovery_in_progress", { unit: "м²" }, "Открытие семей для единицы «м²» уже идёт — дождитесь результата или разберите задержанное."],
  ["discovery_unit_busy", { unit: "м²", count: 7 }, "В единице «м²» идёт перезапрос: 7 заданий предложений ещё не выполнены. Откройте семьи, когда он закончится, — иначе черновики устареют до прихода."],
  ["discovery_nothing_to_do", { unit: "м²" }, "В единице «м²» нет строк без семьи и семей без категории — открывать нечего."],
  ["discovery_too_many_names", { unit: "м²", count: 912, limit: 400 }, "В единице «м²» 912 различных наименований без семьи — больше предела 400 одного открытия."],
  ["discovery_input_unchanged", { unit: "м²" }, "С прошлого открытия единицы «м²» ничего не изменилось — его черновики и есть ответ. Правьте, сливайте или отбрасывайте их."],
  ["discovery_run_superseded", {}, "Эти черновики устарели: единица открыта заново. Обновите экран."],
  ["draft_not_open", { name: "Банковская гарантия" }, "Черновик «Банковская гарантия» уже активирован, слит, отброшен или устарел. Слитый и отброшенный можно вернуть."],
  ["draft_not_restorable", { name: "Банковская гарантия" }, "Черновик «Банковская гарантия» вернуть нельзя: он уже стал семьёй (её архивируют на вкладке «Семьи») или единица открыта заново."],
  ["category_not_found", { name: "№ 4" }, "Категории «№ 4» больше нет — её удалили. Выберите другую."],
  ["family_not_active", { name: "Геотекстиль" }, "Семья «Геотекстиль» больше не активна — слить с ней черновик нельзя."],
  ["draft_without_category", { name: "Банковская гарантия" }, "У черновика «Банковская гарантия» не выбрана категория."],
  ["context_not_in_group", {}, "Строка не входит в группу «Не работа» этого открытия."],
  ["category_not_proposed", { name: "Геотекстиль" }, "Семье «Геотекстиль» категорию в этом открытии не предлагали — смените её на карточке семьи."],
  ["duplicate_active_family", { name: "Геотекстиль", unit: "м²" }, "Активная семья «Геотекстиль» в единице «м²» уже есть — переименуйте черновик или слейте его с ней."],
  ["activate_without_category", {}, "Сначала выберите категорию семьи."],
  ["clear_category_active", {}, "У активной семьи категорию можно сменить, но не снять."],
  ["category_in_use", { name: "Работа", count: 168 }, "Категорию «Работа» носят 168 семей — удалить можно только пустую."],
  ["category_blank_title", {}, "У категории должно быть имя — по определению модель выбирает категорию."],
  ["category_blank_definition", {}, "У категории должно быть определение — по определению модель выбирает категорию."],
  ["category_duplicate", { name: "Работа" }, "Категория «Работа» уже есть."],
  ["draft_blank_title", {}, "У черновика должно быть имя."],
  ["draft_blank_definition", {}, "У черновика должно быть определение."],
  ["context_not_reopenable_state", {}, "Контекст не отмечен «не работа» — возвращать нечего."],
  ["context_not_applicable_by_position", { name: "заголовок" }, "Строка каталога размечена в Review как «заголовок» — её вид решается там."],
];

describe("подписи отказов открытия, категорий и «Вернуть в разбор» (спека 3б §2.12)", () => {
  it("каждый код таблицы §2.12 имеет непустую подпись, лишних кодов в словаре нет", () => {
    const expected = REFUSAL_TABLE.map(([code]) => code).sort();
    expect(Object.keys(DISCOVERY_REFUSAL_TEMPLATE).sort()).toEqual(expected);
    for (const code of expected) {
      expect(DISCOVERY_REFUSAL_TEMPLATE[code].trim().length).toBeGreaterThan(10);
    }
  });

  it.each(REFUSAL_TABLE)("%s — текст таблицы с подстановкой значений экрана", (code, values, text) => {
    expect(discoveryRefusalLabel(code, values)).toBe(text);
  });

  it("нет значения для подстановки — текст сервера, а не «undefined»", () => {
    expect(discoveryRefusalLabel("category_in_use", {}, "Категорию «Охрана» носят 2 семей — удалить можно только пустую.")).toBe(
      "Категорию «Охрана» носят 2 семей — удалить можно только пустую."
    );
  });

  it("значение null (ключ контекста отказа пуст) считается отсутствующим — текст сервера", () => {
    // Ревью задачи 5: `null` — отдельная ветвь предиката «нет значения», у неё свой вход.
    expect(discoveryRefusalLabel("draft_not_open", { name: null }, "текст сервера")).toBe("текст сервера");
  });

  it("нет значения и нет текста сервера — «…» на месте пропущенного", () => {
    expect(discoveryRefusalLabel("draft_not_open")).toBe(
      "Черновик «…» уже активирован, слит, отброшен или устарел. Слитый и отброшенный можно вернуть."
    );
  });

  it("код без подстановок не требует значений и не берёт текст сервера", () => {
    expect(discoveryRefusalLabel("activate_without_category", {}, "сырой текст")).toBe("Сначала выберите категорию семьи.");
  });

  it("неизвестный и пустой код — общая подпись", () => {
    const fallback = "Не удалось выполнить действие. Обновите экран и повторите.";
    expect(discoveryRefusalLabel("some_future_code")).toBe(fallback);
    expect(discoveryRefusalLabel(undefined)).toBe(fallback);
  });

  it("подпись отказа context_not_applicable нейтральна: размеченные в Review строки получают тот же код", () => {
    expect(contextRefusalLabel("context_not_applicable")).toBe("Вид неприменим: контекст не работа.");
  });

  it("виды строк, которые размечает Review, — ровно три, у каждого своя подпись", () => {
    expect(CATALOG_REVIEW_KIND_LABEL).toEqual({
      HEADER: "заголовок",
      LOT_HEADER: "заголовок лота",
      TRASH: "мусор",
    });
  });

  it("число имён строки открытия: прочерк у неизвестного, ноль остаётся нулём", () => {
    expect(discoveryNamesLabel(120)).toBe("имён: 120");
    expect(discoveryNamesLabel(0)).toBe("имён: 0");
    expect(discoveryNamesLabel(null)).toBe("имён: —");
  });
});
