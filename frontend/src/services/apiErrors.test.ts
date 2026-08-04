import { AxiosError } from "axios";
import { describe, expect, it } from "vitest";

import { apiErrorDetail, apiErrorStatus } from "./queries";

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
