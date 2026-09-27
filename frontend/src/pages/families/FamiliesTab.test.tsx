import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { FamiliesTab } from "@/pages/families/FamiliesTab";
import { handlerState } from "@/test/handlers";
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
    // кнопка активна.
    await user.click(screen.getByText("Семья работ №1"));
    expect(
      await screen.findByRole("button", { name: "Активировать семью Семья работ №1" })
    ).not.toBeDisabled();
  });

  it("правка единицы в панели недоступна при привязках, и подпись называет их число", async () => {
    const user = userEvent.setup();
    await renderTab();

    // id=1 ("Семья работ №1") несёт две привязки (`context_count: 2` в фикстуре).
    await user.click(screen.getByText("Семья работ №1"));
    expect(await screen.findByLabelText("Единица")).toBeDisabled();
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
    await user.click(await screen.findByText("Любой статус"));
    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByText("100"));
    await waitFor(() => expect(screen.getByText("Кровельные работы")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { name: "Единица (фильтр)" }));
    await user.click(await screen.findByText("Куб. метр"));

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
    await user.click(await screen.findByText("Любой статус"));
    await user.click(screen.getByRole("combobox", { name: "Единица (фильтр)" }));
    await user.click(await screen.findByText("Кв. метр"));

    await waitFor(() => expect(screen.getByText("1–20 из 43")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByText("10"));
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
    await user.click(await screen.findByText("10"));
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getByText("11–20 из 42")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    await user.click(await screen.findByText("Любой статус"));

    await waitFor(() => expect(screen.getByText("1–10 из 44")).toBeInTheDocument());
  });

  it("смена размера страницы возвращает на первую страницу (спека §2.7)", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getByText("21–40 из 42")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByText("10"));

    await waitFor(() => expect(screen.getByText("1–10 из 42")).toBeInTheDocument());
  });

  it("размер страницы читается и пишется ключом gca.families.families.pageSize", async () => {
    localStorage.setItem("gca.families.families.pageSize", "10");
    const user = userEvent.setup();
    await renderTab();

    expect(screen.getByText("1–10 из 42")).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByText("50"));
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
    expect(screen.getByRole("button", { name: "Архивировать семью Семья работ №3" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Слить" })).not.toBeInTheDocument();
  });

  it("панель активной семьи: «Слить» открывает диалог слияния ЭТОЙ семьи; «Активировать» нет", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    await user.click(await screen.findByText("active"));
    await user.click(await screen.findByText("Кровельные работы"));
    await screen.findByLabelText("Определение");

    expect(screen.queryByRole("button", { name: /Активировать семью/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Архивировать семью Кровельные работы" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Слить" }));
    expect(await screen.findByText("Слить семью «Кровельные работы»")).toBeInTheDocument();
  });

  it("панель архивной семьи не предлагает ни одного действия жизненного цикла", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "Статус" }));
    await user.click(await screen.findByText("archived"));
    await user.click(await screen.findByText("Демонтажные работы (снята)"));
    await screen.findByLabelText("Определение");

    expect(screen.queryByRole("button", { name: /Активировать семью/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Архивировать семью/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Слить" })).not.toBeInTheDocument();
  });

  it("«Архивировать» из панели семьи с привязками предупреждает об отказе сервера числом привязок", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Семья работ №1"));
    await user.click(await screen.findByRole("button", { name: "Архивировать семью Семья работ №1" }));

    expect(await screen.findByText("Архивировать семью «Семья работ №1»?")).toBeInTheDocument();
    expect(
      screen.getByText(/У семьи есть привязанные контексты: 2\. Сервер откажет/)
    ).toBeInTheDocument();
  });

  it("«Архивировать» из панели семьи без привязок архивирует ЭТУ семью, и она уходит из фильтра draft", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByText("Семья работ №3"));
    await user.click(await screen.findByRole("button", { name: "Архивировать семью Семья работ №3" }));
    expect(await screen.findByText("Архивная семья перестаёт предлагаться для назначения контексту.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Архивировать" }));

    await waitFor(() => expect(screen.getByText("1–20 из 41")).toBeInTheDocument());
    expect(handlerState.workFamilies.find((f) => f.id === 3)!.status).toBe("archived");
  });

  it("смена фильтра возвращает пагинацию на первую страницу", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByText("10"));
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(screen.getByText("11–20 из 42")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { name: "Единица (фильтр)" }));
    await user.click(await screen.findByText("Кв. метр"));

    // Единица «Кв. метр» не сужает 42 черновика (все несут unit_id=5) — важен
    // здесь только СБРОС страницы, а не число.
    await waitFor(() => expect(screen.getByText("1–10 из 42")).toBeInTheDocument());
  });

  it("сокращение выдачи действием панели со страницы вне диапазона возвращает на последнюю валидную страницу", async () => {
    const user = userEvent.setup();
    await renderTab();

    await user.click(screen.getByRole("combobox", { name: "На странице:" }));
    await user.click(await screen.findByText("10"));
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
    await user.click(await screen.findByRole("button", { name: "Архивировать семью Семья работ №41" }));
    await user.click(await screen.findByRole("button", { name: "Архивировать" }));
    await waitFor(() => expect(screen.getByText("41–41 из 41")).toBeInTheDocument());

    await user.click(screen.getByText("Семья работ №42"));
    await user.click(await screen.findByRole("button", { name: "Архивировать семью Семья работ №42" }));
    await user.click(await screen.findByRole("button", { name: "Архивировать" }));

    // Была бы страница 5 (индексы 41–50) на 40 семьях — диапазон пуст
    // («41–40 из 40»); зажим обязан вернуть на последнюю валидную, 4-ю.
    await waitFor(() => expect(screen.getByText("31–40 из 40")).toBeInTheDocument());
    expect(dataRows()).toHaveLength(10);
  });
});
