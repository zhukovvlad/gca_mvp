import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HighlightedText } from "./HighlightedText";
import { findMatchRanges } from "./matchRanges";

function marks(): string[] {
  return Array.from(document.querySelectorAll("mark")).map((m) => m.textContent ?? "");
}

describe("findMatchRanges", () => {
  it("находит слово без учёта регистра и возвращает диапазон в исходной строке", () => {
    const text = "Монтаж ООО Ромашка корпус";
    const [range] = findMatchRanges(text, ["ромашка"]);
    expect(text.slice(range.from, range.to)).toBe("Ромашка");
  });

  it("кавычки видимой строки не мешают: слово словаря хранится без них", () => {
    const text = "Монтаж ООО «Ромашка» корпус";
    const [range] = findMatchRanges(text, ["ромашка"]);
    expect(text.slice(range.from, range.to)).toBe("Ромашка");
  });

  it("слово из двух слов находится через кавычку и двойной пробел", () => {
    const text = "Стены ЖК  «Северный» секция";
    const [range] = findMatchRanges(text, ["жк северный"]);
    expect(text.slice(range.from, range.to)).toBe("ЖК  «Северный");
  });

  it("слово целиком подсвечивается, те же буквы внутри длинного слова — нет", () => {
    const whole = "Монтаж Ромашка корпус";
    const [range] = findMatchRanges(whole, ["ромашка"]);
    expect(whole.slice(range.from, range.to)).toBe("Ромашка");
    expect(findMatchRanges("Ромашкам и ультраромашка", ["ромашка"])).toEqual([]);
  });

  it("слово рядом с цифрой и знаком: цифра — часть слова, запятая — граница", () => {
    expect(findMatchRanges("ромашка2", ["ромашка"])).toEqual([]);
    expect(findMatchRanges("ромашка, корпус", ["ромашка"])).toHaveLength(1);
  });

  it("длинное слово внутри не мешает найти целое дальше", () => {
    const text = "ромашкам и ромашка";
    const ranges = findMatchRanges(text, ["ромашка"]);
    expect(ranges).toHaveLength(1);
    expect(text.slice(ranges[0].from, ranges[0].to)).toBe("ромашка");
    expect(ranges[0].from).toBe(11);
  });

  it("отвергнутое вхождение не съедает перекрывающее его целое: поиск шагает на символ", () => {
    // «бета бета» с 5-й позиции прилипла к «альфа»; целое вхождение начинается с 10-й,
    // внутри отвергнутого, — шаг на длину слова его пропустил бы.
    const text = "альфабета бета бета";
    const ranges = findMatchRanges(text, ["бета бета"]);
    expect(ranges).toEqual([{ from: 10, to: 19 }]);
  });

  it("организационная форма посередине имени не мешает: запись словаря хранится без неё", () => {
    const text = "Кладка Ромашка ООО Сервис стен";
    const [range] = findMatchRanges(text, ["ромашка сервис"]);
    expect(text.slice(range.from, range.to)).toBe("Ромашка ООО Сервис");
  });

  it("две формы подряд и форма в кавычках посередине имени подсвечиваются целиком", () => {
    const two = "Ромашка ООО ТОО Сервис";
    const [a] = findMatchRanges(two, ["ромашка сервис"]);
    expect(two.slice(a.from, a.to)).toBe(two);
    const quoted = "Ромашка «ООО» Сервис";
    const [b] = findMatchRanges(quoted, ["ромашка сервис"]);
    expect(quoted.slice(b.from, b.to)).toBe("Ромашка «ООО» Сервис");
  });

  it("между словами не форма или слово, лишь начинающееся с формы, — не совпадение", () => {
    expect(findMatchRanges("Ромашка Плюс Сервис", ["ромашка сервис"])).toEqual([]);
    expect(findMatchRanges("Ромашка ООО1 Сервис", ["ромашка сервис"])).toEqual([]);
  });

  it.each([
    "Ромашка (ООО) Сервис",
    "Ромашка ООО, Сервис",
    "Ромашка-Сервис",
  ])("знак и форма посередине имени не мешают: %s", (text) => {
    const [range] = findMatchRanges(text, ["ромашка сервис"]);
    expect(text.slice(range.from, range.to)).toBe(text);
  });

  it("слитное написание без разделителя — не совпадение", () => {
    expect(findMatchRanges("Ромашкасервис", ["ромашка сервис"])).toEqual([]);
  });

  it("запись «ромашка» подсвечивается в «Ромашка, ТОО» без формы", () => {
    const text = "Ромашка, ТОО";
    const [range] = findMatchRanges(text, ["ромашка"]);
    expect(text.slice(range.from, range.to)).toBe("Ромашка");
  });

  it("номер договора ищется буквально: знак вместо пробела не совпадение", () => {
    expect(findMatchRanges("Договор 12-б", [{ text: "12 б", kind: "contract" }])).toEqual([]);
    expect(findMatchRanges("Договор 12 б", [{ text: "12 б", kind: "contract" }])).toHaveLength(1);
    expect(findMatchRanges("Договор 12-б", [{ text: "12 б", kind: "contractor" }])).toHaveLength(1);
  });

  it("номер тендера ищется буквально, как номер договора", () => {
    expect(findMatchRanges("Тендер 12-б", [{ text: "12 б", kind: "tender" }])).toEqual([]);
  });

  it("сохранённая запись имени со знаком ищется по словам, как на сервере", () => {
    // Совпадения задержанного задания хранятся текстом записи на момент захвата;
    // запись, собранная прежней нормализацией, может нести знак («ромашка,»).
    const text = "Кладка Ромашка ТОО стен";
    const [range] = findMatchRanges(text, [{ text: "ромашка,", kind: "contractor" }]);
    expect(text.slice(range.from, range.to)).toBe("Ромашка");
  });

  it("запись имени без единой буквы или цифры не даёт диапазонов", () => {
    expect(findMatchRanges("Кладка — стен", [{ text: "—", kind: "contractor" }])).toEqual([]);
  });

  it("два вхождения одного слова — два диапазона", () => {
    expect(findMatchRanges("ромашка и Ромашка", ["ромашка"])).toHaveLength(2);
  });

  it("пересекающиеся совпадения сливаются в один диапазон", () => {
    const text = "жк северный парк";
    const ranges = findMatchRanges(text, ["жк северный", "северный парк"]);
    expect(ranges).toHaveLength(1);
    expect(text.slice(ranges[0].from, ranges[0].to)).toBe("жк северный парк");
  });

  it("отсутствие совпадений даёт пустой список", () => {
    expect(findMatchRanges("Монтаж", ["ромашка"])).toEqual([]);
  });
});

/**
 * Пустое слово даёт `indexOf("") === позиция` без продвижения — синхронный
 * бесконечный цикл, который таймаут теста не прерывает. Счётчик вызовов
 * `indexOf` превращает зависание в отказ.
 */
function withIndexOfBudget<T>(run: () => T): T {
  const original = String.prototype.indexOf;
  let calls = 0;
  const spy = vi.spyOn(String.prototype, "indexOf").mockImplementation(function (
    this: string,
    search: string,
    position?: number
  ) {
    calls += 1;
    if (calls > 10_000) throw new Error("поиск не продвигается по строке");
    return original.call(this, search, position);
  });
  try {
    return run();
  } finally {
    spy.mockRestore();
  }
}

describe("findMatchRanges — пустое слово", () => {
  it("пустое слово не зацикливает поиск и не даёт диапазонов", () => {
    expect(withIndexOfBudget(() => findMatchRanges("Монтаж", ["  ", ""]))).toEqual([]);
    expect(
      withIndexOfBudget(() => findMatchRanges("Монтаж ромашка", ["", "   ", "ромашка"]))
    ).toHaveLength(1);
  });
});

describe("HighlightedText", () => {
  it("оборачивает совпавшее в mark, остальной текст оставляет как есть", () => {
    render(
      <p data-testid="line">
        <HighlightedText text="Монтаж ООО «Ромашка» корпус 2" needles={["ромашка"]} />
      </p>
    );
    expect(marks()).toEqual(["Ромашка"]);
    expect(screen.getByTestId("line")).toHaveTextContent("Монтаж ООО «Ромашка» корпус 2");
  });

  it("разметка внутри данных не превращается в элементы", () => {
    render(
      <p data-testid="line">
        <HighlightedText text="<b>жирный</b> ромашка" needles={["ромашка"]} />
      </p>
    );
    expect(document.querySelector("b")).toBeNull();
    expect(screen.getByTestId("line")).toHaveTextContent("<b>жирный</b> ромашка");
    expect(marks()).toEqual(["ромашка"]);
  });

  it("без совпадений mark нет", () => {
    render(<HighlightedText text="Монтаж вентиляции" needles={["ромашка"]} />);
    expect(marks()).toEqual([]);
  });
});
