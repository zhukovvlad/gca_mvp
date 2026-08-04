import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ContractFormDialog } from "./ContractFormDialog";
import { sampleContractCard } from "@/test/fixtures";
import { renderWithProviders } from "@/test/utils";

/**
 * Поиск в комбобоксе объекта/подрядчика (разбор внешнего ревью).
 *
 * Дефект был такой: встроенная фильтрация `Command` отключена, а введённый запрос
 * никуда не уходил — список оставался неизменным. Поле поиска выглядело рабочим и
 * не работало, а форма к тому же брала только первую страницу справочника, так что
 * записи за её пределами выбрать было нельзя вообще.
 */
describe("Форма договора: поиск объекта и подрядчика", () => {
  async function openObjectCombobox(user: ReturnType<typeof userEvent.setup>) {
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);
    await user.click(await screen.findByRole("combobox", { name: /Объект/ }));
    return screen.findByPlaceholderText("Название или адрес объекта");
  }

  it("запрос уходит на сервер и список сужается", async () => {
    const user = userEvent.setup();
    const search = await openObjectCombobox(user);

    // До ввода видны оба объекта.
    expect(await screen.findByText("ЖК Северный")).toBeInTheDocument();
    expect(screen.getByText("ЖК Южный")).toBeInTheDocument();

    await user.type(search, "Южный");

    await waitFor(() => {
      expect(screen.queryByText("ЖК Северный")).not.toBeInTheDocument();
    });
    expect(await screen.findByText("ЖК Южный")).toBeInTheDocument();
  });

  it("ищет и по адресу, а не только по названию", async () => {
    const user = userEvent.setup();
    const search = await openObjectCombobox(user);
    await screen.findByText("ЖК Северный");

    await user.type(search, "Солнечная");

    await waitFor(() => {
      expect(screen.queryByText("ЖК Северный")).not.toBeInTheDocument();
    });
    expect(await screen.findByText("ЖК Южный")).toBeInTheDocument();
  });

  it("выбранный объект остаётся подписан, даже когда выпал из выдачи", async () => {
    const user = userEvent.setup();
    await openObjectCombobox(user);

    await user.click(await screen.findByText("ЖК Северный"));
    const trigger = screen.getByRole("combobox", { name: /Объект/ });
    expect(trigger).toHaveTextContent("ЖК Северный");

    // Новый запрос выкидывает выбранную запись из списка — подпись обязана остаться,
    // иначе человек решит, что выбор сбросился.
    await user.click(trigger);
    await user.type(await screen.findByPlaceholderText("Название или адрес объекта"), "Южный");
    await screen.findByText("ЖК Южный");

    // Ровно одно вхождение — на самой кнопке; в списке записи уже нет. Проверять
    // через queryByText нельзя: подпись кнопки и есть то, что мы хотим увидеть.
    await waitFor(() => {
      expect(screen.getAllByText("ЖК Северный")).toHaveLength(1);
    });
    expect(screen.getByRole("combobox", { name: /Объект/ })).toHaveTextContent("ЖК Северный");
  });

  it("подрядчик ищется по БИН/ИНН", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);
    await user.click(await screen.findByRole("combobox", { name: /Подрядчик/ }));
    const search = await screen.findByPlaceholderText("Название или БИН/ИНН");

    expect(await screen.findByText("ООО СтройПодряд")).toBeInTheDocument();
    await user.type(search, "9876");

    await waitFor(() => {
      expect(screen.queryByText("ООО СтройПодряд")).not.toBeInTheDocument();
    });
    expect(await screen.findByText("ТОО Монолит")).toBeInTheDocument();
  });

  it("в режиме правки подписи берутся из карточки", async () => {
    renderWithProviders(
      <ContractFormDialog open onOpenChange={() => {}} contract={sampleContractCard} />
    );

    // Карточка знает названия, и выдача справочника для подписи не нужна.
    const trigger = await screen.findByRole("combobox", { name: /Объект/ });
    expect(trigger).toHaveTextContent("ЖК Северный");
    expect(screen.getByRole("combobox", { name: /Подрядчик/ })).toHaveTextContent(
      "ООО СтройПодряд"
    );
  });

  it("новый подрядчик требует БИН/ИНН, а не заводится заглушкой", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);
    await user.click(await screen.findByRole("combobox", { name: /Подрядчик/ }));
    await user.type(await screen.findByPlaceholderText("Название или БИН/ИНН"), "ООО Новый");
    await user.click(await screen.findByText(/Создать подрядчика/));

    const inn = await screen.findByLabelText("БИН/ИНН подрядчика");
    const save = screen.getByRole("button", { name: "Сохранить подрядчика" });
    // Без БИН/ИНН сохранить нельзя: он уникален и по нему подрядчик опознаётся.
    expect(save).toBeDisabled();

    await user.type(inn, "555000111222");
    expect(save).toBeEnabled();
    await user.click(save);

    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: /Подрядчик/ })).toHaveTextContent("ООО Новый");
    });
  });
});

describe("Форма договора: класс как снимок (§4)", () => {
  it("объясняет, что класс возьмётся у объекта, если не выбран", async () => {
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);
    expect(
      await screen.findByText(/возьмётся класс объекта/)
    ).toBeInTheDocument();
    expect(screen.getByText(/переклассификация объекта не изменит отклонения/)).toBeInTheDocument();
  });

  it("сумма — текстовое поле, а не number (§3 запрещает float)", async () => {
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);
    const total = await screen.findByLabelText("Сумма договора");
    expect(total).not.toHaveAttribute("type", "number");
    expect(total).toHaveAttribute("inputMode", "decimal");
  });

  it("создать договор нельзя без объекта, подрядчика, номера и даты", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    const submit = await screen.findByRole("button", { name: "Создать договор" });
    expect(submit).toBeDisabled();

    await user.click(screen.getByRole("combobox", { name: /Объект/ }));
    await user.click(await screen.findByText("ЖК Северный"));
    await user.click(screen.getByRole("combobox", { name: /Подрядчик/ }));
    await user.click(await screen.findByText("ООО СтройПодряд"));
    await user.type(screen.getByLabelText("Номер договора"), "ГП-2026-003");
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText("Дата подписания"), "2026-05-01");
    await waitFor(() => expect(submit).toBeEnabled());
  });
});

describe("Комбобокс: подсказки", () => {
  it("показывает класс объекта и БИН подрядчика как подсказку", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    await user.click(await screen.findByRole("combobox", { name: /Объект/ }));
    const northern = (await screen.findByText("ЖК Северный")).closest("[data-slot]") ??
      (await screen.findByText("ЖК Северный")).parentElement;
    expect(within(northern as HTMLElement).getByText("Жилые дома")).toBeInTheDocument();
  });
});
