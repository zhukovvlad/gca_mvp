import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import FamiliesPage from "@/pages/families/FamiliesPage";
import { ContextsTab } from "@/pages/families/ContextsTab";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { ContextRow, WorkFamily } from "@/types/domain";

/**
 * Фильтр «Семья» вкладки «Контексты» и переход «Показать контексты: N» из панели
 * семьи (правка стенда 16.10.2026). Утверждения о запросе читаются из журнала
 * параметров КАЖДОГО запроса, а не из строк на экране.
 */

function family(id: number, overrides: Partial<WorkFamily>): WorkFamily {
  return {
    ...handlerState.workFamilies[0],
    id,
    status: "active",
    unit_id: 5,
    unit_code: "M2",
    unit_symbol: "м²",
    context_count: 0,
    ...overrides,
  };
}

/** Подменяет `GET /contexts` синтетической выдачей и возвращает журнал параметров запросов. */
function useContextRows(rows: ContextRow[]): URLSearchParams[] {
  const requests: URLSearchParams[] = [];
  server.use(
    http.get("/api/v1/semantic/contexts", ({ request }) => {
      const url = new URL(request.url);
      requests.push(url.searchParams);
      const limit = Number(url.searchParams.get("limit") ?? 20);
      const offset = Number(url.searchParams.get("offset") ?? 0);
      return HttpResponse.json({
        items: rows.slice(offset, offset + limit),
        total: rows.length,
        limit,
        offset,
      });
    })
  );
  return requests;
}

function manyRows(n: number): ContextRow[] {
  return Array.from({ length: n }, (_, i) => {
    const id = 9000 + i + 1;
    return {
      id,
      bucket_id: id,
      is_default: true,
      semantic_kind: "WORK",
      semantic_kind_source: "rule",
      name_role: "WORK",
      name_role_source: "rule",
      semantic_state: "SUGGESTED",
      comparability_reason: null,
      work_family_id: null,
      family_title: null,
      work_category_id: 77,
      work_category_code: "05.02.03",
      work_category_title: "Оштукатуривание",
      catalog_position_id: 90000 + i,
      standard_job_title: `Синтетическая работа №${i + 1}`,
      unit_code: "м2",
      unit_symbol: "м²",
      archived_at: null,
      member_count: 1,
      has_stale_members: false,
      has_conflicting_members: false,
      work_category_path: [],
    } as ContextRow;
  });
}

function last(requests: URLSearchParams[]): URLSearchParams {
  expect(requests.length).toBeGreaterThan(0);
  return requests[requests.length - 1];
}

describe("ContextsTab — фильтр «Семья»", () => {
  it("поиск по названию («е» вместо «ё») находит семью; выбор уходит в запрос и возвращает на первую страницу", async () => {
    const user = userEvent.setup();
    handlerState.workFamilies.push(family(501, { title: "Ёлочные работы" }));
    const requests = useContextRows(manyRows(45));
    renderWithProviders(<ContextsTab />);
    await screen.findByText("Синтетическая работа №1");

    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(last(requests).get("offset")).toBe("20"));
    expect(last(requests).has("work_family_id")).toBe(false);

    await user.click(screen.getByRole("combobox", { name: "Семья" }));
    await user.type(screen.getByPlaceholderText("Найти семью…"), "елочные");
    const option = await screen.findByRole("option", { name: /Ёлочные работы/ });
    expect(screen.queryByRole("option", { name: /Кровельные работы/ })).not.toBeInTheDocument();
    await user.click(option);

    await waitFor(() => expect(last(requests).get("work_family_id")).toBe("501"));
    expect(last(requests).get("offset")).toBe("0");
    expect(screen.getByRole("combobox", { name: "Семья" })).toHaveTextContent("Ёлочные работы");
    // Идентификатор семьи на экран не выходит.
    expect(screen.queryByText("501")).not.toBeInTheDocument();
  });

  it("сброс убирает work_family_id из запроса", async () => {
    const user = userEvent.setup();
    const requests = useContextRows(manyRows(3));
    renderWithProviders(<ContextsTab />);
    await screen.findByText("Синтетическая работа №1");

    await user.click(screen.getByRole("combobox", { name: "Семья" }));
    await user.click(await screen.findByRole("option", { name: /Кровельные работы/ }));
    await waitFor(() => expect(last(requests).get("work_family_id")).toBe("43"));

    await user.click(screen.getByRole("button", { name: "Сбросить семью" }));

    await waitFor(() => expect(last(requests).has("work_family_id")).toBe(false));
    expect(screen.getByRole("combobox", { name: "Семья" })).toHaveTextContent("Любая семья");
    expect(screen.queryByRole("button", { name: "Сбросить семью" })).not.toBeInTheDocument();
  });

  it("две семьи с одним названием в разных единицах различимы подсказкой единицы; у не активной — и статусом", async () => {
    const user = userEvent.setup();
    handlerState.workFamilies.push(
      family(601, { title: "Гидроизоляция", unit_symbol: "м²", status: "active" }),
      family(602, { title: "Гидроизоляция", unit_symbol: "м³", status: "draft" })
    );
    useContextRows(manyRows(1));
    renderWithProviders(<ContextsTab />);
    await screen.findByText("Синтетическая работа №1");

    await user.click(screen.getByRole("combobox", { name: "Семья" }));
    const options = await screen.findAllByRole("option", { name: /^Гидроизоляция/ });

    expect(options).toHaveLength(2);
    const texts = options.map((o) => o.textContent ?? "");
    expect(texts.some((t) => t.includes("м²") && !t.includes("черновик"))).toBe(true);
    expect(texts.some((t) => t.includes("м³") && t.includes("черновик"))).toBe(true);
  });
});

/**
 * Держит `ContextsTab` СМОНТИРОВАННОЙ и шлёт ей переход так же, как `FamiliesPage`
 * (`focusFamilyId` + `onFocusShown`, снимающий запрос). На самой странице вкладка
 * перемонтируется при переключении (`TabsContent` Base UI без `keepMounted`), и
 * сброс фильтров получается даром — тесты страницы ниже его поэтому не держат.
 * Здесь держится сам механизм вкладки (ревью пачки 8).
 */
function MountedHarness({ familyId }: { familyId: number }) {
  const [focus, setFocus] = useState<number | null>(null);
  return (
    <>
      <button type="button" onClick={() => setFocus(familyId)}>
        go
      </button>
      <ContextsTab focusFamilyId={focus} onFocusShown={() => setFocus(null)} />
    </>
  );
}

describe("ContextsTab — переход с семьёй при смонтированной вкладке", () => {
  it("сбрасывает поиск, «Вид» и флажки и ставит семью на первую страницу", async () => {
    const user = userEvent.setup();
    const requests = useContextRows(manyRows(45));
    renderWithProviders(<MountedHarness familyId={1} />);
    await screen.findByText("Синтетическая работа №1");

    await user.click(screen.getByRole("combobox", { name: "Вид" }));
    await user.click(await screen.findByRole("option", { name: "работа" }));
    await user.click(screen.getByRole("checkbox", { name: "устаревшие" }));
    await user.type(screen.getByLabelText("Поиск по написанию каталога"), "Синт");
    await user.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => {
      const r = last(requests);
      expect(r.get("semantic_kind")).toBe("WORK");
      expect(r.get("has_stale_members")).toBe("true");
      expect(r.get("catalog_query")).toBe("Синт");
      expect(r.get("offset")).toBe("20");
    });

    await user.click(screen.getByRole("button", { name: "go" }));

    await waitFor(() => {
      const r = last(requests);
      expect(r.get("work_family_id")).toBe("1");
      expect(r.has("catalog_query")).toBe(false);
    });
    const final = last(requests);
    expect(final.has("semantic_kind")).toBe(false);
    expect(final.has("has_stale_members")).toBe(false);
    expect(final.get("offset")).toBe("0");
    expect(screen.getByLabelText("Поиск по написанию каталога")).toHaveValue("");
    expect(screen.getByRole("combobox", { name: "Семья" })).toHaveTextContent("Семья работ №1");
  });

  it("повторный переход к той же семье после ручного сброса срабатывает", async () => {
    const user = userEvent.setup();
    const requests = useContextRows(manyRows(3));
    renderWithProviders(<MountedHarness familyId={1} />);
    await screen.findByText("Синтетическая работа №1");

    await user.click(screen.getByRole("button", { name: "go" }));
    await waitFor(() => expect(last(requests).get("work_family_id")).toBe("1"));

    await user.click(screen.getByRole("button", { name: "Сбросить семью" }));
    await waitFor(() => expect(last(requests).has("work_family_id")).toBe(false));

    await user.click(screen.getByRole("button", { name: "go" }));
    await waitFor(() => expect(last(requests).get("work_family_id")).toBe("1"));
    expect(screen.getByRole("combobox", { name: "Семья" })).toHaveTextContent("Семья работ №1");
  });
});

describe("FamiliesPage — «Показать контексты: N»", () => {
  it("переключает на «Контексты» с этой семьёй и сбрасывает прежние фильтры и поиск", async () => {
    const user = userEvent.setup();
    const requests = useContextRows(manyRows(3));
    renderWithProviders(<FamiliesPage />);

    // Включаем фильтр «Вид» и поиск на вкладке «Контексты».
    await user.click(screen.getByRole("tab", { name: "Контексты" }));
    await screen.findByText("Синтетическая работа №1");
    await user.click(screen.getByRole("combobox", { name: "Вид" }));
    await user.click(await screen.findByRole("option", { name: "работа" }));
    await user.type(screen.getByLabelText("Поиск по написанию каталога"), "Синт");
    await waitFor(() => {
      expect(last(requests).get("semantic_kind")).toBe("WORK");
      expect(last(requests).get("catalog_query")).toBe("Синт");
    });

    // Семья 1 — черновик с двумя контекстами.
    await user.click(screen.getByRole("tab", { name: "Семьи" }));
    await user.click(await screen.findByText("Семья работ №1"));
    await user.click(await screen.findByRole("button", { name: "Показать контексты: 2" }));

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Контексты" })).toHaveAttribute("aria-selected", "true")
    );
    await waitFor(() => expect(last(requests).get("work_family_id")).toBe("1"));
    const final = last(requests);
    expect(final.has("semantic_kind")).toBe(false);
    expect(final.has("catalog_query")).toBe(false);
    expect(final.get("offset")).toBe("0");
    expect(screen.getByRole("combobox", { name: "Семья" })).toHaveTextContent("Семья работ №1");
    // Ни одного промежуточного запроса «семья + прежний поиск/вид»: вкладка
    // монтируется заново, и `useDebounce` начинает с пустой строки. Если
    // вкладкам когда-нибудь дадут `keepMounted`, здесь станет красно — тогда
    // отложенный поиск придётся сбрасывать мимо задержки (ревью пачки 8).
    const withFamily = requests.filter((r) => r.get("work_family_id") === "1");
    expect(withFamily.every((r) => !r.has("catalog_query") && !r.has("semantic_kind"))).toBe(true);
  });

  it("повторный переход к той же семье после ручного сброса срабатывает", async () => {
    const user = userEvent.setup();
    const requests = useContextRows(manyRows(3));
    renderWithProviders(<FamiliesPage />);

    await user.click(await screen.findByText("Семья работ №1"));
    await user.click(await screen.findByRole("button", { name: "Показать контексты: 2" }));
    await waitFor(() => expect(last(requests).get("work_family_id")).toBe("1"));

    await user.click(screen.getByRole("button", { name: "Сбросить семью" }));
    await waitFor(() => expect(last(requests).has("work_family_id")).toBe(false));

    await user.click(screen.getByRole("tab", { name: "Семьи" }));
    await user.click(await screen.findByText("Семья работ №1"));
    await user.click(await screen.findByRole("button", { name: "Показать контексты: 2" }));

    await waitFor(() => expect(last(requests).get("work_family_id")).toBe("1"));
    expect(screen.getByRole("combobox", { name: "Семья" })).toHaveTextContent("Семья работ №1");
  });

  it("у семьи без контекстов кнопки нет", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FamiliesPage />);

    await user.click(await screen.findByText("Семья работ №3"));
    const panelTitle = await screen.findByRole("heading", { name: /Семья «Семья работ №3»/ });

    expect(panelTitle).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Показать контексты/ })).not.toBeInTheDocument();
    // Для порядка: у семьи с контекстами она есть.
    await user.click(screen.getByText("Семья работ №1"));
    expect(
      within(document.body).getByRole("button", { name: "Показать контексты: 2" })
    ).toBeInTheDocument();
  });
});
