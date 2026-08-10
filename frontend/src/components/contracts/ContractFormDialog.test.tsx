import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { ContractFormDialog } from "./ContractFormDialog";
import { sampleContractCard } from "@/test/fixtures";
import { server } from "@/test/server";
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

/**
 * Черновик объекта (§2.2 спеки, решение гейта 1 переиграно 2026-08-11).
 *
 * Тот же дефект, что был у класса, и найден он тоже пользователем на стенде:
 * пункт «Создать объект» при пустом поле поиска не делал **ничего** — обработчик
 * выходил на `query.trim()`, а список закрывался вместе с полем ввода. Замер
 * пробником в jsdom дал одинаковые значения на этой ветке и на версии из `main`,
 * то есть правка класса к дефекту отношения не имела.
 */
describe("Форма договора: объект заводится черновиком (§2.2)", () => {
  function captureObjectPosts() {
    const bodies: Record<string, unknown>[] = [];
    server.use(
      http.post("/api/v1/objects", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        bodies.push(body);
        return HttpResponse.json(
          {
            id: 99,
            title: body.title,
            address: body.address ?? "",
            rate_class_id: null,
            rate_class_title: null,
            contracts_count: 0,
            created_at: null,
            updated_at: null,
          },
          { status: 201 }
        );
      })
    );
    return bodies;
  }

  /**
   * Запрос поиска непустой намеренно — та же единственность якоря, что у класса:
   * иначе снятие 12 (возврат отказа на пустом входе) валило бы все тесты сразу.
   * Название набирается заново после `clear()`, чтобы тест не зависел ещё и от
   * предзаполнения (снятие 13).
   */
  async function openObjectDraft(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("combobox", { name: /Объект/ }));
    await user.type(
      await screen.findByPlaceholderText("Название или адрес объекта"),
      "черновик"
    );
    await user.click(await screen.findByText(/Создать объект/));
    return screen.findByLabelText("Название объекта (обязательно)");
  }

  it("пункт «Создать объект» с пустым поиском открывает черновик, а не молчит", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    await user.click(await screen.findByRole("combobox", { name: /Объект/ }));
    // Точное совпадение подписи и есть утверждение «запрос пуст».
    await user.click(await screen.findByText("Создать объект"));

    expect(await screen.findByLabelText("Название объекта (обязательно)")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Сохранить объект" })).toBeInTheDocument();
  });

  it("название из поиска предзаполняет черновик объекта", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    await user.click(await screen.findByRole("combobox", { name: /Объект/ }));
    await user.type(
      await screen.findByPlaceholderText("Название или адрес объекта"),
      "  ЖК Западный  "
    );
    await user.click(await screen.findByText(/Создать объект/));

    expect(await screen.findByLabelText("Название объекта (обязательно)")).toHaveValue(
      "ЖК Западный"
    );
  });

  it("условие названо у поля объекта: required и связь с подсказкой", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    const field = await openObjectDraft(user);
    expect(field).toBeRequired();
    expect(field).not.toHaveAttribute("placeholder");

    const hintId = field.getAttribute("aria-describedby");
    expect(hintId).toBeTruthy();
    expect(document.getElementById(hintId as string)).toHaveTextContent(
      "Без названия объект не добавить"
    );
  });

  it("пустое название сохранить нельзя, непустое — создаёт объект и подписывает его", async () => {
    captureObjectPosts();
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    const field = await openObjectDraft(user);
    await user.clear(field);

    const save = screen.getByRole("button", { name: "Сохранить объект" });
    expect(save).toBeDisabled();

    await user.type(field, "ЖК Западный");
    expect(save).toBeEnabled();
    await user.click(save);

    // Объект создан, выбран в форме и подписан — подпись держится вне выдачи.
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: /Объект/ })).toHaveTextContent("ЖК Западный");
    });
  });

  it("на время запроса кнопка черновика объекта неактивна: второго POST не будет", async () => {
    let posts = 0;
    let release: (() => void) | undefined;
    const pendingRequest = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("/api/v1/objects", async () => {
        posts += 1;
        await pendingRequest;
        return HttpResponse.json(
          {
            id: 99,
            title: "ЖК Западный",
            address: "",
            rate_class_id: null,
            rate_class_title: null,
            contracts_count: 0,
            created_at: null,
            updated_at: null,
          },
          { status: 201 }
        );
      })
    );
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    const field = await openObjectDraft(user);
    await user.clear(field);
    await user.type(field, "ЖК Западный");

    const save = screen.getByRole("button", { name: "Сохранить объект" });
    await user.click(save);

    await waitFor(() => expect(save).toBeDisabled());
    await user.click(save);
    expect(posts).toBe(1);

    // Ждём закрытия черновика, а не подписи: подпись стережёт свой тест (17/18).
    release?.();
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Сохранить объект" })).not.toBeInTheDocument();
    });
  });

  /**
   * Тело запроса, а не «форма закрылась». `objects.address` — `NOT NULL`, сервер
   * симметрично делает `(address or "").strip()` на обоих путях, и `null` ложится
   * пустой строкой; края режет фронт — ровно как уже делает `ObjectFormDialog`.
   */
  const addressCases: Array<[string, string, string | null]> = [
    ["пробельный адрес уходит null, а не строкой", "   ", null],
    ["края непустого адреса обрезаются", "  ул. Полевая, 1  ", "ул. Полевая, 1"],
  ];

  it.each(addressCases)("%s", async (_name, typed, expected) => {
    const bodies = captureObjectPosts();
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    const field = await openObjectDraft(user);
    await user.clear(field);
    await user.type(field, "  ЖК Западный  ");
    await user.type(screen.getByLabelText("Адрес объекта"), typed);
    await user.click(screen.getByRole("button", { name: "Сохранить объект" }));

    await waitFor(() => expect(bodies.length).toBe(1));
    expect(bodies[0].title).toBe("ЖК Западный");
    expect(bodies[0].address).toBe(expected);
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

/**
 * Тупик, найденный в работе: справочник классов пуст, у объекта класса нет — и
 * форма молчала. Выпадающий список открывался пустым, кнопка «Создать договор»
 * была активна, а отказ приходил с сервера (`_resolve_snapshot_rate_class`, 422)
 * уже после заполнения всей формы. Класс договора обязателен (§4), поэтому он
 * заводится там же, где объект и подрядчик, — по месту.
 */
describe("Форма договора: класса ещё нет в системе", () => {
  const objectWithoutClass = {
    id: 12,
    title: "МИРА",
    address: "",
    rate_class_id: null,
    rate_class_title: null,
    contracts_count: 0,
    created_at: null,
    updated_at: null,
  };

  function noClassesAtAll() {
    server.use(
      http.get("/api/v1/rate-classes", () => HttpResponse.json([])),
      http.get("/api/v1/objects", () =>
        HttpResponse.json({ items: [objectWithoutClass], total: 1, page: 1, page_size: 20 })
      )
    );
  }

  async function selectObjectWithoutClass(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("combobox", { name: /Объект/ }));
    await user.click(await screen.findByText("МИРА"));
  }

  /**
   * Открыть черновик класса. Возвращает поле названия.
   *
   * Запрос поиска **непустой намеренно**, и это не украшение, а единственность
   * якоря негативных проверок (verifying-guards, следствие слоя 8: вход обязан
   * нарушать ровно одно ограничение). Первая редакция этих тестов открывала
   * черновик с пустым поиском, и замер показал цену: снятие 1 — возврат отказа на
   * пустом входе — валило **восемь** тестов вместо одного, потому что через
   * пустой поиск проходили все. Тест пустого поиска ходит своим путём, ниже.
   */
  async function openClassDraft(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("combobox", { name: /Класс объектов/ }));
    await user.type(await screen.findByPlaceholderText("Название класса"), "черновик");
    await user.click(await screen.findByText(/Создать класс/));
    return screen.findByLabelText("Название класса (обязательно)");
  }

  /**
   * Создание класса стало двухшаговым: пункт «Создать класс» открывает
   * черновик-форму, сохранение отправляет запрос (спека §2).
   *
   * Название набирается **заново, в черновик**, хотя поиск его и предзаполнил, —
   * по той же причине единственности якоря: тест, полагающийся на предзаполнение,
   * краснел бы и от снятия 2, у которого есть свой тест.
   */
  async function createClassViaDraft(
    user: ReturnType<typeof userEvent.setup>,
    title: string
  ) {
    const field = await openClassDraft(user);
    await user.clear(field);
    await user.type(field, title);
    await user.click(screen.getByRole("button", { name: "Сохранить класс" }));
  }

  it("класс заводится по месту, когда справочник пуст", async () => {
    noClassesAtAll();
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    await createClassViaDraft(user, "Административные");

    // Список так и остался пустым (сервер отдаёт []), поэтому подпись держится
    // отдельно от выдачи — как у объекта и подрядчика.
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: /Класс объектов/ })).toHaveTextContent(
        "Административные"
      );
    });
  });

  it("без класса — ни в форме, ни у объекта — договор не отправляется", async () => {
    noClassesAtAll();
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    await selectObjectWithoutClass(user);
    await user.click(screen.getByRole("combobox", { name: /Подрядчик/ }));
    await user.click(await screen.findByText("ООО СтройПодряд"));
    await user.type(screen.getByLabelText("Номер договора"), "ПМ-1-СМР");
    await user.type(screen.getByLabelText("Дата подписания"), "2025-02-20");

    // Всё заполнено, но класс взять негде — форма говорит об этом сама, а не
    // отправляет запрос ради 422.
    const submit = screen.getByRole("button", { name: "Создать договор" });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/У объекта «МИРА» класс не задан/)).toBeInTheDocument();

    await createClassViaDraft(user, "Административные");

    await waitFor(() => expect(submit).toBeEnabled());
  });

  it("класс объекта подставляется сам — предупреждения нет", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    await user.click(await screen.findByRole("combobox", { name: /Объект/ }));
    await user.click(await screen.findByText("ЖК Северный"));

    // У «ЖК Северного» класс есть — договор берёт его по умолчанию (§4).
    expect(screen.queryByText(/класс не задан/)).not.toBeInTheDocument();
  });

  it("у member кнопки создания класса нет: классы — право admin", async () => {
    noClassesAtAll();
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />, {
      initialUser: { id: 2, email: "member@example.com", role: "member" },
    });

    await user.click(await screen.findByRole("combobox", { name: /Класс объектов/ }));
    await screen.findByPlaceholderText("Название класса");
    expect(screen.queryByText(/Создать класс/)).not.toBeInTheDocument();
  });

  /**
   * Черновик класса (спека §2). Найденный пользователем дефект: пункт «Создать
   * класс» при пустом поле поиска не делал **ничего** — обработчик выходил на
   * `query.trim()`, а список закрывался вместе с полем, в которое надо было
   * вводить название (замер §1 спеки: 0 запросов, список закрыт).
   */
  it("пункт «Создать класс» с пустым поиском открывает черновик, а не молчит", async () => {
    noClassesAtAll();
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    await user.click(await screen.findByRole("combobox", { name: /Класс объектов/ }));
    // Точное совпадение подписи — это и есть утверждение «запрос пуст»: с
    // непустым запросом пункт подписан «Создать класс: «…»».
    await user.click(await screen.findByText("Создать класс"));

    expect(await screen.findByLabelText("Название класса (обязательно)")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Сохранить класс" })).toBeInTheDocument();
  });

  it("название из поиска предзаполняет черновик — прежний путь не удлиняется", async () => {
    noClassesAtAll();
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    await user.click(await screen.findByRole("combobox", { name: /Класс объектов/ }));
    await user.type(await screen.findByPlaceholderText("Название класса"), "  Административные  ");
    await user.click(await screen.findByText(/Создать класс/));

    expect(await screen.findByLabelText("Название класса (обязательно)")).toHaveValue(
      "Административные"
    );
  });

  it("пустое название сохранить нельзя, непустое — создаёт класс", async () => {
    noClassesAtAll();
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    const field = await openClassDraft(user);
    await user.clear(field);

    const save = screen.getByRole("button", { name: "Сохранить класс" });
    expect(save).toBeDisabled();

    await user.type(field, "Административные");
    expect(save).toBeEnabled();
    await user.click(save);

    // Класс создан — черновик закрылся сам.
    await waitFor(() => {
      expect(screen.queryByLabelText("Название класса (обязательно)")).not.toBeInTheDocument();
    });
  });

  it("на время запроса кнопка черновика неактивна: второго POST не будет", async () => {
    let posts = 0;
    let release: (() => void) | undefined;
    const pendingRequest = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/v1/rate-classes", () => HttpResponse.json([])),
      http.post("/api/v1/rate-classes", async () => {
        posts += 1;
        await pendingRequest;
        return HttpResponse.json(
          {
            id: 3,
            title: "Административные",
            description: null,
            contracts_count: 0,
            objects_count: 0,
            standards_count: 0,
            created_at: null,
            updated_at: null,
          },
          { status: 201 }
        );
      })
    );
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    const field = await openClassDraft(user);
    await user.clear(field);
    await user.type(field, "Административные");

    const save = screen.getByRole("button", { name: "Сохранить класс" });
    await user.click(save);

    // Пока запрос висит, кнопка обязана быть выключена: второй POST на то же
    // название вернёт 409, а не класс.
    await waitFor(() => expect(save).toBeDisabled());
    await user.click(save);
    expect(posts).toBe(1);

    // Ждём закрытия черновика, а не подписи комбобокса: подпись — следствие
    // `setClassLabel`, и утверждение о ней сделало бы этот тест вторым
    // исполнителем требования, за которым стоит правленый «класс заводится по
    // месту» (план §4, снятие 6).
    release?.();
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Сохранить класс" })).not.toBeInTheDocument();
    });
  });

  /**
   * Тело запроса, а не «форма закрылась» (спека §2.1): сервер обрабатывает края
   * описания несимметрично — `create_rate_class` их **не** обрезает, поэтому
   * пробельное описание легло бы в базу как есть.
   */
  const descriptionCases: Array<[string, string, string | null]> = [
    ["пробельное описание уходит null, а не строкой", "   ", null],
    ["края непустого описания обрезаются", "  Многоэтажные жилые  ", "Многоэтажные жилые"],
  ];

  it.each(descriptionCases)("%s", async (_name, typed, expected) => {
    let body: Record<string, unknown> | undefined;
    server.use(
      http.get("/api/v1/rate-classes", () => HttpResponse.json([])),
      http.post("/api/v1/rate-classes", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          {
            id: 3,
            title: body.title,
            description: body.description ?? null,
            contracts_count: 0,
            objects_count: 0,
            standards_count: 0,
            created_at: null,
            updated_at: null,
          },
          { status: 201 }
        );
      })
    );
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    const field = await openClassDraft(user);
    await user.clear(field);
    await user.type(field, "  Административные  ");
    await user.type(screen.getByLabelText("Описание класса"), typed);
    await user.click(screen.getByRole("button", { name: "Сохранить класс" }));

    await waitFor(() => expect(body).toBeDefined());
    expect(body!.title).toBe("Административные");
    expect(body!.description).toBe(expected);
  });

  /**
   * Второе место того же показа (спека §2.0): условие названо у поля и в
   * черновике, тем же механизмом, что на вкладке «Классы объектов» и у
   * `passport-top-n` в `SettingsPage`. Кнопка остаётся нативно `disabled`, и на
   * ней ничего не висит — из tab-порядка она исключена.
   */
  it("в черновике условие названо у поля: required и связь с подсказкой", async () => {
    noClassesAtAll();
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);

    const field = await openClassDraft(user);
    expect(field).toBeRequired();
    expect(field).not.toHaveAttribute("placeholder");

    const hintId = field.getAttribute("aria-describedby");
    expect(hintId).toBeTruthy();
    expect(document.getElementById(hintId as string)).toHaveTextContent(
      "Например: Жилые дома. Без названия класс не добавить"
    );
  });

  /**
   * Регрессионный щит права, а не тест нового поведения: пункта создания у
   * `member` нет уже сегодня, поэтому нет и черновика — тест зелен и до правки.
   * Доказывается он снятием `isAdmin` (план §4, снятие 8), а не красным прогоном.
   */
  it("у member черновика класса нет — как нет и пункта создания", async () => {
    noClassesAtAll();
    const user = userEvent.setup();
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />, {
      initialUser: { id: 2, email: "member@example.com", role: "member" },
    });

    await user.click(await screen.findByRole("combobox", { name: /Класс объектов/ }));
    await screen.findByPlaceholderText("Название класса");

    expect(screen.queryByText(/Создать класс/)).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Название класса (обязательно)")
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сохранить класс" })).not.toBeInTheDocument();
  });
});

/**
 * Секция коммерческих условий (спека §2.5, §2.9): свёрнута по умолчанию, не
 * блокирует создание договора без условий, проценты уходят строками, а пустой
 * комментарий — как `null`, не пустой строкой.
 *
 * `fillRequiredContractFields` — тот же порядок действий, что уже стоит в
 * describe «класс как снимок» (объект → подрядчик → номер → дата), вынесенный
 * сюда как хелпер: он нужен всем четырём тестам этого блока.
 */
describe("Форма договора: коммерческие условия (§2.5, §2.9)", () => {
  async function fillRequiredContractFields() {
    await userEvent.click(await screen.findByRole("combobox", { name: /Объект/ }));
    await userEvent.click(await screen.findByText("ЖК Северный"));
    await userEvent.click(screen.getByRole("combobox", { name: /Подрядчик/ }));
    await userEvent.click(await screen.findByText("ООО СтройПодряд"));
    await userEvent.type(screen.getByLabelText("Номер договора"), "ГП-2026-003");
    await userEvent.type(screen.getByLabelText("Дата подписания"), "2026-05-01");
  }

  it("секция условий свёрнута по умолчанию", async () => {
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);
    expect(screen.queryByLabelText(/аванс, %/i)).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /коммерческие условия/i })).toBeInTheDocument();
  });

  it("создание договора без условий не блокируется", async () => {
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);
    await fillRequiredContractFields();
    expect(screen.getByRole("button", { name: /создать/i })).toBeEnabled();
  });

  it("отправляет проценты строками, а пустой комментарий — как null", async () => {
    let body: Record<string, unknown> | undefined;
    server.use(
      http.post("/api/v1/contracts", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 7 }, { status: 201 });
      })
    );
    renderWithProviders(<ContractFormDialog open onOpenChange={() => {}} />);
    await fillRequiredContractFields();
    await userEvent.click(screen.getByRole("button", { name: /коммерческие условия/i }));
    await userEvent.type(screen.getByLabelText(/аванс, %/i), "30");
    await userEvent.type(screen.getByLabelText(/оговорка к авансу/i), "   ");
    await userEvent.click(screen.getByRole("button", { name: /создать/i }));
    await waitFor(() => expect(body).toBeDefined());
    expect(body!.advance_pct).toBe("30");
    expect(body!.advance_note).toBeNull();
  });

  it("в режиме правки показывает уже заведённые условия", async () => {
    renderWithProviders(
      <ContractFormDialog
        open
        onOpenChange={() => {}}
        contract={{ ...sampleContractCard, advance_pct: "30", advance_note: "траншами" }}
      />
    );
    await userEvent.click(await screen.findByRole("button", { name: /коммерческие условия/i }));
    expect(screen.getByLabelText(/аванс, %/i)).toHaveValue("30");
    expect(screen.getByLabelText(/оговорка к авансу/i)).toHaveValue("траншами");
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
