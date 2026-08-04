import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { http, HttpResponse } from "msw";

import PassportPage from "./PassportPage";
import { longJobTitle, samplePassport } from "@/test/fixtures";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

/**
 * Паспорт объекта (§7.4, DoD §10).
 *
 * Покрывается **состав** паспорта — поля, топ-N, подсветка отклонений, — а не
 * раскладка: `@media print` в jsdom не проверяем, и вёрстку А4 смотрит глаз (§7
 * брифинга фазы 6). Прогон на живом стенде — часть DoD, а не дополнение.
 */

/**
 * Страница читает `contractId` из `useParams`, поэтому её обязательно монтировать
 * внутри `Routes` с тем же шаблоном пути, что в `App.tsx`. Без `Routes` параметры
 * пусты, запрос не уходит вовсе, и экран навсегда остаётся скелетоном — первая
 * редакция этих тестов падала именно так, все четырнадцать.
 */
function renderPassport() {
  return renderWithProviders(
    <Routes>
      <Route path="/contracts/:contractId/passport" element={<PassportPage />} />
    </Routes>,
    { initialRoute: "/contracts/10/passport" }
  );
}

describe("Паспорт объекта", () => {
  it("показывает все реквизиты, которых требует DoD", async () => {
    renderPassport();

    // §1 пункт 2: номер договора, подписант, дата, сумма, класс.
    expect(await screen.findByText("ГП-0114")).toBeInTheDocument();
    expect(screen.getByText("Петров П.П.")).toBeInTheDocument();
    expect(screen.getByText("01.03.2025")).toBeInTheDocument();
    expect(screen.getByText("Жилые дома")).toBeInTheDocument();
    expect(screen.getByText("ЖК Северный")).toBeInTheDocument();
    expect(screen.getByText('ООО "Подрядчик"')).toBeInTheDocument();
  });

  it("сумма договора не теряет разрядов", async () => {
    renderPassport();
    // §3: деньги приходят строкой и форматируются без перевода в number. Сумма на
    // 12 значащих цифр — тот размер, где double уже врёт в последнем разряде.
    expect(await screen.findByText(/1\s234\s567\s890,12/)).toBeInTheDocument();
  });

  it("показывает ключевые расценки последней сметы", async () => {
    renderPassport();

    expect(await screen.findByText("Ключевые расценки")).toBeInTheDocument();
    expect(screen.getByText("Кладка кирпичная наружных стен")).toBeInTheDocument();
    expect(screen.getByText("Стяжка пола цементная")).toBeInTheDocument();
    // Смета — доп. соглашение: §6 берёт только последнюю, и это должно быть видно.
    expect(screen.getByText("доп. соглашение № 1")).toBeInTheDocument();
  });

  it("превышение норматива видно без ручных действий, «нет норматива» отличимо от 0 %", async () => {
    renderPassport();
    await screen.findByText("Ключевые расценки");

    // Три случая §10 рядом: превышение, ровно норматив, норматива нет.
    expect(screen.getByText("+20,0%")).toBeInTheDocument();
    expect(screen.getByText("0,0%")).toBeInTheDocument();

    // «Нет норматива» — прочерк с подсказкой, а НЕ «0,0%». Проверяется наличием
    // подсказки: без неё прочерк нельзя отличить от отсутствия данных.
    const noStandard = screen.getAllByTitle(/Нет норматива/);
    expect(noStandard.length).toBeGreaterThan(0);
  });

  it("подсвечивает превышение и экономию разными цветами", async () => {
    renderPassport();
    await screen.findByText("Ключевые расценки");

    // Подсветка — требование §7.4 («отклонения с подсветкой»). Проверяется класс
    // тона, а не конкретный цвет: цвет живёт в токенах темы.
    expect(screen.getByText("+20,0%").className).toContain("text-warning-text");
    // Ровно по нормативу — нейтрально: это не превышение и не экономия.
    expect(screen.getByText("0,0%").className).toContain("text-fg-tertiary");
  });

  it("итоги считаются по всей смете, а не по показанному топу", async () => {
    renderPassport();
    await screen.findByText("Ключевые расценки");

    // 3 из 1100 — иначе сумму топа легко прочитать как сумму сметы.
    expect(screen.getByText(/Показаны 3 из 1100 расценённых\s+позиций/)).toBeInTheDocument();
    expect(screen.getByText(/у 1098 позиций норматива на эту дату нет/)).toBeInTheDocument();
  });

  it("превышения и «без норматива» показаны разными счётчиками", async () => {
    renderPassport();
    await screen.findByText("Ключевые расценки");

    // §10: слей их в один счётчик — и работа без норматива читалась бы как
    // уложившаяся в него.
    expect(screen.getByText("Превышают норматив")).toBeInTheDocument();
    expect(screen.getByText(/из 2 сравнимых позиций; без норматива 1098/)).toBeInTheDocument();
  });

  it("многокилобайтовое наименование не режется в данных, только в показе", async () => {
    renderPassport();
    await screen.findByText("Ключевые расценки");

    // §11 AGENTS.md: наименование бывает на килобайты. Полный текст обязан
    // остаться доступным (в `title`), иначе печатная форма врёт о работе.
    const cell = screen.getByTitle(longJobTitle);
    expect(cell).toBeInTheDocument();
    expect(cell.getAttribute("data-print")).toBe("clamp");
  });

  it("договор без сметы печатается по реквизитам, а не отказывает", async () => {
    handlerState.passportWithoutEstimate = true;
    renderPassport();

    expect(await screen.findByText("ГП-0114")).toBeInTheDocument();
    expect(screen.getByText(/Смета к договору ещё не загружена/)).toBeInTheDocument();
    expect(screen.getByText("не загружена")).toBeInTheDocument();
    expect(screen.queryByText("Ключевые расценки")).not.toBeInTheDocument();
  });

  it("несуществующий договор даёт понятный экран, а не пустоту", async () => {
    server.use(
      http.get("/api/v1/analytics/passport/:contractId", () =>
        HttpResponse.json({ detail: "Договор 10 не найден." }, { status: 404 })
      )
    );
    renderPassport();

    expect(await screen.findByText("Паспорт не построен")).toBeInTheDocument();
  });

  it("блок подписей есть и на экране, а не только на печати", async () => {
    renderPassport();
    await screen.findByText("ГП-0114");

    // Паспорт — форма, которую подписывают: место под подпись должно быть видно
    // до того, как его напечатали.
    expect(screen.getByText(/Составил/)).toBeInTheDocument();
    expect(screen.getByText(/Утвердил/)).toBeInTheDocument();
  });

  it("служебное обрамление помечено как непечатаемое", async () => {
    const { container } = renderPassport();
    await screen.findByText("ГП-0114");

    // Кнопка печати и хлебные крошки документом не являются. Проверяется разметка,
    // потому что сам `@media print` в jsdom не наблюдаем.
    const hidden = container.querySelector('[data-print="hide"]');
    expect(hidden).not.toBeNull();
    expect(within(hidden as HTMLElement).getByRole("button", { name: /Печать/ })).toBeInTheDocument();

    expect(container.querySelector('[data-print="sheet"]')).not.toBeNull();
  });

  it("длина топа следует настройке из базы", async () => {
    handlerState.passportTopN = 1;
    renderPassport();

    await screen.findByText("Ключевые расценки");
    expect(screen.getByText("Кладка кирпичная наружных стен")).toBeInTheDocument();
    // N = 1, значит вторая работа не показывается, хотя в смете она есть.
    expect(screen.queryByText("Стяжка пола цементная")).not.toBeInTheDocument();
    expect(screen.getByText(/Показаны 1 из 1100/)).toBeInTheDocument();
  });

  it("неразобранная очередь названа настоящей причиной пустого топа", async () => {
    /*
      **Найдено прогоном стенда фазы 6.** На живой базе все позиции реальной сметы
      имели цену, но каталог целиком состоял из TO_REVIEW, и паспорт сообщал «не
      заполнена цена за единицу» — то есть указывал на несуществующую проблему.
      Тесты этого не поймали: в фикстурах каталожные строки сразу POSITION.
    */
    server.use(
      http.get("/api/v1/analytics/passport/:contractId", () =>
        HttpResponse.json({
          ...samplePassport,
          key_rates: [],
          totals: {
            ...samplePassport.totals,
            positions_priced: 0,
            positions_shown: 0,
            positions_pending_review: 1830,
          },
        })
      )
    );
    renderPassport();

    expect(await screen.findByText(/1830 позиций ждут ручного матчинга/)).toBeInTheDocument();
    // И есть куда пойти: подсказка без действия оставляет человека там же.
    expect(screen.getByRole("link", { name: /Разобрать очередь ручного матчинга/ })).toHaveAttribute(
      "href",
      "/review"
    );
    // Неверная причина больше не показывается.
    expect(screen.queryByText(/не заполнена цена за единицу/)).not.toBeInTheDocument();
  });

  it("разобранные не-работы не выдаются за отсутствие цены", async () => {
    /*
      Третья причина пустого топа, вскрытая правкой по замечанию ревью: позиции
      расценены, но их каталожные строки помечены как не-работа (§5.4.3). Звать в
      очередь тут нельзя — там их нет; говорить «не заполнена цена» — неправда.
    */
    server.use(
      http.get("/api/v1/analytics/passport/:contractId", () =>
        HttpResponse.json({
          ...samplePassport,
          key_rates: [],
          totals: {
            ...samplePassport.totals,
            positions_priced: 0,
            positions_shown: 0,
            positions_pending_review: 0,
            positions_non_work: 42,
          },
        })
      )
    );
    renderPassport();

    expect(await screen.findByText(/42 расценённых позиций сметы отнесены к строкам/)).toBeInTheDocument();
    expect(screen.queryByText(/не заполнена цена за единицу/)).not.toBeInTheDocument();
    // И в очередь не зовём: разбирать нечего.
    expect(screen.queryByRole("link", { name: /Разобрать очередь/ })).not.toBeInTheDocument();
  });

  it("смета без расценённых работ объясняет причину пустого топа", async () => {
    server.use(
      http.get("/api/v1/analytics/passport/:contractId", () =>
        HttpResponse.json({
          ...samplePassport,
          key_rates: [],
          totals: {
            ...samplePassport.totals,
            positions_priced: 0,
            positions_shown: 0,
            // Нули здесь существенны: причина именно в отсутствии цен, а не в
            // очереди и не в разобранных не-работах.
            positions_pending_review: 0,
            positions_non_work: 0,
          },
        })
      )
    );
    renderPassport();

    expect(await screen.findByText(/у позиций не заполнена цена за единицу/)).toBeInTheDocument();
  });
});
