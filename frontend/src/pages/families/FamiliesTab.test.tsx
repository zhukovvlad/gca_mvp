import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { FamiliesTab } from "@/pages/families/FamiliesTab";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";

/**
 * Вкладка «Семьи» экрана `/families` (спека
 * `2026-09-25-families-screen-design.md` §2.1, §2.7). Экран целиком под
 * `RequireAdmin` (проверяется в `FamiliesPage.test.tsx`, тем же входом, что и
 * `/standards`), здесь — только содержимое вкладки.
 *
 * Правка семьи была диалогом (фича 1) — теперь панель СПРАВА ОТ СПИСКА,
 * открытая щелчком по строке (спека §2.1: «панель правки выбранной семьи
 * справа»); состав действий тот же («Активировать», «Архивировать»,
 * «Слить…»), они переехали ИЗ строки списка В панель.
 */

async function renderTab() {
  renderWithProviders(<FamiliesTab />);
  await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());
}

function dataRows() {
  const rowgroups = screen.getAllByRole("rowgroup");
  // rowgroups[0] — thead, rowgroups[1] — tbody (тот же порядок, что рендерит `Table`).
  return within(rowgroups[1]).getAllByRole("row");
}

describe("FamiliesTab", () => {
  // Размер страницы — `usePersistedPageSize` (`gca.families.families.pageSize`),
  // и без сброса выбор одного теста ("размер 10") пережил бы следующий:
  // именно так дважды устроен ключ per-key, не per-suite (`pageSizeShared.test.tsx`
  // делает то же для той же причины).
  afterEach(() => {
    localStorage.clear();
  });

  it("после seed показывает 42 черновика — штатное первое состояние", async () => {
    await renderTab();
    // Фильтр статуса открывается на draft: в фикстуре 44 семьи, из них 42 —
    // draft (id 43 — active, id 44 — archived), и счёт «42» обязан быть
    // результатом ФИЛЬТРА, а не длины массива фикстуры (докстринг
    // `initialWorkFamilies`). Пагинация (спека §2.7, размер по умолчанию 20)
    // показывает только первую страницу — итог из 42 читается из диапазона
    // под списком, а не из числа строк в DOM.
    expect(dataRows()).toHaveLength(20);
    expect(screen.getByText("1–20 из 42")).toBeInTheDocument();
  });

  // -------------------------------------------------------------------
  //  Сверка с макетом 27.09.2026 (mockup.html, mock-families.png):
  //  статус словом+цветом, сводка над списком, единица символом, ellipsis
  //  определения.
  // -------------------------------------------------------------------

  it("статус печатается словом — ни в строке списка, ни в фильтре нет кода draft/active/archived", async () => {
    const user = userEvent.setup();
    await renderTab();

    // Список — статус draft: строки несут «черновик», не код.
    expect(screen.queryByText("draft", { selector: "span" })).not.toBeInTheDocument();
    expect(screen.getAllByText("черновик").length).toBeGreaterThan(0);

    // Фильтр — раскрыть и проверить слова у ВСЕХ вариантов.
    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    expect(await screen.findByText("все")).toBeInTheDocument();
    expect(screen.getByText("активна")).toBeInTheDocument();
    expect(screen.getByText("в архиве")).toBeInTheDocument();
    expect(screen.queryByText("active", { selector: "[role=option]" })).not.toBeInTheDocument();
    expect(screen.queryByText("archived", { selector: "[role=option]" })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    // Панель правки (ревью задачи 9) — её плашка статуса тоже словом.
    await user.click(screen.getByText("Семья работ №3"));
    const heading = await screen.findByRole("heading", { name: "Семья «Семья работ №3»" });
    const panelHead = heading.parentElement!;
    expect(within(panelHead).getByText("черновик")).toBeInTheDocument();
    expect(within(panelHead).queryByText("draft")).not.toBeInTheDocument();
  });

  it("статусы «черновик»/«активна»/«в архиве» несут РАЗНЫЕ классы заливки (спека, сверка с макетом)", async () => {
    const user = userEvent.setup();
    await renderTab();
    // Открыть фильтр «все», страница 100 — все три статуса видны в списке разом.
    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    await user.click(await screen.findByRole("option", { name: "все" }));
    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByRole("option", { name: "100" }));

    // Строки конкретных семей (не селект) — исключает совпадение с текстом
    // всплывающего списка выбора.
    const draftRow = (await screen.findByText("Семья работ №3")).closest("tr")!;
    const activeRow = screen.getByText("Кровельные работы").closest("tr")!;
    const archivedRow = screen.getByText("Демонтажные работы (снята)").closest("tr")!;
    const draftBadge = within(draftRow).getByText("черновик");
    const activeBadge = within(activeRow).getByText("активна");
    const archivedBadge = within(archivedRow).getByText("в архиве");
    const classes = new Set([draftBadge.className, activeBadge.className, archivedBadge.className]);
    expect(classes.size).toBe(3);
  });

  it("сводка над списком считает по ВСЕМ семьям (1 активная, 42 черновика, 1 в архиве) — не по текущему фильтру", async () => {
    await renderTab();
    // Фильтр по умолчанию — draft, но сводка обязана считать по ПОЛНОМУ
    // списку (решение этой задачи, спека §2.8, сверка с макетом 27.09.2026):
    // иначе на дефолтном экране сводка солгала бы «активных 0».
    expect(screen.getByText("активных 1 · черновиков 42 · в архиве 1")).toBeInTheDocument();
  });

  /**
   * Внешнее ревью PR #54: сводка раньше считалась по `allFamiliesQ.data ?? []`
   * — пока безфильтровый запрос ещё в пути (или упал), пустой массив даёт
   * счётчики «0/0/0», и экран лжёт «активных 0», хотя причина ложь загрузки,
   * а не состав каталога. Отфильтрованный запрос (`familiesQ`, несёт
   * `status=draft`) и безфильтровый (`allFamiliesQ`, без параметров) — два
   * РАЗНЫХ запроса одного маршрута, различаются по строке запроса.
   */
  it("сводка не печатается, пока безфильтровый запрос ещё в пути — список при этом уже виден", async () => {
    server.use(
      http.get("/api/v1/semantic/families", async ({ request }) => {
        const url = new URL(request.url);
        const isUnfiltered = !url.searchParams.get("status") && !url.searchParams.get("unit_id");
        if (isUnfiltered) {
          await delay("infinite");
        }
        const status = url.searchParams.get("status");
        const unitId = url.searchParams.get("unit_id");
        const items = handlerState.workFamilies.filter((family) => {
          if (status && family.status !== status) return false;
          if (unitId && String(family.unit_id) !== unitId) return false;
          return true;
        });
        return HttpResponse.json({ items });
      })
    );

    renderWithProviders(<FamiliesTab />);

    // Отфильтрованный запрос (status=draft) отвечает штатно — список виден.
    await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());
    expect(screen.queryByText(/активных/)).not.toBeInTheDocument();
    expect(screen.queryByText(/сводка недоступна/)).not.toBeInTheDocument();
  });

  it("сводка печатает «сводка недоступна», когда безфильтровый запрос упал — не «активных 0»", async () => {
    server.use(
      http.get("/api/v1/semantic/families", ({ request }) => {
        const url = new URL(request.url);
        const status = url.searchParams.get("status");
        const unitId = url.searchParams.get("unit_id");
        const isUnfiltered = !status && !unitId;
        if (isUnfiltered) {
          return HttpResponse.json({ detail: "Не удалось получить семьи." }, { status: 500 });
        }
        const items = handlerState.workFamilies.filter((family) => {
          if (status && family.status !== status) return false;
          if (unitId && String(family.unit_id) !== unitId) return false;
          return true;
        });
        return HttpResponse.json({ items });
      })
    );

    renderWithProviders(<FamiliesTab />);

    await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());
    expect(await screen.findByText("сводка недоступна")).toBeInTheDocument();
    expect(screen.queryByText(/активных 0/)).not.toBeInTheDocument();
  });

  it("единица списка — символ (м²), не код (M2)", async () => {
    await renderTab();
    expect(screen.queryByText("M2")).not.toBeInTheDocument();
    expect(screen.getAllByText("м²").length).toBeGreaterThan(0);
  });

  it("определение без текста печатает красное «нет определения», непустое — обрезается многоточием с полным текстом в title", async () => {
    await renderTab();
    // id=2 ("Устройство покрытий полов") и 39 других черновиков без
    // определения на этой странице — берём строку id=2 ИМЕННО, не первую
    // попавшуюся.
    const row = screen.getByText("Устройство покрытий полов").closest("tr")!;
    const empty = within(row).getByText("нет определения");
    expect(empty).toHaveClass("text-destructive");

    // id=1 ("Семья работ №1") несёт определение — печатается ЦЕЛИКОМ в
    // атрибуте `title` (полный текст доступен без раскрытия), само отображение
    // обрезается CSS (`truncate`), а не JS-обрезкой строки.
    const definitionText =
      "Оштукатуривание стен и потолков цементно-песчаным раствором.";
    const full = screen.getByTitle(definitionText);
    expect(full).toHaveClass("truncate");
  });

  it("кнопка активации в панели недоступна без определения, а с определением доступна", async () => {
    const user = userEvent.setup();
    await renderTab();

    // id=2 ("Устройство покрытий полов") — черновик без определения:
    // щелчок по строке открывает панель, кнопка активации в НЕЙ недоступна.
    await user.click(screen.getByText("Устройство покрытий полов"));
    expect(
      await screen.findByRole("button", { name: "Активировать семью Устройство покрытий полов" })
    ).toBeDisabled();

    // id=1 ("Семья работ №1") несёт определение — переключаемся на её панель,
    // кнопка активна, и подсказки о недостающем определении больше нет.
    await user.click(screen.getByText("Семья работ №1"));
    expect(
      await screen.findByRole("button", { name: "Активировать семью Семья работ №1" })
    ).not.toBeDisabled();
    expect(
      screen.queryByText("Активировать можно только с определением.")
    ).not.toBeInTheDocument();
  });

  it("правка единицы в панели недоступна при привязках, и подпись называет их число", async () => {
    const user = userEvent.setup();
    await renderTab();

    // id=1 ("Семья работ №1") несёт две привязки (`context_count: 2` в фикстуре).
    await user.click(screen.getByText("Семья работ №1"));
    expect(await screen.findByLabelText("Единица")).toBeDisabled();
    // Символ, не код (ревью задачи 9): поле панели — тоже экран.
    expect(screen.getByLabelText("Единица")).toHaveValue("м²");
    expect(
      screen.getByText("Единица недоступна: привязано контекстов — 2")
    ).toBeInTheDocument();
  });

  it("у семьи без привязок единица в панели редактируется, и подписи о недоступности нет", async () => {
    const user = userEvent.setup();
    await renderTab();

    // id=3 ("Семья работ №3") — без привязок (`context_count: 0`).
    await user.click(screen.getByText("Семья работ №3"));
    expect(await screen.findByLabelText("Единица")).not.toBeDisabled();
    // Предзаполнение правки — символом, не кодом (ревью задачи 9).
    expect(screen.getByLabelText("Единица")).toHaveValue("м²");
    expect(screen.queryByText(/Единица недоступна/)).not.toBeInTheDocument();
  });

  it("путь «дописать определение → активировать» проходит на экране через панель", async () => {
    const user = userEvent.setup();
    await renderTab();

    // id=2 ("Устройство покрытий полов") — черновик без определения и без привязок.
    const activateName = "Активировать семью Устройство покрытий полов";
    await user.click(screen.getByText("Устройство покрытий полов"));
    expect(await screen.findByRole("button", { name: activateName })).toBeDisabled();

    await user.type(screen.getByLabelText("Определение"), "Устройство покрытий полов из линолеума.");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(handlerState.lastUpdateFamilyRequest).toMatchObject({
        id: 2,
        body: { definition: "Устройство покрытий полов из линолеума." },
      })
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: activateName })).not.toBeDisabled()
    );
    await user.click(screen.getByRole("button", { name: activateName }));

    // Активированная семья уходит из фильтра draft: 42 → 41 (диапазон под
    // списком — тот же счёт, что раньше читался длиной DOM, теперь за
    // пагинацией страницы).
    await waitFor(() => expect(screen.getByText("1–20 из 41")).toBeInTheDocument());
    expect(screen.queryByText("Устройство покрытий полов")).not.toBeInTheDocument();
  });

  it("правка семьи с привязками не шлёт unit_name — иначе сервер отказал бы и определение не сохранилось", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Семья работ №1"));
    const definition = await screen.findByLabelText("Определение");
    await user.clear(definition);
    await user.type(definition, "Новое определение.");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(handlerState.lastUpdateFamilyRequest).not.toBeNull());
    expect(handlerState.lastUpdateFamilyRequest!.id).toBe(1);
    expect(handlerState.lastUpdateFamilyRequest!.body).not.toHaveProperty("unit_name");
    expect(handlerState.lastUpdateFamilyRequest!.body).toMatchObject({ definition: "Новое определение." });
  });

  it("правка семьи без привязок шлёт unit_name из поля единицы", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Семья работ №3"));
    const unit = await screen.findByLabelText("Единица");
    await user.clear(unit);
    await user.type(unit, "м3");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(handlerState.lastUpdateFamilyRequest).toMatchObject({ id: 3, body: { unit_name: "м3" } })
    );
  });

  it("создание семьи доходит до сервера и появляется в списке", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: /Новая семья/ }));
    await user.type(screen.getByLabelText("Название (обязательно)"), "Новая семья работ");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(handlerState.lastCreateFamilyRequest).toMatchObject({ title: "Новая семья работ" })
    );
    // Новая семья — 43-я в фильтре draft (сервер добавляет её в конец
    // списка): диапазон под списком обязан отразить рост итога сразу, а сама
    // строка находится на СВОЕЙ странице (3-я при размере 20), не на первой.
    await waitFor(() => expect(screen.getByText("1–20 из 43")).toBeInTheDocument());
    await user.click(within(screen.getByRole("navigation", { name: "pagination" })).getByText("3"));
    expect(await screen.findByText("Новая семья работ")).toBeInTheDocument();
  });

  it("фильтр по единице сужает список семей (спека §2.10)", async () => {
    const user = userEvent.setup();
    await renderTab();

    // Статус "Любой" — иначе id 43 (active, единица «Куб. метр») исключён
    // фильтром статуса и не докажет, что сужает именно ЕДИНИЦА. Размер
    // страницы — 100: id 43/44 стоят в конце списка (44 семьи), а страница
    // размером 20 их не покажет вовсе.
    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    await user.click(await screen.findByRole("option", { name: "все" }));
    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByRole("option", { name: "100" }));
    await waitFor(() => expect(screen.getByText("Кровельные работы")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { name: "Единица (фильтр)" }));
    await user.click(await screen.findByRole("option", { name: "Куб. метр" }));

    await waitFor(() => {
      expect(screen.getByText("Кровельные работы")).toBeInTheDocument();
      expect(screen.queryByText("Семья работ №3")).not.toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------
  //  Панель правки — щелчок по строке, не отдельная кнопка (спека §2.1)
  // ---------------------------------------------------------------------

  it("щелчок по семье открывает панель правки справа от списка; список остаётся виден", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Семья работ №3"));

    expect(await screen.findByLabelText("Название")).toBeInTheDocument();
    // Список рядом — не подменён панелью.
    expect(screen.getByText("Устройство покрытий полов")).toBeInTheDocument();
  });

  // Прежняя кнопка «Правка» (фича 1) была доступна с клавиатуры; строка без
  // неё обязана остаться доступной сама (спека §2.1).
  it("строка семьи открывается с клавиатуры — фокус и Enter", async () => {
    const user = userEvent.setup();
    await renderTab();

    const row = screen.getByText("Семья работ №3").closest("tr")!;
    row.focus();
    await user.keyboard("{Enter}");

    expect(await screen.findByLabelText("Название")).toHaveValue("Семья работ №3");
  });

  it("строка семьи открывается с клавиатуры — фокус и Пробел", async () => {
    const user = userEvent.setup();
    await renderTab();

    const row = screen.getByText("Семья работ №1").closest("tr")!;
    row.focus();
    await user.keyboard(" ");

    expect(await screen.findByLabelText("Название")).toHaveValue("Семья работ №1");
  });

  it("Пробел и Enter на строке семьи гасят действие по умолчанию (страница не прокручивается)", async () => {
    await renderTab();

    const row = screen.getByText("Семья работ №3").closest("tr")!;
    row.focus();
    // `fireEvent` возвращает false, если обработчик вызвал preventDefault.
    expect(fireEvent.keyDown(row, { key: " " })).toBe(false);
    expect(fireEvent.keyDown(row, { key: "Enter" })).toBe(false);
  });

  it("прочие клавиши на строке семьи (Tab, буква) не открывают панель и не гасят действие по умолчанию", async () => {
    await renderTab();

    const row = screen.getByText("Семья работ №3").closest("tr")!;
    row.focus();
    expect(fireEvent.keyDown(row, { key: "Tab" })).toBe(true);
    expect(fireEvent.keyDown(row, { key: "a" })).toBe(true);
    expect(screen.getByText("Семья не выбрана")).toBeInTheDocument();
  });

  it("переключение между семьями меняет содержимое панели, а не накапливает несохранённый черновик", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Семья работ №3"));
    const definitionField = await screen.findByLabelText("Определение");
    await user.type(definitionField, "черновик, который не будет сохранён");

    // Переключились на другую семью — поле панели обязано показать ЕЁ данные,
    // а не несохранённый текст предыдущей.
    await user.click(screen.getByText("Семья работ №1"));
    await waitFor(() =>
      expect(screen.getByLabelText("Определение")).toHaveValue(
        "Оштукатуривание стен и потолков цементно-песчаным раствором."
      )
    );
  });

  // ---------------------------------------------------------------------
  //  Пагинация на фронтенде (спека §2.7): весь список приходит одним
  //  запросом, страницы режет клиент; размер по умолчанию 20.
  // ---------------------------------------------------------------------

  it("пагинация на фронтенде: размер 10 при 43 семьях (статус «Любой» + единица «Кв. метр») даёт 5 страниц, третья — 21–30", async () => {
    const user = userEvent.setup();
    await renderTab();

    // «Любой статус» + «Кв. метр» (unit_id 5) — 42 черновика (все несут
    // unit_id=5) плюс архивная (id 44, тоже unit_id=5); активная (id 43) несёт
    // ДРУГУЮ единицу (unit_id=3, «Куб. метр») и в выдачу не входит: 42+1=43.
    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    await user.click(await screen.findByRole("option", { name: "все" }));
    await user.click(screen.getByRole("combobox", { name: "Единица (фильтр)" }));
    await user.click(await screen.findByRole("option", { name: "Кв. метр" }));

    await waitFor(() => expect(screen.getByText("1–20 из 43")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByRole("option", { name: "10" }));
    await waitFor(() => {
      expect(dataRows()).toHaveLength(10);
      expect(screen.getByText("1–10 из 43")).toBeInTheDocument();
    });

    // 43 при размере 10 — ровно 5 страниц: последний номер «5», «6» нет.
    const nav = screen.getByRole("navigation", { name: "pagination" });
    expect(within(nav).getByText("5")).toBeInTheDocument();
    expect(within(nav).queryByText("6")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));

    await waitFor(() => {
      expect(dataRows()).toHaveLength(10);
      expect(screen.getByText("21–30 из 43")).toBeInTheDocument();
    });
    // Строки третьей страницы — ИМЕННО 21-я…30-я семья выдачи (id 21…30),
    // а не любые десять: диапазон под списком считается отдельно от среза, и
    // срез со сдвинутым началом дал бы ту же подпись при чужих строках.
    const rows = dataRows();
    expect(within(rows[0]).getByText("Семья работ №21")).toBeInTheDocument();
    expect(within(rows[9]).getByText("Семья работ №30")).toBeInTheDocument();

    // Последняя, неполная страница — диапазон кончается итогом.
    await user.click(within(screen.getByRole("navigation", { name: "pagination" })).getByText("5"));
    await waitFor(() => expect(screen.getByText("41–43 из 43")).toBeInTheDocument());
    expect(dataRows()).toHaveLength(3);
  });

  it("смена фильтра статуса тоже возвращает на первую страницу", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByRole("option", { name: "10" }));
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getByText("11–20 из 42")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    await user.click(await screen.findByRole("option", { name: "все" }));

    await waitFor(() => expect(screen.getByText("1–10 из 44")).toBeInTheDocument());
  });

  it("смена размера страницы возвращает на первую страницу (спека §2.7)", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getByText("21–40 из 42")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByRole("option", { name: "10" }));

    await waitFor(() => expect(screen.getByText("1–10 из 42")).toBeInTheDocument());
  });

  it("размер страницы читается и пишется ключом gca.families.families.pageSize", async () => {
    localStorage.setItem("gca.families.families.pageSize", "10");
    const user = userEvent.setup();
    await renderTab();

    expect(screen.getByText("1–10 из 42")).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByRole("option", { name: "50" }));
    await waitFor(() => expect(screen.getByText("1–42 из 42")).toBeInTheDocument());
    expect(localStorage.getItem("gca.families.families.pageSize")).toBe("50");
  });

  // ---------------------------------------------------------------------
  //  Действия панели по статусу семьи — «Активировать» / «Слить…» /
  //  «Архивировать» переехали из строки в панель (спека §2.1: в тестах
  //  фичи 1 эти ветви не стояли вовсе).
  // ---------------------------------------------------------------------

  it("панель черновика: «Активировать» и «Архивировать», без «Слить»", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Семья работ №3"));
    await screen.findByLabelText("Определение");
    expect(screen.getByRole("button", { name: "Активировать семью Семья работ №3" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "В архив: семья Семья работ №3" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Слить" })).not.toBeInTheDocument();
    // Без определения (id=3 его не несёт) — подсказка под кнопками (сверка
    // с макетом 27.09.2026).
    expect(screen.getByText("Активировать можно только с определением.")).toBeInTheDocument();
  });

  it("панель активной семьи: «Слить» открывает диалог слияния ЭТОЙ семьи; «Активировать» нет", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    await user.click(await screen.findByRole("option", { name: "активна" }));
    await user.click(await screen.findByText("Кровельные работы"));
    await screen.findByLabelText("Определение");

    expect(screen.queryByRole("button", { name: /Активировать семью/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "В архив: семья Кровельные работы" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Слить" }));
    expect(await screen.findByText("Слить семью «Кровельные работы»")).toBeInTheDocument();
  });

  it("панель архивной семьи не предлагает ни одного действия жизненного цикла", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    await user.click(await screen.findByRole("option", { name: "в архиве" }));
    await user.click(await screen.findByText("Демонтажные работы (снята)"));
    await screen.findByLabelText("Определение");

    expect(screen.queryByRole("button", { name: /Активировать семью/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /В архив: семья/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Слить" })).not.toBeInTheDocument();
  });

  it("«Архивировать» из панели семьи с привязками предупреждает об отказе сервера числом привязок", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Семья работ №1"));
    await user.click(await screen.findByRole("button", { name: "В архив: семья Семья работ №1" }));

    expect(await screen.findByText("Архивировать семью «Семья работ №1»?")).toBeInTheDocument();
    expect(
      screen.getByText(/У семьи есть привязанные контексты: 2\. Сервер откажет/)
    ).toBeInTheDocument();
  });

  it("«Архивировать» из панели семьи без привязок архивирует ЭТУ семью, и она уходит из фильтра draft", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Семья работ №3"));
    await user.click(await screen.findByRole("button", { name: "В архив: семья Семья работ №3" }));
    expect(await screen.findByText("Архивная семья перестаёт предлагаться для назначения контексту.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Архивировать" }));

    await waitFor(() => expect(screen.getByText("1–20 из 41")).toBeInTheDocument());
    expect(handlerState.workFamilies.find((f) => f.id === 3)!.status).toBe("archived");
  });

  it("смена фильтра возвращает пагинацию на первую страницу", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByRole("option", { name: "10" }));
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getByText("11–20 из 42")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { name: "Единица (фильтр)" }));
    await user.click(await screen.findByRole("option", { name: "Кв. метр" }));

    // Единица «Кв. метр» не сужает 42 черновика (все несут unit_id=5) — важен
    // здесь только СБРОС страницы, а не число.
    await waitFor(() => expect(screen.getByText("1–10 из 42")).toBeInTheDocument());
  });

  it("сокращение выдачи действием панели со страницы вне диапазона возвращает на последнюю валидную страницу", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByRole("option", { name: "10" }));
    // Страница 5 — последняя при 42 черновиках и размере 10 (строки 41–42).
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getByText("41–42 из 42")).toBeInTheDocument());

    // Архивируем ОБЕ семьи страницы (id 41, 42 — без привязок): после первой
    // страница 5 остаётся валидной (41 семья, 5 страниц), после второй — нет
    // (40 семей, 4 страницы), и «41–50» из среза не существует вовсе.
    await user.click(screen.getByText("Семья работ №41"));
    await user.click(await screen.findByRole("button", { name: "В архив: семья Семья работ №41" }));
    await user.click(await screen.findByRole("button", { name: "Архивировать" }));
    await waitFor(() => expect(screen.getByText("41–41 из 41")).toBeInTheDocument());

    await user.click(screen.getByText("Семья работ №42"));
    await user.click(await screen.findByRole("button", { name: "В архив: семья Семья работ №42" }));
    await user.click(await screen.findByRole("button", { name: "Архивировать" }));

    // Была бы страница 5 (индексы 41–50) на 40 семьях — диапазон пуст
    // («41–40 из 40»); зажим обязан вернуть на последнюю валидную, 4-ю.
    await waitFor(() => expect(screen.getByText("31–40 из 40")).toBeInTheDocument());
    expect(dataRows()).toHaveLength(10);
  });
  it("панель семьи несёт блок «Схема и варианты» выбранной семьи", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    await user.click(await screen.findByRole("option", { name: "активна" }));
    // Смена фильтра статуса перечитывает список — ожидание с запасом под нагрузку `just ci`.
    await user.click(await screen.findByText("Кровельные работы", {}, { timeout: 8000 }));

    expect(await screen.findByRole("heading", { name: "Схема и варианты" })).toBeInTheDocument();
    expect(await screen.findByText("Версия схемы 2", {}, { timeout: 8000 })).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: "Варианты семьи" }, { timeout: 8000 })).toBeInTheDocument();
  });
});
