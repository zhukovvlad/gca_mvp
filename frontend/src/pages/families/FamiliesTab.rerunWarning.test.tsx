import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { FamiliesTab } from "@/pages/families/FamiliesTab";
import { qk } from "@/services/queryKeys";
import { handlerState } from "@/test/handlers";
import { server } from "@/test/server";
import { createTestQueryClient, renderWithProviders } from "@/test/utils";

/**
 * Предупреждение «идёт перезапрос» в окнах действий над семьёй, меняющих список семей в
 * теле запроса предложения: правка названия или определения, архивирование, слияние.
 * Активная семья фикстуры — «Кровельные работы» (id 43, единица м³).
 */

const WARNING_7 =
  "В единице «м³» идёт перезапрос: 7 заданий предложений ещё не выполнены. Это изменение отменит их, а уже полученные, но не разобранные предложения скроются — единицу придётся перезапросить заново.";

function setBusy(familyId: number, busy: number) {
  handlerState.workFamilies.find((f) => f.id === familyId)!.unit_busy_jobs = busy;
}

async function openActivePanel(queryClient: QueryClient = createTestQueryClient()) {
  const user = userEvent.setup();
  renderWithProviders(<FamiliesTab />, { queryClient });
  await waitFor(() => expect(screen.getByText("Семья работ №3")).toBeInTheDocument());
  await user.click(screen.getByRole("combobox", { name: "Статус" }));
  await user.click(await screen.findByRole("option", { name: "активна" }));
  await user.click(await screen.findByText("Кровельные работы"));
  await screen.findByLabelText("Определение");
  return user;
}

describe("FamiliesTab — предупреждение о сбросе перезапроса", () => {
  afterEach(() => {
    localStorage.clear();
  });

  describe("правка названия и определения", () => {
    it("правка названия при идущем перезапросе предупреждает числом заданий; без правки предупреждения нет", async () => {
      setBusy(43, 7);
      const user = await openActivePanel();
      expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();

      await user.type(screen.getByLabelText("Название"), " 2");

      expect(await screen.findByText(WARNING_7)).toBeInTheDocument();
    });

    it("правка определения при идущем перезапросе предупреждает; возврат прежнего текста снимает предупреждение", async () => {
      setBusy(43, 7);
      const user = await openActivePanel();

      await user.type(screen.getByLabelText("Определение"), "!");
      expect(await screen.findByText(WARNING_7)).toBeInTheDocument();
      await user.type(screen.getByLabelText("Определение"), "{Backspace}");

      await waitFor(() => expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument());
    });

    it("правка названия без идущего перезапроса предупреждения не даёт", async () => {
      setBusy(43, 0);
      const user = await openActivePanel();

      await user.type(screen.getByLabelText("Название"), " 2");

      expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
    });

    it("правка одной лишь категории предупреждения не даёт (категории нет в теле запроса предложения)", async () => {
      setBusy(43, 7);
      const user = await openActivePanel();

      await user.click(screen.getByRole("combobox", { name: "Категория" }));
      await user.click(await screen.findByRole("option", { name: "Проектирование" }));

      await waitFor(() =>
        expect(handlerState.lastUpdateFamilyRequest).toEqual({
          id: 43,
          body: { family_category_id: 4 },
        })
      );
      expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
    });

    describe("смена единицы: затронуты прежняя (м³) и новая единицы", () => {
      const M2_UNIT_ID = 5;
      // Разбор имени идёт после паузы в наборе (300 мс) и запроса — под нагрузкой дольше 1 с.
      const LATER = { timeout: 8000 };
      const M3_UNIT_ID = 3;
      const WARNING_NEW_4 =
        "В единице «м²» идёт перезапрос: 4 заданий предложений ещё не выполнены. Это изменение отменит их, а уже полученные, но не разобранные предложения скроются — единицу придётся перезапросить заново.";
      const WARNING_BOTH =
        "В единицах «м³» и «м²» идёт перезапрос: 7 и 4 заданий предложений ещё не выполнены. Это изменение отменит их, а уже полученные, но не разобранные предложения скроются — обе единицы придётся перезапросить заново.";

      async function typeUnit(user: ReturnType<typeof userEvent.setup>, text: string) {
        const input = screen.getByLabelText("Единица");
        await user.clear(input);
        await user.type(input, text);
      }

      /**
       * Дождаться ответа разбора имени (после паузы в наборе) и его отрисовки — вместо
       * фиксированной паузы: «предупреждения нет» обязано проверяться ПОСЛЕ ответа, иначе оно
       * истинно уже до запроса. Наблюдатели react-query узнают об ответе через notifyManager
       * (`setTimeout(0)`), поэтому после `success` в кэше — один такой же тик внутри `act`.
       */
      async function settleLookup(queryClient: QueryClient, text: string) {
        await waitFor(() => {
          const state = queryClient.getQueryState(qk.unitBusy(text));
          expect(state?.status).toBe("success");
          expect(state?.fetchStatus).toBe("idle");
        }, LATER);
        await act(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));
      }

      it("прежняя свободна, новая занята — предупреждение называет новую единицу и её число", async () => {
        setBusy(43, 0);
        handlerState.unitBusyByUnitId[M2_UNIT_ID] = 4;
        const user = await openActivePanel();

        await typeUnit(user, "м²");

        expect(await screen.findByText(WARNING_NEW_4, {}, LATER)).toBeInTheDocument();
      });

      it("прежняя занята, новая свободна — предупреждение называет прежнюю", async () => {
        setBusy(43, 7);
        handlerState.unitBusyByUnitId[M2_UNIT_ID] = 0;
        const queryClient = createTestQueryClient();
        const user = await openActivePanel(queryClient);

        await typeUnit(user, "м²");
        // Пока ответа нет, прежняя считается и так — проверять после ответа.
        await settleLookup(queryClient, "м²");

        expect(screen.getByText(WARNING_7)).toBeInTheDocument();
      });

      it("заняты обе — одно предупреждение с обеими единицами и обоими числами", async () => {
        setBusy(43, 7);
        handlerState.unitBusyByUnitId[M2_UNIT_ID] = 4;
        const user = await openActivePanel();

        await typeUnit(user, "м²");

        expect(await screen.findByText(WARNING_BOTH, {}, LATER)).toBeInTheDocument();
        expect(screen.getAllByTestId("rerun-reset-warning")).toHaveLength(1);
      });

      it("другое написание той же единицы — не смена: целевой единицы нет, прежняя свободна — предупреждения нет", async () => {
        setBusy(43, 0);
        handlerState.unitBusyByUnitId[M3_UNIT_ID] = 0;
        handlerState.unitBusyByUnitId[M2_UNIT_ID] = 4;
        const queryClient = createTestQueryClient();
        const user = await openActivePanel(queryClient);

        await typeUnit(user, "куб.м");
        await settleLookup(queryClient, "куб.м");

        expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
      });

      it("другое написание той же единицы при занятой единице — после разбора предупреждение снимается", async () => {
        setBusy(43, 7);
        const user = await openActivePanel();

        await typeUnit(user, "куб.м");

        await waitFor(
          () => expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument(),
          LATER
        );
      });

      it("неизвестное имя единицы — без ошибки и без предупреждения о новой единице", async () => {
        setBusy(43, 0);
        handlerState.unitBusyByUnitId[M2_UNIT_ID] = 4;
        const queryClient = createTestQueryClient();
        const user = await openActivePanel(queryClient);

        await typeUnit(user, "нет такой единицы");
        await settleLookup(queryClient, "нет такой единицы");

        expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
        expect(screen.getByLabelText("Единица")).toBeInTheDocument();
      });

      it("неизвестное имя при занятой прежней — после разбора предупреждение снимается: сохранение откажет, смены не будет", async () => {
        setBusy(43, 7);
        const queryClient = createTestQueryClient();
        const user = await openActivePanel(queryClient);

        await typeUnit(user, "нет такой единицы");
        await settleLookup(queryClient, "нет такой единицы");

        expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
      });

      it("разбор имени не удался — прежняя занятая единица по-прежнему в предупреждении", async () => {
        server.use(
          http.get("/api/v1/semantic/unit-busy-jobs", () => HttpResponse.json({}, { status: 500 }))
        );
        setBusy(43, 7);
        const queryClient = createTestQueryClient();
        const user = await openActivePanel(queryClient);

        await typeUnit(user, "м²");
        await waitFor(() => {
          const state = queryClient.getQueryState(qk.unitBusy("м²"));
          expect(state?.status).toBe("error");
          expect(state?.fetchStatus).toBe("idle");
        }, LATER);
        await act(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));

        expect(screen.getByText(WARNING_7)).toBeInTheDocument();
      });

      it("смена единицы без идущих перезапросов предупреждения не даёт", async () => {
        setBusy(43, 0);
        const queryClient = createTestQueryClient();
        const user = await openActivePanel(queryClient);

        await typeUnit(user, "м²");
        await settleLookup(queryClient, "м²");

        expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
      });

      describe("сохранение не обгоняет проверку целевой единицы", () => {
        function hold() {
          let release: () => void = () => {};
          handlerState.unitBusyGate = new Promise<void>((resolve) => {
            release = resolve;
          });
          return release;
        }

        it("кнопка: пока проверка не завершена — «Проверяю единицу…», запроса нет; после — предупреждение и сохранение", async () => {
          setBusy(43, 0);
          handlerState.unitBusyByUnitId[M2_UNIT_ID] = 4;
          const release = hold();
          const user = await openActivePanel();

          await typeUnit(user, "м²");

          const pending = await screen.findByRole("button", { name: "Проверяю единицу…" });
          expect(pending).toBeDisabled();
          await user.click(pending);
          expect(handlerState.lastUpdateFamilyRequest).toBeNull();

          release();
          expect(await screen.findByText(WARNING_NEW_4)).toBeInTheDocument();
          await user.click(await screen.findByRole("button", { name: "Сохранить" }));
          await waitFor(() =>
            expect(handlerState.lastUpdateFamilyRequest).toEqual({
              id: 43,
              body: expect.objectContaining({ unit_name: "м²" }),
            })
          );
        });

        it("отправка формы в обход кнопки (программно) до конца проверки ничего не шлёт", async () => {
          setBusy(43, 0);
          handlerState.unitBusyByUnitId[M2_UNIT_ID] = 4;
          const release = hold();
          const user = await openActivePanel();

          await typeUnit(user, "м²");
          await screen.findByRole("button", { name: "Проверяю единицу…" });
          fireEvent.submit(screen.getByLabelText("Единица").closest("form")!);
          expect(handlerState.lastUpdateFamilyRequest).toBeNull();

          release();
          expect(await screen.findByText(WARNING_NEW_4)).toBeInTheDocument();
        });

        it("Enter в поле: пока проверка не завершена — запроса нет; после — сохраняет", async () => {
          setBusy(43, 0);
          handlerState.unitBusyByUnitId[M2_UNIT_ID] = 4;
          const release = hold();
          const user = await openActivePanel();

          await typeUnit(user, "м²");
          await screen.findByRole("button", { name: "Проверяю единицу…" });
          await user.type(screen.getByLabelText("Единица"), "{Enter}");
          expect(handlerState.lastUpdateFamilyRequest).toBeNull();

          release();
          expect(await screen.findByText(WARNING_NEW_4)).toBeInTheDocument();
          await user.type(screen.getByLabelText("Единица"), "{Enter}");
          await waitFor(() => expect(handlerState.lastUpdateFamilyRequest).not.toBeNull());
        });

        it("ответ по прежнему вводу не выдаётся за ответ по новому: пока новый разбор в пути — ожидание, предупреждения о прежней цели нет", async () => {
          // Риск — показ прежних данных на время нового запроса (например, `keepPreviousData`):
          // кнопка открылась бы, а предупреждение относилось бы к другой единице.
          setBusy(43, 0);
          handlerState.unitBusyByUnitId[M2_UNIT_ID] = 4;
          const queryClient = createTestQueryClient();
          const user = await openActivePanel(queryClient);
          await typeUnit(user, "м²");
          expect(await screen.findByText(WARNING_NEW_4, {}, LATER)).toBeInTheDocument();

          const release = hold();
          await typeUnit(user, "куб.м");
          await waitFor(
            () => expect(queryClient.getQueryState(qk.unitBusy("куб.м"))?.fetchStatus).toBe("fetching"),
            LATER
          );
          await act(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));

          expect(screen.getByRole("button", { name: "Проверяю единицу…" })).toBeDisabled();
          expect(screen.queryByText(WARNING_NEW_4)).not.toBeInTheDocument();

          release();
          await settleLookup(queryClient, "куб.м");
          expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
          expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
        });

        it("ответ по пустому имени из кэша не подменяет разбор нового ввода в паузе набора", async () => {
          // Без имени хук ходит по ключу пустого имени; в приложении кэш живёт минутами, и ответ
          // «единицы нет» оттуда не должен ни открыть сохранение, ни дать предупреждение.
          setBusy(43, 0);
          handlerState.unitBusyByUnitId[0] = 2;
          const queryClient = new QueryClient({
            defaultOptions: {
              queries: { retry: false, gcTime: Infinity, staleTime: 0 },
              mutations: { retry: false },
            },
          });
          const user = await openActivePanel(queryClient);
          const input = screen.getByLabelText("Единица");
          await user.clear(input);
          await settleLookup(queryClient, "");
          expect(screen.getByText(/«без единицы»/)).toBeInTheDocument();

          const release = hold();
          await user.type(input, "м²");

          expect(screen.getByRole("button", { name: "Проверяю единицу…" })).toBeDisabled();
          expect(screen.queryByText(/«без единицы»/)).not.toBeInTheDocument();
          release();
          await settleLookup(queryClient, "м²");
          expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
        });

        it("отказ проверки виден сразу: у проверки свои `retry: false`, а не только у тестового клиента", async () => {
          // Клиент с повтором через минуту: без собственного `retry: false` у проверки отказ
          // показался бы только после повтора, а всё это время сохранение ждало бы.
          setBusy(43, 0);
          handlerState.unitBusyFails = true;
          const queryClient = new QueryClient({
            defaultOptions: {
              queries: { retry: 1, retryDelay: 60_000, gcTime: 0, staleTime: 0 },
              mutations: { retry: false },
            },
          });
          const user = await openActivePanel(queryClient);

          await typeUnit(user, "м²");

          expect(await screen.findByTestId("unit-check-failed-warning", {}, LATER)).toBeInTheDocument();
          expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
        });

        it("новый ввод после завершённой проверки снова блокирует сохранение до разбора нового значения", async () => {
          setBusy(43, 0);
          handlerState.unitBusyByUnitId[M2_UNIT_ID] = 4;
          const user = await openActivePanel();
          await typeUnit(user, "м²");
          expect(await screen.findByText(WARNING_NEW_4)).toBeInTheDocument();
          expect(await screen.findByRole("button", { name: "Сохранить" })).toBeEnabled();

          const release = hold();
          await user.type(screen.getByLabelText("Единица"), "{Backspace}");

          expect(await screen.findByRole("button", { name: "Проверяю единицу…" })).toBeDisabled();
          release();
          expect(await screen.findByRole("button", { name: "Сохранить" })).toBeEnabled();
        });

        it("повторный ввод уже проверенной единицы: сохранение ждёт свежий ответ, а не кэш", async () => {
          setBusy(43, 0);
          handlerState.unitBusyByUnitId[M2_UNIT_ID] = 4;
          const user = await openActivePanel();
          await typeUnit(user, "м²");
          expect(await screen.findByText(WARNING_NEW_4)).toBeInTheDocument();
          await typeUnit(user, "куб.м");
          await screen.findByRole("button", { name: "Сохранить" });

          // Число за время правки выросло; ответ на повторный ввод «м²» задержан.
          handlerState.unitBusyByUnitId[M2_UNIT_ID] = 9;
          const release = hold();
          const before = handlerState.unitBusyRequests;
          await typeUnit(user, "м²");

          // Задержанный запрос дошёл до сервера (пауза набора позади); кэш по «м²» уже на экране.
          await waitFor(() => expect(handlerState.unitBusyRequests).toBe(before + 1));
          expect(screen.getByRole("button", { name: "Проверяю единицу…" })).toBeDisabled();
          expect(handlerState.lastUpdateFamilyRequest).toBeNull();
          release();
          expect(await screen.findByRole("button", { name: "Сохранить" })).toBeEnabled();
          expect(
            await screen.findByText(WARNING_NEW_4.replace("4 заданий", "9 заданий"))
          ).toBeInTheDocument();
        });

        it("возврат единицы к сохранённому значению ожидания не требует", async () => {
          setBusy(43, 0);
          const user = await openActivePanel();
          await typeUnit(user, "м²");
          await typeUnit(user, "м³");

          expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
        });

        it("отказ проверки: видно предупреждение с введённым текстом, сохранение разрешено", async () => {
          setBusy(43, 0);
          handlerState.unitBusyFails = true;
          const user = await openActivePanel();

          await typeUnit(user, "м²");

          expect(
            await screen.findByText(
              "Не удалось проверить, идёт ли перезапрос в единице «м²». Сохранение может сбросить её перезапрос."
            )
          ).toBeInTheDocument();
          await user.click(await screen.findByRole("button", { name: "Сохранить" }));
          await waitFor(() =>
            expect(handlerState.lastUpdateFamilyRequest).toEqual({
              id: 43,
              body: expect.objectContaining({ unit_name: "м²" }),
            })
          );
        });
      });

      it("правка названия при смене единицы считает и прежнюю, и новую единицы", async () => {
        setBusy(43, 7);
        handlerState.unitBusyByUnitId[M2_UNIT_ID] = 4;
        const user = await openActivePanel();

        await user.type(screen.getByLabelText("Название"), " 2");
        await typeUnit(user, "м²");

        expect(await screen.findByText(WARNING_BOTH, {}, LATER)).toBeInTheDocument();
      });
    });

    it("у черновика правка названия предупреждения не даёт: его нет в теле запроса предложения", async () => {
      setBusy(3, 7);
      const user = userEvent.setup();
      renderWithProviders(<FamiliesTab />);
      await user.click(await screen.findByText("Семья работ №3"));
      await screen.findByLabelText("Определение");

      await user.type(screen.getByLabelText("Название"), " 2");

      expect(screen.queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
    });

    it("у черновика смена единицы не проверяется и сохранение не ждёт: черновика нет в теле запроса предложения", async () => {
      const queryClient = createTestQueryClient();
      const user = userEvent.setup();
      renderWithProviders(<FamiliesTab />, { queryClient });
      await user.click(await screen.findByText("Семья работ №3"));
      await screen.findByLabelText("Определение");

      const input = screen.getByLabelText("Единица");
      await user.clear(input);
      await user.type(input, "м³");

      // Сразу после ввода — ещё в паузе набора: у активной семьи кнопка ждала бы разбора.
      expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: "Проверяю единицу…" })).not.toBeInTheDocument();
      expect(queryClient.getQueryState(qk.unitBusy("м³"))).toBeUndefined();
    });
  });

  describe("архивирование", () => {
    it("окно архивирования при идущем перезапросе предупреждает числом заданий и не блокирует действие", async () => {
      setBusy(43, 7);
      const user = await openActivePanel();

      await user.click(screen.getByRole("button", { name: "В архив: семья Кровельные работы" }));

      const dialog = await screen.findByRole("alertdialog");
      expect(within(dialog).getByText(WARNING_7)).toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Архивировать" })).toBeEnabled();
    });

    it("окно архивирования без идущего перезапроса предупреждения не даёт", async () => {
      setBusy(43, 0);
      const user = await openActivePanel();

      await user.click(screen.getByRole("button", { name: "В архив: семья Кровельные работы" }));

      const dialog = await screen.findByRole("alertdialog");
      expect(within(dialog).queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
    });
  });

  describe("слияние", () => {
    it("окно слияния при идущем перезапросе предупреждает числом заданий и не блокирует действие", async () => {
      setBusy(43, 7);
      const user = await openActivePanel();

      await user.click(screen.getByRole("button", { name: "Слить" }));

      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText(WARNING_7)).toBeInTheDocument();
      expect(within(dialog).getByRole("combobox", { name: "Целевая семья" })).toBeEnabled();
    });

    it("окно слияния без идущего перезапроса предупреждения не даёт", async () => {
      setBusy(43, 0);
      const user = await openActivePanel();

      await user.click(screen.getByRole("button", { name: "Слить" }));

      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).queryByTestId("rerun-reset-warning")).not.toBeInTheDocument();
    });
  });
});

describe("FamiliesTab — поиск целевой семьи при слиянии", () => {
  afterEach(() => {
    localStorage.clear();
  });

  function activeFamily(id: number, title: string) {
    const base = handlerState.workFamilies.find((f) => f.id === 43)!;
    handlerState.workFamilies.push({ ...base, id, title });
  }

  async function openMerge() {
    const user = await openActivePanel();
    await user.click(screen.getByRole("button", { name: "Слить" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("combobox", { name: "Целевая семья" }));
    return { user, dialog };
  }

  it("поиск сужает список по подстроке без учёта регистра; выбор из найденного шлёт тот же запрос слияния", async () => {
    activeFamily(61, "Устройство стяжки пола");
    activeFamily(62, "Окраска стен");
    const { user, dialog } = await openMerge();
    expect(await screen.findByRole("option", { name: "Окраска стен" })).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("Найти семью…"), "ЯЖКИ П");

    expect(await screen.findByRole("option", { name: "Устройство стяжки пола" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Окраска стен" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "Устройство стяжки пола" }));
    await user.click(within(dialog).getByRole("button", { name: "Слить" }));

    await waitFor(() =>
      expect(handlerState.lastMergeFamiliesRequest).toEqual({ id: 43, targetFamilyId: 61 })
    );
  });

  it("варианты приходят вразнобой — показаны по алфавиту", async () => {
    activeFamily(62, "Штукатурка");
    activeFamily(63, "Армирование");
    await openMerge();

    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Армирование",
      "Штукатурка",
    ]);
  });

  it("пустой результат поиска — «Ничего не найдено»; сама сливаемая семья в целях не находится", async () => {
    activeFamily(62, "Окраска стен");
    const { user } = await openMerge();

    await user.type(screen.getByPlaceholderText("Найти семью…"), "кровельные");

    expect(await screen.findByText("Ничего не найдено")).toBeInTheDocument();
  });
});
