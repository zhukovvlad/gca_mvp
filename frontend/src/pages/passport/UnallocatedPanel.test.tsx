import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { UnallocatedPanel } from "./UnallocatedPanel";
import { longJobTitle, sampleProjectPassport } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { ProjectPassport } from "@/types/domain";

/**
 * Панель-верстак разноса (план, задача 8).
 *
 * Рендерится напрямую, без `ProjectPassportPage`/`CategoryTable` — контракт
 * компонента: `passport`, `contractId`, `estimateId` приходят пропсами
 * (сборку строки «Нераспределённое» и разворот по шеврону несёт
 * `CategoryTable`, её тесты — в `ProjectPassportPage.test.tsx`).
 *
 * Значения `contractId`/`estimateId` здесь синтетические (5 и 11) — панели
 * они нужны только чтобы передать их дальше в мутации; реальные значения из
 * маршрута/сметы проверяет тест разводки в `ProjectPassportPage.test.tsx`.
 *
 * Дерево нераспределённого в фикстуре (`sampleProjectPassport.unallocated.sections`):
 * - 5001 «Устройство временных сооружений» — корень, свои строки есть, ни одна не
 *   расценена (`amount: null`, `rows: 3`), лист (`subtree_amount: null`).
 * - 5002 «Демонтажные работы» — корень, своих денег нет (`amount: null`), но
 *   `subtree_amount: "115000.00"` — единственное известное слагаемое дерева,
 *   свёрнутое от ребёнка.
 * - 5003 «Демонтаж перегородок» — ребёнок 5002, `depth: 1`, единственный узел с
 *   известной СВОЕЙ суммой (`amount: "115000.00"`).
 *
 * `manual_assignments` несёт одну запись: 5004 «Устройство эстакад», отнесённая
 * на статью "10" («Прочие работы»).
 */

// Один путь, два глагола (PUT/DELETE) — не два эндпоинта: раньше константы
// назывались PUT_URL/DELETE_URL при байт-в-байт одинаковом значении.
const CATEGORY_OVERRIDE_URL = "/api/v1/estimates/:estimateId/category-overrides/:positionItemId";

function renderPanel(passport: ProjectPassport = sampleProjectPassport) {
  return renderWithProviders(
    <UnallocatedPanel passport={passport} contractId={5} estimateId={11} />
  );
}

describe("Панель-верстак разноса: разметка и печать", () => {
  it('панель помечена data-testid и не попадает в печатный поток (data-print="hide")', () => {
    renderPanel();

    const panel = screen.getByTestId("unallocated-panel");
    expect(panel).toHaveAttribute("data-print", "hide");
  });
});

describe("Панель-верстак разноса: дерево разделов без статьи", () => {
  it("дерево восстановлено по parent_position_item_id, а не по порядку sections[]", () => {
    // sections[] идёт по глубине (5001 depth0, 5002 depth0, 5003 depth1) — узел
    // 5003 обязан оказаться ВНУТРИ строки 5002, а не рядом с ней как третий корень.
    renderPanel();

    const parent = screen.getByTestId("unallocated-section-5002");
    expect(within(parent).getByTestId("unallocated-section-5003")).toBeInTheDocument();
  });

  it("сиблинги отсортированы по subtree_amount по убыванию, null — в конце", () => {
    renderPanel();

    const roots = screen
      .getAllByTestId(/^unallocated-section-\d+$/)
      .filter((el) => el.parentElement?.closest('[data-testid^="unallocated-section-"]') === null);
    // 5002 (subtree_amount "115000.00") — раньше 5001 (subtree_amount null).
    const ids = roots.map((el) => el.getAttribute("data-testid"));
    expect(ids.indexOf("unallocated-section-5002")).toBeLessThan(
      ids.indexOf("unallocated-section-5001")
    );
  });

  it("сравнение subtree_amount — точное десятичное (BigInt), а не Number(): порядок по убыванию защищён и на разнице за 15-м знаком", () => {
    /*
      Finding I-1 (ревью 1): в `sampleProjectPassport` у сравниваемых
      сиблингов всегда хотя бы один операнд null, поэтому
      `compareDecimalStrings` не вызывается ни разу за весь прогон — можно
      развернуть `-cmp` в `cmp` (сортировка стала бы по возрастанию) или
      подменить BigInt на `Number()`, и все 308 тестов остаются зелёными.

      Три сиблинга-корня, все с известным subtree_amount, порядок массива
      (7001, 7002, 7003) СОВПАДАЕТ с порядком id по возрастанию — если
      сортировки нет вовсе или она развёрнута, порядок на экране совпал бы с
      порядком массива/id, а не с ожидаемым убыванием.

      7002 ("1000000000000000.01") и 7003 ("1000000000000000.02") различаются
      только за 15-м значащим разрядом — Number() схлопывает оба в одно и то
      же значение (проверено: Number("1000000000000000.01") ===
      Number("1000000000000000.02") ⇒ true в Node). При такой подмене
      компаратор вернул бы 0 для этой пары, а `Array.prototype.sort`
      (стабильная сортировка, ES2019+) оставил бы порядок ПРИХОДА — 7002 перед
      7003, что противоречит ожидаемому убыванию (7003 больше и обязан идти
      первым).
    */
    const passportWithComparableSiblings: ProjectPassport = {
      ...sampleProjectPassport,
      unallocated: {
        ...sampleProjectPassport.unallocated,
        sections: [
          {
            position_item_id: 7001,
            parent_position_item_id: null,
            number: "9.1",
            title: "Раздел «Малая сумма»",
            depth: 0,
            amount: null,
            subtree_amount: "50.00",
            rows: 1,
            rows_priced: 0,
            rows_not_finite: 0,
            smr_article_raw: null,
          },
          {
            position_item_id: 7002,
            parent_position_item_id: null,
            number: "9.2",
            title: "Раздел «Разница за 15-м знаком, меньшая»",
            depth: 0,
            amount: null,
            subtree_amount: "1000000000000000.01",
            rows: 1,
            rows_priced: 0,
            rows_not_finite: 0,
            smr_article_raw: null,
          },
          {
            position_item_id: 7003,
            parent_position_item_id: null,
            number: "9.3",
            title: "Раздел «Разница за 15-м знаком, большая»",
            depth: 0,
            amount: null,
            subtree_amount: "1000000000000000.02",
            rows: 1,
            rows_priced: 0,
            rows_not_finite: 0,
            smr_article_raw: null,
          },
        ],
      },
    };
    renderPanel(passportWithComparableSiblings);

    const order = screen
      .getAllByTestId(/^unallocated-section-\d+$/)
      .map((el) => el.getAttribute("data-testid"));
    expect(order).toEqual([
      "unallocated-section-7003",
      "unallocated-section-7002",
      "unallocated-section-7001",
    ]);
  });

  it("раздел с несуществующим родителем не пропадает — рисуется верхним уровнем", () => {
    /*
      Finding I-4 (ревью 1). `byId.get(parent)?.children.push(node)` раньше
      молча проглатывал узел, если его `parent_position_item_id` не находился
      среди `sections[]`: узел оставался в `byId`, но не попадал ни в `roots`,
      ни в чей-либо `children` — исчезал с экрана без диагностики. По правилам
      бэкенда сироты не бывает (родители перевешиваются в выборку), но именно
      поэтому появление сироты нельзя проглатывать молча.
    */
    const passportWithOrphan: ProjectPassport = {
      ...sampleProjectPassport,
      unallocated: {
        ...sampleProjectPassport.unallocated,
        sections: [
          {
            position_item_id: 8001,
            parent_position_item_id: 9999, // 9999 не существует в sections[].
            number: "9.9",
            title: "Раздел «Сирота»",
            depth: 3,
            amount: "1.00",
            subtree_amount: "1.00",
            rows: 1,
            rows_priced: 1,
            rows_not_finite: 0,
            smr_article_raw: null,
          },
        ],
      },
    };
    renderPanel(passportWithOrphan);

    const node = screen.getByTestId("unallocated-section-8001");
    expect(node).toBeInTheDocument();
    // Верхний уровень — над узлом нет другого узла unallocated-section-*.
    expect(node.parentElement?.closest('[data-testid^="unallocated-section-"]')).toBeNull();
  });

  it("вершина без своих денег показывает сумму поддерева, а собственной строки нет", () => {
    renderPanel();

    // 5002: amount === null (своих расценённых позиций нет), subtree_amount —
    // единственное известное слагаемое всего дерева, "115000.00". Своей строки
    // быть не должно (`amount === null`), но свёртка поддерева — ПЕРВИЧНАЯ и
    // рендерится всегда, независимо от того, есть своя сумма или нет (правка
    // ревью 1, finding C-1) — здесь она просто единственная.
    const node = screen.getByTestId("unallocated-section-5002");
    expect(within(node).getByTestId("subtree-amount-5002")).toHaveTextContent("115 000,00 ₽");
    expect(within(node).queryByTestId("own-amount-5002")).not.toBeInTheDocument();
  });

  it("лист со своими расценёнными позициями показывает собственную сумму РЯДОМ со сводом поддерева", () => {
    renderPanel();

    // 5003 — лист: amount === subtree_amount === "115000.00" (у листа своя
    // сумма и свёртка поддерева совпадают численно, но это ДВА элемента, не
    // один — правка ревью 1, finding C-1: до правки own-amount и
    // subtree-amount были взаимоисключающим выбором, и для узла со своими
    // деньгами testid subtree-amount-5003 не рендерился вовсе).
    const node = screen.getByTestId("unallocated-section-5003");
    expect(within(node).getByTestId("subtree-amount-5003")).toHaveTextContent("115 000,00 ₽");
    expect(within(node).getByTestId("own-amount-5003")).toHaveTextContent("115 000,00 ₽");
  });

  it("вершина со своими деньгами И нераспределённым поддеревом показывает ОБЕ суммы, разные и различимые", () => {
    /*
      Дискриминирующий тест finding C-1 (ревью 1). `sampleProjectPassport` не
      может выразить этот случай — там своя сумма и свёртка поддерева либо обе
      null (5001), либо совпадают (5002 null/известна, 5003 известна/известна
      как у листа). Локальная надстройка — не правка общей фикстуры
      (task-8-controller-notes: «adding NEW fixtures… build them as local
      derivations»).

      6001 — корень со своей расценённой суммой (100.00) и ребёнком 6002,
      несущим 500000.00 своей же расценённой суммы: subtree_amount у 6001
      (500100.00) обязан включать обе. Раньше `hasOwnAmount` выбирал бы own
      (100,00 ₽) и молчал о своде поддерева — аналитик увидел бы вершину,
      отсортированную по 500 100 ₽ (цена решения), но подписанную 100 ₽.
    */
    const passportWithBothSums: ProjectPassport = {
      ...sampleProjectPassport,
      unallocated: {
        ...sampleProjectPassport.unallocated,
        sections: [
          {
            position_item_id: 6001,
            parent_position_item_id: null,
            number: "9.1",
            title: "Раздел «Своя сумма и большое поддерево»",
            depth: 0,
            amount: "100.00",
            subtree_amount: "500100.00",
            rows: 1,
            rows_priced: 1,
            rows_not_finite: 0,
            smr_article_raw: null,
          },
          {
            position_item_id: 6002,
            parent_position_item_id: 6001,
            number: "9.1.1",
            title: "Подраздел «Даёт деньги поддереву»",
            depth: 1,
            amount: "500000.00",
            subtree_amount: "500000.00",
            rows: 1,
            rows_priced: 1,
            rows_not_finite: 0,
            smr_article_raw: null,
          },
        ],
      },
    };
    renderPanel(passportWithBothSums);

    const node = screen.getByTestId("unallocated-section-6001");
    const subtree = within(node).getByTestId("subtree-amount-6001");
    const own = within(node).getByTestId("own-amount-6001");
    expect(subtree).toHaveTextContent("500 100,00 ₽");
    expect(own).toHaveTextContent("100,00 ₽");
    // Ни один не выглядит как другой — иначе аналитик спутал бы «цену решения»
    // (свод поддерева) с суммой своих расценённых позиций.
    expect(subtree.textContent).not.toBe(own.textContent);
  });

  it("отступ вложенного узла больше отступа родителя", () => {
    renderPanel();

    const parentRow = screen.getByTestId("unallocated-section-row-5002");
    const childRow = screen.getByTestId("unallocated-section-row-5003");
    const parentIndent = Number(parentRow.style.paddingLeft.replace("px", ""));
    const childIndent = Number(childRow.style.paddingLeft.replace("px", ""));
    expect(childIndent).toBeGreaterThan(parentIndent);
  });
});

describe("Панель-верстак разноса: выбор статьи", () => {
  it("кнопка выбора статьи различима по accessible-имени — несёт номер и название раздела", () => {
    /*
      Finding I-3 (ревью 1), закрытие пробела ревью 2: `getByRole("button", {
      name: /снять/i })`-подобный regex-запрос удовлетворяется голой подписью
      («Отнести на статью…») ровно так же, как и подписью с разделом —
      поэтому раньше НИЧТО не отличало кнопку по имени, только по testid.
      Здесь запрос — ТОЧНОЕ имя, включающее номер и название конкретного
      раздела; при удалении `aria-label` (или при повторном использовании
      одной и той же подписи на все строки) такой запрос не найдёт элемент
      вовсе и тест покраснеет — проверено построением (см. отчёт).
    */
    renderPanel();

    const picker5001 = screen.getByRole("button", {
      name: "Отнести на статью: 5.1 Раздел «Устройство временных сооружений»",
    });
    const picker5002 = screen.getByRole("button", {
      name: "Отнести на статью: 5.2 Раздел «Демонтажные работы»",
    });

    expect(picker5001).toHaveAttribute("data-testid", "pick-category-5001");
    expect(picker5002).toHaveAttribute("data-testid", "pick-category-5002");
    expect(picker5001).not.toBe(picker5002);
  });

  it("в выборе статьи есть статьи, которых нет в видимом дереве паспорта", async () => {
    // Источник вариантов — category_options, а НЕ categories: build_tree прячет
    // вложенные узлы дерева без строк, и код "10.01" (id 16) — ровно такой узел,
    // отсутствующий в categories, но обязанный быть доступным для выбора.
    renderPanel();

    await userEvent.click(screen.getByTestId("pick-category-5001"));

    const visibleCodes = new Set(sampleProjectPassport.categories.map((c) => c.code));
    const hidden = sampleProjectPassport.category_options.filter((o) => !visibleCodes.has(o.code));
    expect(hidden.length).toBeGreaterThan(0);
    expect(screen.getByText(hidden[0].title)).toBeInTheDocument();
  });

  it("поиск находит статью по коду", async () => {
    renderPanel();

    await userEvent.click(screen.getByTestId("pick-category-5001"));
    const input = screen.getByRole("combobox");
    await userEvent.type(input, "04.02");

    // Код "04.02" — «Пусконаладочные работы»; ищем по коду, находим по названию.
    expect(await screen.findByText("Пусконаладочные работы")).toBeInTheDocument();
    // Заведомо непричастный вариант (нет цифры "4" в коде "08" и нет её в
    // названии "Слаботочные системы") отфильтрован.
    expect(screen.queryByText("Слаботочные системы")).not.toBeInTheDocument();
  });

  it("поиск находит статью по названию", async () => {
    renderPanel();

    await userEvent.click(screen.getByTestId("pick-category-5001"));
    const input = screen.getByRole("combobox");
    await userEvent.type(input, "Слаботочные");

    expect(await screen.findByText("Слаботочные системы")).toBeInTheDocument();
    expect(screen.queryByText("Пусконаладочные работы")).not.toBeInTheDocument();
  });

  it("выбор статьи вызывает мутацию с верными аргументами", async () => {
    const received: { estimateId: string; positionItemId: string; body: unknown } = {
      estimateId: "",
      positionItemId: "",
      body: null,
    };
    server.use(
      http.put(CATEGORY_OVERRIDE_URL, async ({ params, request }) => {
        received.estimateId = String(params.estimateId);
        received.positionItemId = String(params.positionItemId);
        received.body = await request.json();
        return HttpResponse.json({
          chapters_updated: 1,
          additional_works_updated: 0,
          chapters_manual: 1,
        });
      })
    );
    renderPanel();

    await userEvent.click(screen.getByTestId("pick-category-5001"));
    await userEvent.click(await screen.findByText("Пусконаладочные работы"));

    await waitFor(() => expect(received.positionItemId).toBe("5001"));
    expect(received.estimateId).toBe("11");
    // category_options: id 8, code "04.02" — «Пусконаладочные работы».
    expect(received.body).toMatchObject({ work_category_id: 8 });
  });
});

describe("Панель-верстак разноса: разнесено вручную", () => {
  it("запись несёт статью, автора, дату, примечание и кнопку «снять»", () => {
    renderPanel();

    const row = screen.getByTestId("manual-assignment-5004");
    expect(within(row).getAllByText(/Прочие работы/).length).toBeGreaterThan(0);
    expect(within(row).getByText(/analyst@example\.com/)).toBeInTheDocument();
    // День/месяц зависят от локальной таймзоны прогона (`formatDate` берёт
    // `toLocaleDateString` на локальном времени, а `vitest.config.ts` TZ не
    // фиксирует) — `assigned_at` "2026-08-05T09:15:00Z" при западных
    // смещениях сдвигается на 04.08. Год не сдвинется ни при каком реальном
    // смещении, поэтому паттерн ДД.ММ.2026 пинает «дата отрендерена в формате
    // дд.мм.гггг», не зависящем от TZ, без превращения в тавтологию через
    // повторный вызов formatDate.
    expect(within(row).getByText(/\d{2}\.\d{2}\.2026/)).toBeInTheDocument();
    expect(
      within(row).getByText(/Отнесено на «Прочие работы» по решению главного инженера/)
    ).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /снять/i })).toBeInTheDocument();
  });

  it("«снять» вызывает мутацию отмены с верными аргументами", async () => {
    const received: { estimateId: string; positionItemId: string } = {
      estimateId: "",
      positionItemId: "",
    };
    server.use(
      http.delete(CATEGORY_OVERRIDE_URL, ({ params }) => {
        received.estimateId = String(params.estimateId);
        received.positionItemId = String(params.positionItemId);
        return HttpResponse.json({
          chapters_updated: 1,
          additional_works_updated: 0,
          chapters_manual: 0,
        });
      })
    );
    renderPanel();

    const row = screen.getByTestId("manual-assignment-5004");
    await userEvent.click(within(row).getByRole("button", { name: /снять/i }));

    await waitFor(() => expect(received.positionItemId).toBe("5004"));
    expect(received.estimateId).toBe("11");
  });

  it("кнопка «снять» различима по accessible-имени — несёт номер и название раздела", () => {
    /*
      Finding I-3 (ревью 1), закрытие пробела ревью 2: прежний запрос
      `getByRole("button", { name: /снять/i })` удовлетворяется голой
      подписью «Снять» точно так же, как подписью с разделом — regex не
      различал строки вовсе, различал их только `data-testid`. Здесь на
      панели ДВА решения (5004 из фикстуры плюс локальная надстройка 5005 —
      `sampleProjectPassport.manual_assignments` несёт только одно, а
      различимость по имени можно показать только при двух), и запрос —
      ТОЧНОЕ имя с номером и названием конкретного раздела. Удаление
      `aria-label` или повтор одной подписи на обе строки — и запрос не
      найдёт элемент вовсе (проверено построением, см. отчёт).
    */
    const secondAssignment = {
      ...sampleProjectPassport.manual_assignments[0],
      position_item_id: 5005,
      number: "5.4",
      title: "Раздел «Устройство площадки»",
      work_category_id: 6,
      category_code: "04",
      category_title: "Инженерные сети",
      assigned_by_email: "other@example.com",
      note: null,
    };
    const passportWithTwoAssignments: ProjectPassport = {
      ...sampleProjectPassport,
      manual_assignments: [...sampleProjectPassport.manual_assignments, secondAssignment],
    };
    renderPanel(passportWithTwoAssignments);

    const remove5004 = screen.getByRole("button", {
      name: "Снять решение по разделу: 5.3 Раздел «Устройство эстакад»",
    });
    const remove5005 = screen.getByRole("button", {
      name: "Снять решение по разделу: 5.4 Раздел «Устройство площадки»",
    });

    expect(remove5004).toHaveAttribute("data-testid", "manual-remove-5004");
    expect(remove5005).toHaveAttribute("data-testid", "manual-remove-5005");
    expect(remove5004).not.toBe(remove5005);
  });
});

describe("Панель-верстак разноса: зажим длинного наименования", () => {
  it("длинное наименование раздела зажато line-clamp-2 и не несёт класса block", () => {
    const passportWithLongTitle: ProjectPassport = {
      ...sampleProjectPassport,
      unallocated: {
        ...sampleProjectPassport.unallocated,
        sections: sampleProjectPassport.unallocated.sections.map((section) =>
          section.position_item_id === 5001 ? { ...section, title: longJobTitle } : section
        ),
      },
    };
    renderPanel(passportWithLongTitle);

    const title = screen.getByTestId("unallocated-section-title-5001");
    expect(title.className).toContain("line-clamp-2");
    expect(title.className.split(/\s+/)).not.toContain("block");
  });
});
