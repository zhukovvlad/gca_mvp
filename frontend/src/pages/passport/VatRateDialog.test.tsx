import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { VatRateDialog } from "./VatRateDialog";
import { sampleProjectPassport } from "@/test/fixtures";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import type { ProjectPassportEstimate } from "@/types/domain";

/**
 * Диалог правки ставок НДС сметы (спека пересчёта §2.7, задача 10).
 *
 * **Расхождение с текстом брифа, зафиксированное явно (см. отчёт задачи):**
 * пример теста в брифе кликает «Сохранить»/читает поле «Ставка показа» БЕЗ
 * предварительного открытия диалога — `DialogContent` через `@base-ui/react`
 * рендерится в портал только когда диалог открыт (тот же Radix-подобный
 * механизм, что у остальных диалогов проекта), поэтому такие запросы ничего
 * не находят ни при какой реализации. Здесь каждый тест сперва кликает
 * триггер «Изменить ставку». Второе расхождение: бриф проверяет отправленное
 * тело через `vi.fn()` `patch`, не подключённый к компоненту, — диалог шлёт
 * запрос РЕАЛЬНОЙ мутацией `useSetEstimateVat` (задача 9), и тело перехвачено
 * здесь через MSW-хендлер, а не через мок.
 */

const PATCH_URL = "/api/v1/estimates/:id/vat";

const baseEstimate = sampleProjectPassport.estimate!;

const estimateWithoutRate: ProjectPassportEstimate = {
  ...baseEstimate,
  vat_rate: null,
  vat_rate_base_override: null,
  vat_rate_target: null,
  vat_display_rate: null,
};

const estimateWithRate: ProjectPassportEstimate = {
  ...baseEstimate,
  vat_rate: "20",
  vat_rate_base_override: null,
  vat_rate_target: null,
  vat_display_rate: "20",
};

const estimateWithTarget: ProjectPassportEstimate = {
  ...baseEstimate,
  vat_rate: "20",
  vat_rate_base_override: null,
  vat_rate_target: "16",
  vat_display_rate: "16",
};

/** Успешный ответ-заглушка PATCH — тело эха не имеет значения, важен запрос. */
function stubSuccess() {
  let body: unknown;
  server.use(
    http.patch(PATCH_URL, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({
        estimate_id: 600,
        vat_rate_base_override: null,
        vat_rate_target: null,
        vat_rate_updated_at: "2026-08-13T10:00:00Z",
      });
    })
  );
  return () => body;
}

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /изменить ставку/i }));
}

describe("VatRateDialog: правка ставок НДС", () => {
  // Краснеет от: снятия `disabled={base === null}` с поля «Ставка показа» —
  // без него поле осталось бы включённым, `toBeDisabled()` бы не сработал;
  // снятия блока `{base === null && <p role="note">…}` убрало бы объяснение.
  it("без базы поле показа заблокировано и объясняет причину", async () => {
    const user = userEvent.setup();
    renderWithProviders(<VatRateDialog estimate={estimateWithoutRate} contractId={1} />);
    await openDialog(user);

    expect(screen.getByLabelText(/ставка показа/i)).toBeDisabled();
    expect(screen.getByText(/сначала объявите базовую ставку/i)).toBeInTheDocument();
  });

  // Краснеет от: удаления диффа `changedOnly` и отправки обоих полей целиком
  // (например, `submit({ base_override: asValue(baseDraft), target: asValue(targetDraft) })`
  // без сравнения с исходным эстимейтом) — тело несло бы лишний `base_override`.
  it("отправляет только изменённое поле, а не оба", async () => {
    const getBody = stubSuccess();
    const user = userEvent.setup();
    renderWithProviders(<VatRateDialog estimate={estimateWithRate} contractId={1} />);
    await openDialog(user);

    await user.clear(screen.getByLabelText(/ставка показа/i));
    await user.type(screen.getByLabelText(/ставка показа/i), "16");
    await user.click(screen.getByRole("button", { name: /^сохранить$/i }));

    await waitFor(() => expect(getBody()).toBeDefined());
    expect(getBody()).toEqual({ target: "16" });
  });

  // Краснеет от: замены литерала кнопки «Снять ставку показа» на
  // `submit({ target: "" })` или на `submit(changedOnly(...))` — оба варианта
  // отправили бы что угодно, кроме буквального `null`, требуемого §2.7 для
  // «снять», в отличие от «не менять» (отсутствующее поле).
  it("снятие ставки отправляет null, а не пустую строку", async () => {
    const getBody = stubSuccess();
    const user = userEvent.setup();
    renderWithProviders(<VatRateDialog estimate={estimateWithTarget} contractId={1} />);
    await openDialog(user);

    await user.click(screen.getByRole("button", { name: /снять ставку показа/i }));

    await waitFor(() => expect(getBody()).toBeDefined());
    expect(getBody()).toEqual({ target: null });
  });

  // Краснеет от: `submit(changedOnly(estimate, Number(baseDraft) as any, targetDraft))`
  // или любого `Number(targetDraft)` на входе в тело запроса — значение
  // приехало бы числом `16.5`, а не строкой "16.5" (§3 запрещает `float`
  // в ставке на любом слое).
  it("отправляет строку, а не число: float в ставке запрещён", async () => {
    const getBody = stubSuccess();
    const user = userEvent.setup();
    renderWithProviders(<VatRateDialog estimate={estimateWithRate} contractId={1} />);
    await openDialog(user);

    await user.type(screen.getByLabelText(/ставка показа/i), "16.5");
    await user.click(screen.getByRole("button", { name: /^сохранить$/i }));

    await waitFor(() => expect(getBody()).toBeDefined());
    const sent = getBody() as { target: unknown };
    expect(sent.target).toBe("16.5");
    expect(typeof sent.target).toBe("string");
  });

  // Краснеет от: `onError: toastApiError` без собственного `role="alert"` в
  // диалоге (только тост) либо от жёсткого текста `"Произошла ошибка"` вместо
  // `apiErrorDetail(mutation.error)` — текст сервера не дошёл бы до экрана.
  it("показывает текст 422 сервера, а не общее «ошибка»", async () => {
    server.use(
      http.patch(PATCH_URL, () =>
        HttpResponse.json({ detail: "Сначала объявите базовую ставку." }, { status: 422 })
      )
    );
    const user = userEvent.setup();
    renderWithProviders(<VatRateDialog estimate={estimateWithRate} contractId={1} />);
    await openDialog(user);

    await user.click(screen.getByRole("button", { name: /^сохранить$/i }));

    // Область поиска — ВНУТРИ диалога: `useSetEstimateVat` (onError:
    // `toastApiError`) кладёт тот же текст ещё и в тост-уведомление, и
    // глобальный запрос был бы неоднозначен между двумя каналами.
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText(/сначала объявите базовую ставку/i)).toBeInTheDocument();
  });

  // Краснеет от: удаления `if (!isAdmin) return null;` — триггер остался бы
  // виден члену команды, у которого на бэкенде (`require_admin`) этого права
  // нет вовсе.
  it("member не видит управления ставкой", () => {
    renderWithProviders(<VatRateDialog estimate={estimateWithRate} contractId={1} />, {
      initialUser: { id: 2, email: "member@example.com", role: "member" },
    });

    expect(screen.queryByRole("button", { name: /изменить ставку/i })).not.toBeInTheDocument();
  });
});
