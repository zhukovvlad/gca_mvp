import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { InflationSeriesDialog } from "@/components/inflation/InflationSeriesDialog";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { InflationSeries } from "@/types/domain";

/*
 * Окно правки ряда: ОДИН компонент на три входа (DoD 34) и живая расшифровка
 * коэффициента (DoD 25).
 *
 * **Главный тест здесь — про расшифровку рядом с полем.** Юнит-тест
 * `lib/inflation.test.ts` доказывает, что функция умеет считать; защита от ошибки
 * ×13 существует лишь тогда, когда результат ПОКАЗАН до сохранения. Снятие, которое
 * может случиться, — удаление вызова `coefficientLevel` из компонента, и юнит-тест
 * его переживает зелёным по построению (`docs/insights/data-flow-assertions-for-order.md`).
 */

const ACTIVE_SERIES: InflationSeries = {
  id: 1,
  name: "Росстат, ИПЦ, декабрь к декабрю",
  note: "официальная публикация, по РФ",
  is_active: true,
  year_from: 2024,
  year_to: 2025,
  value_count: 2,
  created_at: "2026-01-12T10:00:00+03:00",
  updated_at: "2026-01-12T10:00:00+03:00",
};

const ARCHIVED_SERIES: InflationSeries = { ...ACTIVE_SERIES, id: 2, is_active: false };

const SAVED_VALUES = [
  {
    year: 2024,
    coefficient: "1.0750",
    source: "бюллетень 01.2025",
    is_forecast: false,
    created_at: "2026-01-12T10:00:00+03:00",
    updated_at: "2026-01-12T10:00:00+03:00",
  },
  {
    year: 2025,
    coefficient: "1.0830",
    source: "бюллетень 01.2026",
    is_forecast: false,
    created_at: "2026-01-12T10:00:00+03:00",
    updated_at: "2026-01-12T10:00:00+03:00",
  },
];

function serveValues(values = SAVED_VALUES) {
  server.use(
    http.get("/api/v1/inflation-series/:id/values", () => HttpResponse.json(values))
  );
}

async function openWithSeries(series: InflationSeries | null, missingYears?: number[]) {
  const onOpenChange = vi.fn();
  renderWithProviders(
    <InflationSeriesDialog
      open
      target={series === null ? { mode: "create" } : { mode: "edit", series }}
      missingYears={missingYears}
      onOpenChange={onOpenChange}
    />
  );
  if (series !== null) {
    await waitFor(() =>
      expect(screen.getByLabelText(`Коэффициент за ${SAVED_VALUES[0].year}`)).toBeInTheDocument()
    );
  }
  return { onOpenChange };
}

describe("InflationSeriesDialog", () => {
  it("показывает расшифровку по ВВОДИМОМУ значению до сохранения (DoD 25)", async () => {
    /*
     * Ошибка ×13 буквально: человек вводит 0.083, имея в виду 8,3 % прироста.
     * Схема такое значение примет, и приведение посчитает дефляцию на 91,7 % —
     * молча и сразу на всех сравнениях. Видно это ТОЛЬКО здесь, до сохранения.
     */
    serveValues();
    await openWithSeries(ACTIVE_SERIES);

    const field = screen.getByLabelText("Коэффициент за 2025");
    await userEvent.clear(field);
    await userEvent.type(field, "0.083");

    expect(screen.getByTestId("level-2025")).toHaveTextContent("Снижение 91,7 %");

    await userEvent.clear(field);
    await userEvent.type(field, "1.083");
    expect(screen.getByTestId("level-2025")).toHaveTextContent("Рост 8,3 %");
  });

  it("подставляет сохранённые годы и их расшифровки при открытии", async () => {
    serveValues();
    await openWithSeries(ACTIVE_SERIES);

    expect(screen.getByLabelText("Коэффициент за 2024")).toHaveValue("1.0750");
    expect(screen.getByTestId("level-2024")).toHaveTextContent("Рост 7,5 %");
    expect(screen.getByTestId("level-2025")).toHaveTextContent("Рост 8,3 %");
  });

  it("режим создания — ТОТ ЖЕ компонент, и он шлёт POST", async () => {
    /*
     * Это первый и обязательный пользовательский путь: миграция создаёт справочник
     * ПУСТЫМ (§2.6), и без создания через интерфейс фича не заводится вовсе.
     */
    let posted: unknown = null;
    server.use(
      http.post("/api/v1/inflation-series", async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({ ...ACTIVE_SERIES, id: 7 }, { status: 201 });
      })
    );

    const { onOpenChange } = await openWithSeries(null);
    expect(screen.getByText("Новый ряд индексов")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Название"), "Внутренняя оценка ПЭО");
    await userEvent.type(screen.getByLabelText("Примечание"), "смета строительных ресурсов");
    await userEvent.click(screen.getByRole("button", { name: "Добавить год" }));
    await userEvent.type(screen.getByLabelText("Коэффициент за 2024"), "1.12");
    await userEvent.type(screen.getByLabelText("Источник за 2024"), "внутренний расчёт");

    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toEqual({
      name: "Внутренняя оценка ПЭО",
      note: "смета строительных ресурсов",
      values: [
        { year: 2024, coefficient: "1.12", source: "внутренний расчёт", is_forecast: false },
      ],
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("правка уходит ОДНИМ запросом: название, примечание и все годы вместе", async () => {
    /*
     * Отправлять их порознь значило бы допустить ряд, исправленный наполовину, — а
     * он общий и без версий, так что «наполовину» означает неверные числа у всех,
     * кто в этот момент смотрит сравнение (§2.12).
     */
    serveValues();
    let patched: unknown = null;
    let requests = 0;
    server.use(
      http.patch("/api/v1/inflation-series/:id", async ({ request }) => {
        requests += 1;
        patched = await request.json();
        return HttpResponse.json(ACTIVE_SERIES);
      })
    );

    await openWithSeries(ACTIVE_SERIES);
    const field = screen.getByLabelText("Коэффициент за 2025");
    await userEvent.clear(field);
    await userEvent.type(field, "1.0915");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(patched).not.toBeNull());
    expect(requests).toBe(1);
    expect(patched).toEqual({
      name: ACTIVE_SERIES.name,
      note: ACTIVE_SERIES.note,
      values: [
        { year: 2024, coefficient: "1.0750", source: "бюллетень 01.2025", is_forecast: false },
        { year: 2025, coefficient: "1.0915", source: "бюллетень 01.2026", is_forecast: false },
      ],
    });
  });

  it("вход из баннера открывает окно на НЕДОСТАЮЩИХ годах", async () => {
    serveValues([SAVED_VALUES[1]]);
    renderWithProviders(
      <InflationSeriesDialog
        open
        target={{ mode: "edit", series: ACTIVE_SERIES }}
        missingYears={[2026, 2024]}
        onOpenChange={vi.fn()}
      />
    );

    await waitFor(() =>
      expect(screen.getByLabelText("Коэффициент за 2026")).toBeInTheDocument()
    );

    // Годы добавлены ПУСТЫМИ и по возрастанию: заполнить их и есть смысл входа.
    expect(screen.getByLabelText("Коэффициент за 2024")).toHaveValue("");
    expect(screen.getByLabelText("Коэффициент за 2026")).toHaveValue("");
    // Сохранённый год не задублирован пустой строкой.
    expect(screen.getAllByLabelText(/^Коэффициент за 2025$/)).toHaveLength(1);
    // Пока годы пусты, сохранять нечего: неполный год сервер отверг бы 422.
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

  it("архивный ряд не правится, и окно говорит почему", async () => {
    serveValues();
    await openWithSeries(ARCHIVED_SERIES);

    expect(screen.getByText(/сначала верните его в активные/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Добавить год" })).toBeDisabled();
  });

  it("сохранённый год не предлагает удаления — его не существует", async () => {
    serveValues();
    await openWithSeries(ACTIVE_SERIES);

    // `DELETE` запрещён (§2.10), и кнопка у сохранённого года обещала бы операцию,
    // которой нет. У добавленной строки убрать её из ФОРМЫ можно: она ещё не ушла.
    expect(screen.queryByRole("button", { name: "Убрать строку 2024" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Добавить год" }));
    expect(screen.getByRole("button", { name: "Убрать строку 2026" })).toBeInTheDocument();
  });

  it("режим ПРАВКИ без полученного объекта показывает загрузку, а НЕ форму создания", async () => {
    /*
     * Найдено внешним ревью, дважды. Прежний контракт различал режимы по
     * `series === null`, и любой промах поиска молча становился созданием. Промах
     * ЗАКОНЕН: список рядов — отдельный запрос, он может ещё не разрешиться, когда
     * сравнение уже пришло, или упасть вовсе. `admin`, думая что правит ряд,
     * открывал бы пустую форму «Новый ряд индексов» и заводил дубликат.
     */
    serveValues();
    renderWithProviders(
      <InflationSeriesDialog
        open
        target={{ mode: "edit", series: null, reason: "pending" }}
        onOpenChange={vi.fn()}
      />
    );

    expect(await screen.findByText(/Загружаем ряд и его годы/)).toBeInTheDocument();
    expect(screen.queryByText("Новый ряд индексов")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Название")).not.toBeInTheDocument();
  });

  it("упавший запрос СПИСКА даёт отказ, а не бесконечную загрузку", async () => {
    /*
     * Третий круг внешнего ревью. `ready` отвечал только на «данные есть», поэтому
     * терминальная ошибка не отличалась от ожидания: `data` не появится никогда, а
     * окно обещало, что оно ещё грузится. Различить это само окно не может — запрос
     * списка принадлежит вызывающему, — поэтому причину отсутствия объекта заявляет
     * он, тем же приёмом, каким заявляет режим.
     */
    serveValues();
    const onOpenChange = vi.fn();
    renderWithProviders(
      <InflationSeriesDialog
        open
        target={{ mode: "edit", series: null, reason: "failed" }}
        onOpenChange={onOpenChange}
      />
    );

    expect(await screen.findByText(/Не удалось загрузить ряд и его годы/)).toBeInTheDocument();
    expect(screen.queryByText(/Загружаем ряд и его годы/)).not.toBeInTheDocument();
    // Формы нет ни в виде правки, ни в виде создания: она показала бы ряд без годов.
    expect(screen.queryByLabelText("Название")).not.toBeInTheDocument();
    expect(screen.queryByText("Новый ряд индексов")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("упавший запрос ГОДОВ ряда тоже даёт отказ — ревью его не назвало", async () => {
    /*
     * ВТОРОЙ путь того же выражения, и он отличается от первого владельцем запроса:
     * годы ряда запрашивает само окно, поэтому `values.isError` оно обязано читать
     * само, а не ждать пропа. Объект ряда здесь ПОЛУЧЕН — то есть первое условие
     * `ready` выполнено, и без этой ветки окно висело бы на «Загружаем…» при живом
     * ряде на экране.
     */
    server.use(
      http.get("/api/v1/inflation-series/:id/values", () => new HttpResponse(null, { status: 500 }))
    );
    renderWithProviders(
      <InflationSeriesDialog
        open
        target={{ mode: "edit", series: ACTIVE_SERIES }}
        onOpenChange={vi.fn()}
      />
    );

    expect(await screen.findByText(/Не удалось загрузить ряд и его годы/)).toBeInTheDocument();
    expect(screen.queryByText(/Загружаем ряд и его годы/)).not.toBeInTheDocument();
    // Пустая форма с сохранёнными годами, которых не видно, — хуже отказа: правка
    // выглядела бы как их потеря.
    expect(screen.queryByLabelText("Название")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
  });

  it("год добавленной строки редактируется целиком, не теряя фокус", async () => {
    /*
     * Ключом строки был сам год, и при первом введённом символе React размонтировал
     * строку — поле теряло фокус, ввести год целиком было невозможно. Прежние тесты
     * этого не поймали: они нажимали «Добавить год» и правили только коэффициент с
     * источником, оставляя год посчитанным. Найдено внешним ревью.
     */
    serveValues();
    await openWithSeries(ACTIVE_SERIES);

    await userEvent.click(screen.getByRole("button", { name: "Добавить год" }));
    // Добавленная строка получает следующий год за максимальным — 2026.
    const yearField = screen.getByLabelText("Год строки 3");
    await userEvent.clear(yearField);
    await userEvent.type(yearField, "2031");

    expect(yearField).toHaveValue("2031");
    // Строка не пересоздалась: фокус остался в поле, куда шёл ввод.
    expect(yearField).toHaveFocus();
    // Соседние поля той же строки на месте и связаны с новым годом.
    expect(screen.getByLabelText("Коэффициент за 2031")).toBeInTheDocument();
  });

  it("пустой год блокирует сохранение, а не превращается в ноль", async () => {
    // `Number("") || 0` показывал «0» сразу после стирания, то есть стереть год и
    // набрать другой было нельзя.
    serveValues();
    await openWithSeries(ACTIVE_SERIES);

    await userEvent.click(screen.getByRole("button", { name: "Добавить год" }));
    const yearField = screen.getByLabelText("Год строки 3");
    await userEvent.clear(yearField);

    expect(yearField).toHaveValue("");
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

  it("окно в DOM одно, сколько бы входов его ни открывало (DoD 34)", async () => {
    serveValues();
    await openWithSeries(ACTIVE_SERIES);

    const dialogs = document.querySelectorAll("[role=dialog]");
    expect(dialogs).toHaveLength(1);
    expect(within(dialogs[0] as HTMLElement).getByText("Изменить ряд индексов")).toBeInTheDocument();
  });
});
