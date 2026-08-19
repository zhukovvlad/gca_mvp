import { describe, expect, it } from "vitest";

import { coefficientLevel } from "./inflation";

/*
 * Расшифровка коэффициента — защита от ошибки ×13 (спека §2.4, DoD 25).
 *
 * Эти тесты доказывают только то, что функция УМЕЕТ СЧИТАТЬ. Защита существует
 * лишь тогда, когда расшифровка ПОКАЗАНА рядом с полем ввода, и это проверяет
 * компонентный тест `InflationSeriesDialog.test.tsx`: снятие, которое может
 * случиться, — удаление вызова из компонента, и здешние тесты его переживают
 * зелёными по построению (`docs/insights/data-flow-assertions-for-order.md`).
 */
describe("coefficientLevel", () => {
  it("читает нормальный годовой коэффициент как рост", () => {
    const level = coefficientLevel("1.083");
    expect(level.text).toBe("Рост 8,3 %");
    expect(level.level).toBe("+8,3%");
    expect(level.tone).toBe("ok");
  });

  it("читает «прирост вместо коэффициента» как невероятное снижение", () => {
    /*
     * Ровно ошибка ×13: человек вводит `0.083`, имея в виду 8,3 % прироста. Схема
     * такое значение принимает (условие только `coefficient > 0`), и приведение
     * посчитало бы дефляцию на 91,7 % молча — на всех сравнениях сразу.
     */
    const level = coefficientLevel("0.083");
    expect(level.text).toBe("Снижение 91,7 %");
    expect(level.tone).toBe("bad");
  });

  it("держит один знак после запятой", () => {
    expect(coefficientLevel("1.15").text).toBe("Рост 15,0 %");
    expect(coefficientLevel("1.1234").text).toBe("Рост 12,3 %");
  });

  it("считает точно, без float", () => {
    // 1.07 − 1 = 0.07 ровно. Через `Number` вышло бы 0.07000000000000006, и на
    // границе округления это давало бы другой последний знак.
    expect(coefficientLevel("1.07").level).toBe("+7,0%");
    expect(coefficientLevel("1.005").level).toBe("+0,5%");
  });

  it("законное снижение цен НЕ красное — красным помечается невероятный уровень", () => {
    // Спека §2.4 запрещает искусственный диапазон допустимых значений: он отверг
    // бы законный год высокой инфляции. Тон — подсказка о правдоподобии, а не
    // проверка.
    expect(coefficientLevel("0.98").tone).toBe("ok");
    expect(coefficientLevel("0.98").text).toBe("Снижение 2,0 %");
    expect(coefficientLevel("1.99").tone).toBe("ok");
    expect(coefficientLevel("2.5").tone).toBe("bad");
    expect(coefficientLevel("0.4").tone).toBe("bad");
  });

  it("единица — это «без изменения», а не «рост 0 %»", () => {
    const level = coefficientLevel("1");
    expect(level.text).toBe("Без изменения");
    expect(level.tone).toBe("muted");
  });

  it("пустой и неразбираемый вход дают прочерк, а не мусор", () => {
    for (const raw of ["", "   ", "abc", "-", ".", "1.2.3"]) {
      expect(coefficientLevel(raw)).toEqual({ level: "", text: "—", tone: "muted" });
    }
  });

  it("принимает запятую как десятичный разделитель", () => {
    // Человек набирает «1,083» на русской раскладке; `normalizeDecimalInput` это
    // уже умеет, и расшифровка обязана вести себя так же, иначе она молчала бы
    // ровно на самом частом вводе.
    expect(coefficientLevel("1,083").text).toBe("Рост 8,3 %");
  });
});
