import { AxiosError } from "axios";
import { describe, expect, it } from "vitest";

import { apiErrorCode, apiErrorContext, apiErrorDetail, apiErrorStatus } from "./queries";

function errorWith(status: number, data: unknown): AxiosError {
  const error = new AxiosError("Request failed");
  // @ts-expect-error — собираем минимальный ответ, полный AxiosResponse тут не нужен
  error.response = { status, data };
  return error;
}

describe("apiErrorDetail", () => {
  it("читает строковый detail доменного отказа", () => {
    const message = "Договор с таким номером уже есть.";
    expect(apiErrorDetail(errorWith(409, { detail: message }))).toBe(message);
  });

  it("читает detail-СПИСОК ошибки валидации Pydantic", () => {
    /*
     * Найдено собственным ревью фазы 5. FastAPI кладёт ошибки валидации в
     * `detail` списком, и сообщение лежит в `msg` с приставкой «Value error, ».
     * Пока разбиралась только строка, все тексты, написанные в валидаторах, до
     * человека не доходили — он видел «Request failed with status code 422».
     */
    const error = errorWith(422, {
      detail: [
        {
          type: "value_error",
          loc: ["body", "total_amount"],
          msg: 'Value error, Сумму передавайте строкой (например "1234.56"), а не числом с плавающей точкой: float теряет копейки.',
        },
      ],
    });
    expect(apiErrorDetail(error)).toBe(
      'Сумму передавайте строкой (например "1234.56"), а не числом с плавающей точкой: float теряет копейки.'
    );
  });

  it("склеивает несколько ошибок валидации", () => {
    const error = errorWith(422, {
      detail: [
        { msg: "Value error, Первая причина." },
        { msg: "Value error, Вторая причина." },
      ],
    });
    expect(apiErrorDetail(error)).toBe("Первая причина.; Вторая причина.");
  });

  it("не выдумывает текст, когда его нет", () => {
    expect(apiErrorDetail(errorWith(500, {}))).toBeUndefined();
    expect(apiErrorDetail(errorWith(422, { detail: [] }))).toBeUndefined();
    expect(apiErrorDetail(errorWith(422, { detail: [{ loc: ["body"] }] }))).toBeUndefined();
    expect(apiErrorDetail(new Error("сетевой сбой"))).toBeUndefined();
  });
});

describe("apiErrorStatus", () => {
  it("отдаёт код ответа, по которому экран различает развилки", () => {
    // 409 при загрузке — предложение заменить; 410 при скачивании — файл удалён
    // ретенцией. Оба решения принимаются по этому числу.
    expect(apiErrorStatus(errorWith(409, {}))).toBe(409);
    expect(apiErrorStatus(errorWith(410, {}))).toBe(410);
    expect(apiErrorStatus(new Error("нет ответа"))).toBeUndefined();
  });
});

describe("apiErrorDetail: третья форма detail — объект кодированного отказа", () => {
  /*
   * Спека инфляции §2.12: доменный отказ с кодом кладёт в `detail` ОБЪЕКТ
   * `{code, message, ...контекст}`. Пока разбирались только строка и список,
   * тост печатал бы `[object Object]` — то есть человек не увидел бы ни причины,
   * ни недостающих годов.
   */
  const refusal = {
    detail: {
      code: "missing_inflation_years",
      message: "Не заданы коэффициенты за годы: 2024, 2026.",
      missing_years: [2024, 2026],
    },
  };

  it("берёт message, а не сериализует объект", () => {
    expect(apiErrorDetail(errorWith(422, refusal))).toBe(
      "Не заданы коэффициенты за годы: 2024, 2026."
    );
  });

  it("отдаёт код, по которому экран выбирает ПОВЕДЕНИЕ, а не только текст", () => {
    // У `missing_inflation_years` баннер даёт кнопку «Заполнить недостающие
    // годы»; у `amendment_date_missing` кнопки нет — правкой ряда это не
    // лечится. Различить их можно только по коду.
    expect(apiErrorCode(errorWith(422, refusal))).toBe("missing_inflation_years");
    expect(
      apiErrorCode(
        errorWith(422, {
          detail: {
            code: "amendment_date_missing",
            message: "У допсоглашений нет собственной даты подготовки: ГП-0007 ДС №1.",
            estimate_ids: [7],
          },
        })
      )
    ).toBe("amendment_date_missing");
  });

  it("отдаёт контекст ключами РЯДОМ с code, а не вложенным узлом", () => {
    const context = apiErrorContext<{ missing_years: number[] }>(errorWith(422, refusal));
    expect(context?.missing_years).toEqual([2024, 2026]);
  });

  it("не путает объектный отказ с двумя прежними формами", () => {
    // Строка и список кодов не несут — иначе баннер отказа показался бы там, где
    // сервер просто отверг форму запроса.
    expect(apiErrorCode(errorWith(409, { detail: "Ряд индексов с таким названием уже есть." }))).toBeUndefined();
    expect(apiErrorCode(errorWith(422, { detail: [{ msg: "Value error, Причина." }] }))).toBeUndefined();
    expect(apiErrorContext(errorWith(500, {}))).toBeUndefined();

    // И наоборот: прежние формы читаются по-прежнему, до символа.
    expect(apiErrorDetail(errorWith(409, { detail: "Строковый отказ." }))).toBe("Строковый отказ.");
    expect(apiErrorDetail(errorWith(422, { detail: [{ msg: "Value error, Причина." }] }))).toBe(
      "Причина."
    );
  });

  it("не принимает за кодированный отказ объект без code или без message", () => {
    // Форма контракта фиксирована: без обоих полей это не кодированный отказ, и
    // выдавать его за таковой значило бы показать баннер по чужому телу.
    expect(apiErrorCode(errorWith(422, { detail: { message: "без кода" } }))).toBeUndefined();
    expect(apiErrorCode(errorWith(422, { detail: { code: "без сообщения" } }))).toBeUndefined();
    expect(apiErrorDetail(errorWith(422, { detail: { message: "без кода" } }))).toBeUndefined();
  });
});
