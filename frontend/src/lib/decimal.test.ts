/**
 * @vitest-environment node
 *
 * Тесты чистых функций: DOM здесь не наблюдается ни разу, а jsdom стоит около
 * секунды на файл. Выигрыш — на ТОЧЕЧНОМ прогоне (эти девять файлов: 9,1 с →
 * 3,8 с), а НЕ на полном наборе: там окружения поднимаются параллельно, и
 * 42,3 с накопленного `environment` уходят в тень тяжёлых компонентных файлов
 * (замер 2026-09-02: 98,6 с до, 100,2 с после — в пределах разброса). Признак
 * «нужен ли jsdom» объявляется ФАЙЛОМ, а не глобом в конфиге: глоб пришлось бы
 * держать в синхронизации с деревом, и молчаливое возвращение файла в jsdom
 * заметить было бы нечем.
 */
import { describe, expect, it } from "vitest";

import {
  addDecimalStrings,
  compareDecimalStrings,
  multiplyDecimalStrings,
  normalizeDecimalInput,
} from "./decimal";
import { formatDecimalMoney, roundDecimal } from "./format";

/** Неразрывный пробел: им `formatDecimalMoney` группирует разряды, как ru-RU. */
const NBSP = " ";

describe("multiplyDecimalStrings", () => {
  it("умножает точно там, где float ошибается", () => {
    // Пары подобраны замером, а не на слух: промах float выборочный, и на
    // «1000.33 × 1.075» его нет вовсе — тем он и опасен.
    expect(multiplyDecimalStrings("1000.10", "1.07")).toBe("1070.107");
    expect(Number("1000.10") * Number("1.07")).toBe(1070.1070000000002);

    expect(multiplyDecimalStrings("550.55", "1.043")).toBe("574.22365");
    expect(Number("550.55") * Number("1.043")).toBe(574.2236499999999);
  });

  it("не теряет разряды на больших суммах", () => {
    // 2^53 уже не влезает в double без потерь — BigInt влезает.
    expect(multiplyDecimalStrings("12345678901234567890", "2")).toBe("24691357802469135780");
  });

  it("убирает незначащие нули дробной части", () => {
    expect(multiplyDecimalStrings("10.50", "2")).toBe("21");
    expect(multiplyDecimalStrings("0.10", "0.10")).toBe("0.01");
  });

  it("сохраняет знак, но не даёт минус нуля", () => {
    expect(multiplyDecimalStrings("-2.5", "4")).toBe("-10");
    expect(multiplyDecimalStrings("-2.5", "-4")).toBe("10");
    expect(multiplyDecimalStrings("-0", "5")).toBe("0");
  });

  it("отказывается от нечисловых входов, а не выдумывает результат", () => {
    expect(multiplyDecimalStrings("", "2")).toBeNull();
    expect(multiplyDecimalStrings("1,5", "2")).toBeNull();
    expect(multiplyDecimalStrings("1e3", "2")).toBeNull();
  });
});

describe("addDecimalStrings", () => {
  it("складывает точно там, где Number промахивается", () => {
    // Предпосылка проверяется ВНУТРИ теста: промах float выборочный, и её слом
    // должен быть виден как падение, а не как молчание (false-test-premises).
    expect(Number("0.1") + Number("0.2")).not.toBe(0.3);
    expect(addDecimalStrings("0.1", "0.2")).toBe("0.3");
  });

  it("выравнивает разные масштабы", () => {
    /*
      Реалистичная пара площадей. Замерено: `Number("62399.7") + Number("13341.3")`
      даёт РОВНО 75741 — то есть именно на ней float не промахивается, и
      утверждать здесь `not.toBe` нельзя, тест покраснел бы на верной реализации.
      Записано, чтобы пару «поближе к домену» не перенесли в тест предпосылки
      выше: точная функция нужна не из-за этих чисел, а из-за класса чисел.
    */
    expect(Number("62399.7") + Number("13341.3")).toBe(75741);
    expect(addDecimalStrings("62399.7", "13341.30")).toBe("75741");
  });

  it("складывает целые", () => {
    expect(addDecimalStrings("100", "23")).toBe("123");
  });

  it("ноль слагаемым не мешает", () => {
    expect(addDecimalStrings("100.50", "0")).toBe("100.5");
  });

  it("возвращает null на не-числе", () => {
    expect(addDecimalStrings("сто", "1")).toBeNull();
    expect(addDecimalStrings("", "1")).toBeNull();
    expect(addDecimalStrings("1,5", "1")).toBeNull();
  });
});

describe("compareDecimalStrings", () => {
  it("сравнивает при ОБОИХ отрицательных — то, на чём ломалась склейка минуса", () => {
    /*
     * Ровно случай внешнего ревью: −100 → −80 это РОСТ, −100 → −120 это
     * СНИЖЕНИЕ. Прежний приём `addDecimalStrings(a, `-${b}`)` давал на этих
     * входах `--100`, разбор отказывал, и оба случая становились
     * неразличимы — «не смог сравнить».
     */
    expect(compareDecimalStrings("-80", "-100")).toBe(1);
    expect(compareDecimalStrings("-120", "-100")).toBe(-1);
    expect(compareDecimalStrings("-100", "-100")).toBe(0);
  });

  it("склейка минуса, от которой избавились, действительно не работала", () => {
    /*
     * Предпосылка утверждения выше, проверенная НЕ через проверяемый механизм:
     * если `addDecimalStrings` однажды начнёт принимать `--100`, этот тест
     * покраснеет и скажет, что довод устарел.
     */
    expect(addDecimalStrings("-80", "--100")).toBeNull();
  });

  it("знаки в разные стороны и ноль", () => {
    expect(compareDecimalStrings("1", "-1")).toBe(1);
    expect(compareDecimalStrings("-1", "1")).toBe(-1);
    expect(compareDecimalStrings("0", "-0")).toBe(0);
    expect(compareDecimalStrings("-0.00", "0")).toBe(0);
  });

  it("разные масштабы выравниваются, а не сравниваются как текст", () => {
    // Как текст «9» больше «10», и лексикографическое сравнение соврало бы.
    expect(compareDecimalStrings("9", "10")).toBe(-1);
    expect(compareDecimalStrings("100.10", "100.1")).toBe(0);
    expect(compareDecimalStrings("100.100000001", "100.1")).toBe(1);
    expect(compareDecimalStrings("-100.100000001", "-100.1")).toBe(-1);
  });

  it("не теряет разряды на суммах, где float уже врёт", () => {
    // Пара за пределом 15 значащих цифр double: `Number` склеил бы её в одно.
    expect(compareDecimalStrings("12345678901234567.89", "12345678901234567.88")).toBe(1);
    expect(Number("12345678901234567.89") === Number("12345678901234567.88")).toBe(true);
  });

  it("не десятичное число — `null`, а не догадка", () => {
    expect(compareDecimalStrings("--100", "1")).toBeNull();
    expect(compareDecimalStrings("1", "abc")).toBeNull();
    expect(compareDecimalStrings("", "1")).toBeNull();
    expect(compareDecimalStrings("1e3", "1")).toBeNull();
  });
});

describe("formatDecimalMoney", () => {
  it("форматирует строку, не переводя её в число", () => {
    expect(formatDecimalMoney("1234567890.12", "")).toBe(`1${NBSP}234${NBSP}567${NBSP}890,12`);
  });

  it("сохраняет разряды, которых double уже не держит", () => {
    // Замер: Number("12345678901234567.89") === 12345678901234568 — младшие
    // разряды и копейки исчезают. Форматирование строкой их сохраняет.
    expect(String(Number("12345678901234567.89"))).toBe("12345678901234568");
    expect(formatDecimalMoney("12345678901234567.89", "")).toBe(
      `12${NBSP}345${NBSP}678${NBSP}901${NBSP}234${NBSP}567,89`
    );
  });

  it("не округляет и не обрезает дробную часть", () => {
    // Утверждённая ставка 1075.35475 — показать «1 075,35» значило бы соврать
    // о цифре, по которой идёт торг.
    expect(formatDecimalMoney("1075.35475", "")).toBe(`1${NBSP}075,35475`);
  });

  it("дополняет до двух знаков по денежной привычке", () => {
    expect(formatDecimalMoney("10.5", "")).toBe("10,50");
    expect(formatDecimalMoney("10", "")).toBe("10,00");
  });

  it("пустое значение — прочерк, а не ноль", () => {
    expect(formatDecimalMoney(null)).toBe("—");
    expect(formatDecimalMoney(undefined)).toBe("—");
    expect(formatDecimalMoney("")).toBe("—");
  });

  it("неожидаемый формат отдаёт как есть, а не прячет", () => {
    expect(formatDecimalMoney("не число", "")).toBe("не число");
  });

  it("добавляет знак валюты неразрывным пробелом", () => {
    expect(formatDecimalMoney("1000")).toBe(`1${NBSP}000,00${NBSP}₽`);
  });

  it("отбрасывает хвостовые нули за копейками", () => {
    /*
      Деление `numeric` в PostgreSQL доводит результат до своей шкалы, поэтому
      средневзвешенная ставка §6 приезжает с десятком нулей на конце. Обрезка нулей
      — не округление: 32263577.210000000000 и 32263577.21 равны. На стенде матрица
      без этого читалась как набор случайных цифр.
    */
    expect(formatDecimalMoney("32263577.210000000000", "")).toBe(
      `32${NBSP}263${NBSP}577,21`
    );
    // Значащие цифры при этом целы — обрезаются только нули.
    expect(formatDecimalMoney("1075.35475000", "")).toBe(`1${NBSP}075,35475`);
    // Копейки не съедаются, даже если они нули.
    expect(formatDecimalMoney("100.00", "")).toBe("100,00");
    expect(formatDecimalMoney("100.500000", "")).toBe("100,50");
  });

  it("округляет только по явной просьбе, и целыми числами", () => {
    // Артефакт деления: показывать двенадцать знаков — изображать точность,
    // которой нет. Но округление всегда осознанное: без параметра оно не
    // происходит (см. тест «не округляет и не обрезает» выше).
    expect(formatDecimalMoney("49467.503222935929", "", 2)).toBe(`49${NBSP}467,50`);
    expect(formatDecimalMoney("66121.329021664522", "", 2)).toBe(`66${NBSP}121,33`);
  });
});

describe("roundDecimal", () => {
  it("округляет половину от нуля", () => {
    expect(roundDecimal("2.345", 2)).toBe("2.35");
    expect(roundDecimal("-2.345", 2)).toBe("-2.35");
  });

  it("не трогает значение, у которого знаков не больше нужного", () => {
    expect(roundDecimal("2.3", 2)).toBe("2.3");
    expect(roundDecimal("2", 2)).toBe("2");
  });

  it("переносит разряд", () => {
    expect(roundDecimal("9.999", 2)).toBe("10.00");
  });

  it("не даёт «минус ноль»", () => {
    // «-0,00» как ставка — артефакт округления, а не значение.
    expect(roundDecimal("-0.001", 2)).toBe("0.00");
  });

  it("не зависит от двоичного представления", () => {
    // Те же границы, на которых расходится float (см. percent.test.ts): здесь
    // «только для показа» не оправдывает Number() — §3 запрещает его для денег.
    expect(roundDecimal("0.145", 2)).toBe("0.15");
    expect(Number("0.145").toFixed(2)).toBe("0.14"); // замер: double врёт
  });

  it("огромное значение не теряет разрядов", () => {
    expect(roundDecimal("12345678901234567.891", 2)).toBe("12345678901234567.89");
  });
});

describe("normalizeDecimalInput", () => {
  it("принимает запятую как десятичный разделитель", () => {
    // Русская раскладка даёт «1234,56» естественнее, чем «1234.56». Без этого
    // сервер отвечал бы ошибкой валидации Pydantic — по-английски и не о том.
    expect(normalizeDecimalInput("1234,56")).toBe("1234.56");
  });

  it("убирает пробелы, включая неразрывный", () => {
    expect(normalizeDecimalInput("1 234 567,89")).toBe("1234567.89");
    expect(normalizeDecimalInput(`1${NBSP}234${NBSP}567.89`)).toBe("1234567.89");
  });

  it("не трогает строку, где запятая — разделитель разрядов", () => {
    // «1,234.56» слепая замена превратила бы в «1.234.56», то есть в мусор.
    // Отдаём как есть: пусть отказывает сервер, а не мы молча искажаем ввод.
    expect(normalizeDecimalInput("1,234.56")).toBe("1,234.56");
  });

  it("оставляет уже правильную строку без изменений", () => {
    expect(normalizeDecimalInput("1075.35475")).toBe("1075.35475");
    expect(normalizeDecimalInput("")).toBe("");
  });
});
