import { useState } from "react";
import { ChevronDownIcon } from "lucide-react";

import { EntityCombobox } from "@/components/domain/EntityCombobox";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useCurrentUser } from "@/hooks/useAuth";
import {
  useContractors,
  useCreateContract,
  useCreateContractor,
  useCreateObject,
  useCreateRateClass,
  useObjects,
  useRateClasses,
  useUpdateContract,
} from "@/services/queries";
import { normalizeDecimalInput } from "@/lib/decimal";
import { useDebounce } from "@/lib/useDebounce";
import type { ContractCard, ContractInput } from "@/types/domain";

interface ContractFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Задан — правка карточки, иначе создание. */
  contract?: ContractCard;
  onCreated?: (contract: ContractCard) => void;
}

interface FormState {
  object_id: number | null;
  contractor_id: number | null;
  rate_class_id: number | null;
  contract_number: string;
  signed_date: string;
  title: string;
  signer: string;
  total_amount: string;
  notes: string;
  /**
   * Коммерческие условия (спека §2.5): три пары «процент + оговорка». Проценты —
   * текстом, тем же приёмом, что `total_amount`: `<input type="number">` отдал бы
   * float, а сервер его отвергает явно (§3).
   */
  advance_pct: string;
  advance_note: string;
  bank_guarantee_pct: string;
  bank_guarantee_note: string;
  retention_pct: string;
  retention_note: string;
}

const EMPTY: FormState = {
  object_id: null,
  contractor_id: null,
  rate_class_id: null,
  contract_number: "",
  signed_date: "",
  title: "",
  signer: "",
  total_amount: "",
  notes: "",
  advance_pct: "",
  advance_note: "",
  bank_guarantee_pct: "",
  bank_guarantee_note: "",
  retention_pct: "",
  retention_note: "",
};

function fromContract(contract: ContractCard): FormState {
  return {
    object_id: contract.object_id,
    contractor_id: contract.contractor_id,
    rate_class_id: contract.rate_class_id,
    contract_number: contract.contract_number,
    signed_date: contract.signed_date,
    title: contract.title ?? "",
    signer: contract.signer ?? "",
    total_amount: contract.total_amount ?? "",
    notes: contract.notes ?? "",
    advance_pct: contract.advance_pct ?? "",
    advance_note: contract.advance_note ?? "",
    bank_guarantee_pct: contract.bank_guarantee_pct ?? "",
    bank_guarantee_note: contract.bank_guarantee_note ?? "",
    retention_pct: contract.retention_pct ?? "",
    retention_note: contract.retention_note ?? "",
  };
}

/**
 * Форма договора (§7.1).
 *
 * Объект, подрядчик и класс выбираются или **создаются по месту** — решение §6.1.
 * Класс договора можно не указывать: сервер подставит класс объекта, потому что
 * это снимок на момент создания (§4), а `objects.rate_class_id` для того и
 * существует. Подсказка об этом стоит рядом с полем.
 *
 * **Класс заводится здесь же, а не только на экране «Нормативы».** Найденный в
 * работе тупик: на чистой базе классов нет ни одного, у объекта класса тоже нет —
 * и выбирать было нечего. Список открывался пустым, кнопка отправки оставалась
 * активной, а отказ приходил с сервера (§4: колонка NOT NULL, `NULL` недопустим)
 * уже после заполнения всей формы. Поэтому: создание класса по месту (право
 * `admin` — §3), явное предупреждение вместо пустого списка и проверка на клиенте.
 *
 * Сумма — текстовое поле, и уходит на сервер **строкой**: §3 запрещает float, а
 * `<input type="number">` отдал бы именно его.
 */
export function ContractFormDialog({
  open,
  onOpenChange,
  contract,
  onCreated,
}: ContractFormDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        {/*
          Тело формы — отдельный компонент, и в дереве его нет, пока диалог
          закрыт. Поэтому на каждое открытие оно монтируется заново, а начальные
          значения задаёт `useState` — без эффекта, сбрасывающего поля (setState в
          эффекте вызывает каскадный рендер).
        */}
        {open && (
          <ContractForm
            key={contract?.id ?? "new"}
            contract={contract}
            onOpenChange={onOpenChange}
            onCreated={onCreated}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function ContractForm({
  contract,
  onOpenChange,
  onCreated,
}: {
  contract?: ContractCard;
  onOpenChange: (open: boolean) => void;
  onCreated?: (contract: ContractCard) => void;
}) {
  const isEdit = contract !== undefined;
  const [form, setForm] = useState<FormState>(contract ? fromContract(contract) : EMPTY);
  /**
   * Черновик нового подрядчика. Отдельный шаг, а не создание одним нажатием:
   * БИН/ИНН обязателен и уникален (`uq_contractors_inn`), и подставить туда
   * заглушку значило бы засорить справочник значением, которое потом никто не
   * отличит от настоящего. У объекта такой проблемы нет — ему достаточно названия.
   */
  const [contractorDraft, setContractorDraft] = useState<{ title: string; inn: string } | null>(
    null
  );
  /**
   * Черновик нового объекта. Тот же дефект, что был у класса, и найден он тоже
   * пользователем на стенде (§2.2 спеки, решение гейта 1 переиграно): прежний
   * обработчик брал название из поля поиска и при пустом поле **выходил молча**, а
   * список закрывался вместе с полем, в которое надо было вводить название.
   * Черновик даёт и то, чего не было вовсе: адрес — сегодня уходит только `title`,
   * потому что взять адрес неоткуда.
   */
  const [objectDraft, setObjectDraft] = useState<{ title: string; address: string } | null>(
    null
  );
  /**
   * Черновик нового класса — тем же приёмом, что подрядчик, и по той же причине:
   * одним нажатием класс не заводится. Прежний обработчик брал название из поля
   * поиска, а при пустом поле **выходил молча** — и вместе со списком исчезало
   * поле, в которое надо было вводить название (замер §1 спеки: 0 запросов).
   * Черновик даёт и то, чего не было вовсе: описание класса.
   */
  const [classDraft, setClassDraft] = useState<{ title: string; description: string } | null>(
    null
  );

  // Запросы поиска уходят на сервер как `q`: клиентской фильтрации мало, потому
  // что записи за пределами страницы выдачи иначе недостижимы (дефект, найденный
  // внешним ревью).
  const [objectQuery, setObjectQuery] = useState("");
  const [contractorQuery, setContractorQuery] = useState("");
  const objectSearch = useDebounce(objectQuery, 300);
  const contractorSearch = useDebounce(contractorQuery, 300);

  /**
   * Подписи выбранных записей запоминаются отдельно от выдачи: после нового
   * запроса выбранная запись из списка пропадает, и подпись на кнопке иначе
   * подменилась бы плейсхолдером. В режиме правки начальные подписи берутся из
   * карточки — там есть названия объекта и подрядчика.
   */
  const [objectLabel, setObjectLabel] = useState(contract?.object_title ?? "");
  const [contractorLabel, setContractorLabel] = useState(contract?.contractor_title ?? "");
  const [classLabel, setClassLabel] = useState(contract?.rate_class_title ?? "");

  /**
   * Класс выбранного объекта — то самое значение по умолчанию, которое подставит
   * сервер. Форма держит его у себя, чтобы знать, определён ли класс договора,
   * **не отправляя запрос**: иначе единственным способом это выяснить остаётся
   * отказ 422. В режиме правки не нужен — там класс уже зафиксирован снимком.
   */
  const [objectClass, setObjectClass] = useState<{ id: number; title: string } | null>(null);

  /**
   * Классы приходят одним списком, без пагинации (`GET /v1/rate-classes`), поэтому
   * поиск фильтрует локально: серверный `q`, которого требует `EntityCombobox`,
   * нужен лишь пагинированной выдаче — там записи за пределами страницы иначе
   * недостижимы, а здесь страницы нет.
   */
  const [classQuery, setClassQuery] = useState("");

  const objectsQ = useObjects({ q: objectSearch || undefined, page_size: 20 });
  const contractorsQ = useContractors({ q: contractorSearch || undefined, page_size: 20 });
  const classesQ = useRateClasses();

  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  const createObject = useCreateObject();
  const createContractor = useCreateContractor();
  const createRateClass = useCreateRateClass();
  const createContract = useCreateContract();
  const updateContract = useUpdateContract();

  function patch(fields: Partial<FormState>) {
    setForm((prev) => ({ ...prev, ...fields }));
  }

  async function handleSaveObjectDraft() {
    if (!objectDraft) return;
    const title = objectDraft.title.trim();
    if (!title) return;
    try {
      const created = await createObject.mutateAsync({
        title,
        // Края режет фронт — так уже шлёт `ObjectFormDialog`, и второй конвенции
        // на то же поле быть не должно. `objects.address` — NOT NULL, сервер
        // симметрично делает `(address or "").strip()` на обоих путях, поэтому
        // `null` ложится пустой строкой (спека §2.2).
        address: objectDraft.address.trim() || null,
      });
      setObjectLabel(created.title);
      // У нового объекта класса нет — его дефолт задаётся отдельно, а класс
      // договора придётся выбрать здесь.
      setObjectClass(null);
      patch({ object_id: created.id });
      setObjectDraft(null);
    } catch {
      // Причина уже в тосте (чаще всего — название занято); черновик оставляем.
    }
  }

  async function handleSaveClassDraft() {
    if (!classDraft) return;
    const title = classDraft.title.trim();
    if (!title) return;
    try {
      const created = await createRateClass.mutateAsync({
        // Края описания режет фронт — так уже сделано на экране «Нормативы»
        // (`RateClassesTab`), и второй конвенции на то же поле быть не должно.
        // Пробельное описание обязано уйти `null`: `create_rate_class` края не
        // обрезает, и «   » легло бы в базу как есть (спека §2.1).
        title,
        description: classDraft.description.trim() || null,
      });
      setClassLabel(created.title);
      patch({ rate_class_id: created.id });
      setClassDraft(null);
    } catch {
      // Причина уже в тосте (чаще всего — название занято); черновик оставляем.
    }
  }

  async function handleSaveContractorDraft() {
    if (!contractorDraft) return;
    const title = contractorDraft.title.trim();
    const inn = contractorDraft.inn.trim();
    if (!title || !inn) return;
    try {
      const created = await createContractor.mutateAsync({ title, inn });
      setContractorLabel(created.title);
      patch({ contractor_id: created.id });
      setContractorDraft(null);
    } catch {
      // Причина уже в тосте (чаще всего — БИН/ИНН занят); черновик оставляем.
    }
  }

  /**
   * Секция условий свёрнута по умолчанию (спека §2.9) — и в создании, и в правке:
   * на момент заведения карточки условия обычно ещё не согласованы, а в правке
   * лишний раскрытый блок утяжелял бы форму без причины. Пользователь раскрывает
   * её сам, когда условия нужно посмотреть или изменить.
   */
  const [termsOpen, setTermsOpen] = useState(false);

  const classNeedle = classQuery.trim().toLowerCase();
  const visibleClasses = (classesQ.data ?? []).filter(
    (rateClass) => !classNeedle || rateClass.title.toLowerCase().includes(classNeedle)
  );

  /**
   * Класс договора определён: либо выбран здесь, либо его даст объект. Пустой
   * класс сервер отвергает (§4), и знать это до отправки — работа формы.
   */
  const classResolved = form.rate_class_id !== null || objectClass !== null;

  const canSubmit =
    form.object_id !== null &&
    form.contractor_id !== null &&
    form.contract_number.trim().length > 0 &&
    form.signed_date.length > 0 &&
    classResolved;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;

    const payload: ContractInput = {
      object_id: form.object_id as number,
      contractor_id: form.contractor_id as number,
      contract_number: form.contract_number.trim(),
      signed_date: form.signed_date,
      rate_class_id: form.rate_class_id,
      title: form.title.trim() || null,
      signer: form.signer.trim() || null,
      total_amount: normalizeDecimalInput(form.total_amount) || null,
      notes: form.notes.trim() || null,
      // Проценты — тем же приёмом, что сумма; пустой комментарий обязан уйти как
      // `null`, а не пустой строкой (спека §2.5): иначе паспорт напечатал бы
      // пустую оговорку как заведённый факт.
      advance_pct: normalizeDecimalInput(form.advance_pct) || null,
      advance_note: form.advance_note.trim() || null,
      bank_guarantee_pct: normalizeDecimalInput(form.bank_guarantee_pct) || null,
      bank_guarantee_note: form.bank_guarantee_note.trim() || null,
      retention_pct: normalizeDecimalInput(form.retention_pct) || null,
      retention_note: form.retention_note.trim() || null,
    };

    try {
      if (isEdit) {
        await updateContract.mutateAsync({ id: contract.id, input: payload });
      } else {
        const created = await createContract.mutateAsync(payload);
        onCreated?.(created);
      }
      onOpenChange(false);
    } catch {
      // Текст отказа уже показан тостом (`toastApiError`); диалог оставляем
      // открытым, чтобы человек исправил введённое, а не набирал заново.
    }
  }

  const pending = createContract.isPending || updateContract.isPending;

  return (
    <>
      <DialogHeader>
        <DialogTitle>{isEdit ? "Правка договора" : "Новый договор"}</DialogTitle>
        <DialogDescription>
          Карточка договора — источник истины при импорте: объект, подрядчик и
          реквизиты берутся отсюда, а не из файла сметы.
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={handleSubmit} className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="contract-object">Объект</Label>
          <EntityCombobox
            id="contract-object"
            items={objectsQ.data?.items ?? []}
            value={form.object_id}
            onChange={(item) => {
              setObjectLabel(item?.title ?? "");
              setObjectClass(
                item?.rate_class_id != null && item.rate_class_title != null
                  ? { id: item.rate_class_id, title: item.rate_class_title }
                  : null
              );
              patch({ object_id: item?.id ?? null });
            }}
            getLabel={(item) => item.title}
            getHint={(item) => item.rate_class_title ?? undefined}
            placeholder="Выберите объект"
            searchPlaceholder="Название или адрес объекта"
            emptyText="Объект не найден — его можно создать"
            onQueryChange={setObjectQuery}
            selectedLabel={objectLabel}
            loading={objectsQ.isFetching}
            onCreateRequest={(query) => setObjectDraft({ title: query.trim(), address: "" })}
            createLabel="Создать объект"
            disabled={createObject.isPending}
          />
          {objectDraft && (
            <div className="grid gap-2 rounded-md border border-border-subtle p-3">
              <p className="text-xs text-fg-secondary">
                Новый объект. Адрес можно не заполнять — его уточняют позже, а
                объект нужен уже сейчас, чтобы завести договор.
              </p>
              <div className="grid gap-2">
                <Label htmlFor="contract-object-draft-title">
                  Название объекта (обязательно)
                </Label>
                <Input
                  id="contract-object-draft-title"
                  value={objectDraft.title}
                  onChange={(e) => setObjectDraft({ ...objectDraft, title: e.target.value })}
                  required
                  aria-describedby="contract-object-draft-title-hint"
                />
                {/*
                  Условие названо у ПОЛЯ, а не у кнопки, — тот же механизм, что у
                  класса и у `passport-top-n` в `SettingsPage`. Второй конвенции на
                  то же правило в проекте быть не должно.
                */}
                <p id="contract-object-draft-title-hint" className="text-xs text-fg-tertiary">
                  Например: ЖК Северный. Без названия объект не добавить
                </p>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="contract-object-draft-address">Адрес объекта</Label>
                <Input
                  id="contract-object-draft-address"
                  value={objectDraft.address}
                  onChange={(e) => setObjectDraft({ ...objectDraft, address: e.target.value })}
                />
              </div>
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  onClick={handleSaveObjectDraft}
                  // `isPending` не украшение: второй клик отправил бы второй POST
                  // на то же название и получил 409 вместо объекта.
                  disabled={!objectDraft.title.trim() || createObject.isPending}
                >
                  Сохранить объект
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setObjectDraft(null)}
                >
                  Отмена
                </Button>
              </div>
            </div>
          )}
        </div>

        <div className="grid gap-2">
          <Label htmlFor="contract-contractor">Подрядчик</Label>
          <EntityCombobox
            id="contract-contractor"
            items={contractorsQ.data?.items ?? []}
            value={form.contractor_id}
            onChange={(item) => {
              setContractorLabel(item?.title ?? "");
              patch({ contractor_id: item?.id ?? null });
            }}
            getLabel={(item) => item.title}
            getHint={(item) => item.inn}
            placeholder="Выберите подрядчика"
            searchPlaceholder="Название или БИН/ИНН"
            emptyText="Подрядчик не найден — его можно создать"
            onQueryChange={setContractorQuery}
            selectedLabel={contractorLabel}
            loading={contractorsQ.isFetching}
            onCreateRequest={(query) =>
              setContractorDraft({ title: query.trim(), inn: "" })
            }
            createLabel="Создать подрядчика"
            disabled={createContractor.isPending}
          />
          {contractorDraft && (
            <div className="grid gap-2 rounded-md border border-border-subtle p-3">
              <p className="text-xs text-fg-secondary">
                Новый подрядчик. БИН/ИНН обязателен и уникален — по нему подрядчик
                и опознаётся в базе.
              </p>
              <Input
                aria-label="Название подрядчика"
                placeholder="Название"
                value={contractorDraft.title}
                onChange={(e) =>
                  setContractorDraft({ ...contractorDraft, title: e.target.value })
                }
              />
              <Input
                aria-label="БИН/ИНН подрядчика"
                placeholder="БИН / ИНН"
                value={contractorDraft.inn}
                onChange={(e) =>
                  setContractorDraft({ ...contractorDraft, inn: e.target.value })
                }
              />
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  onClick={handleSaveContractorDraft}
                  disabled={
                    !contractorDraft.title.trim() ||
                    !contractorDraft.inn.trim() ||
                    createContractor.isPending
                  }
                >
                  Сохранить подрядчика
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setContractorDraft(null)}
                >
                  Отмена
                </Button>
              </div>
            </div>
          )}
        </div>

        <div className="grid gap-2 sm:grid-cols-2 sm:gap-4">
          <div className="grid gap-2">
            <Label htmlFor="contract-number">Номер договора</Label>
            <Input
              id="contract-number"
              value={form.contract_number}
              onChange={(e) => patch({ contract_number: e.target.value })}
              required
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="contract-signed-date">Дата подписания</Label>
            <Input
              id="contract-signed-date"
              type="date"
              value={form.signed_date}
              onChange={(e) => patch({ signed_date: e.target.value })}
              required
            />
          </div>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="contract-rate-class">Класс объектов</Label>
          <EntityCombobox
            id="contract-rate-class"
            items={visibleClasses}
            value={form.rate_class_id}
            onChange={(item) => {
              setClassLabel(item?.title ?? "");
              patch({ rate_class_id: item?.id ?? null });
            }}
            getLabel={(item) => item.title}
            getHint={(item) => item.description ?? undefined}
            placeholder={
              objectClass ? `Как у объекта: ${objectClass.title}` : "Как у объекта"
            }
            searchPlaceholder="Название класса"
            emptyText={
              isAdmin
                ? "Класс не найден — его можно создать"
                : "Класс не найден. Классы заводит администратор на экране «Нормативы»."
            }
            onQueryChange={setClassQuery}
            selectedLabel={classLabel}
            loading={classesQ.isFetching}
            // Классы — право `admin` (§3), у member кнопки создания нет вовсе:
            // сервер всё равно ответит 403, и предлагать действие бессмысленно.
            onCreateRequest={
              isAdmin
                ? (query) => setClassDraft({ title: query.trim(), description: "" })
                : undefined
            }
            createLabel="Создать класс"
            disabled={createRateClass.isPending}
          />
          {classDraft && (
            <div className="grid gap-2 rounded-md border border-border-subtle p-3">
              <p className="text-xs text-fg-secondary">
                Новый класс объектов. По нему сравниваются нормативы, а в договоре
                класс фиксируется снимком.
              </p>
              <div className="grid gap-2">
                <Label htmlFor="contract-rate-class-draft-title">
                  Название класса (обязательно)
                </Label>
                <Input
                  id="contract-rate-class-draft-title"
                  value={classDraft.title}
                  onChange={(e) => setClassDraft({ ...classDraft, title: e.target.value })}
                  required
                  aria-describedby="contract-rate-class-draft-title-hint"
                />
                {/*
                  Условие названо у ПОЛЯ, а не у кнопки: `disabled` у кнопки
                  нативный, он убирает её из tab-порядка, и `aria-describedby` на
                  ней клавиатурный пользователь не получил бы вовсе. Механизм тот
                  же, что у `passport-top-n` в `SettingsPage`, — второй конвенции
                  на то же правило в проекте быть не должно. Подпись статичная:
                  появляющийся текст пришлось бы делать живой областью, а сказать
                  он должен то же самое.
                */}
                <p
                  id="contract-rate-class-draft-title-hint"
                  className="text-xs text-fg-tertiary"
                >
                  Например: Жилые дома. Без названия класс не добавить
                </p>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="contract-rate-class-draft-description">Описание класса</Label>
                <Input
                  id="contract-rate-class-draft-description"
                  value={classDraft.description}
                  onChange={(e) =>
                    setClassDraft({ ...classDraft, description: e.target.value })
                  }
                />
              </div>
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  onClick={handleSaveClassDraft}
                  // `isPending` здесь не украшение: второй клик отправил бы второй
                  // POST на то же название и получил 409 вместо класса.
                  disabled={!classDraft.title.trim() || createRateClass.isPending}
                >
                  Сохранить класс
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setClassDraft(null)}
                >
                  Отмена
                </Button>
              </div>
            </div>
          )}
          {form.object_id !== null && !classResolved && (
            <p role="alert" className="text-xs text-danger-text">
              У объекта «{objectLabel}» класс не задан, а класс договора обязателен:
              он фиксируется снимком и по нему сравниваются нормативы.{" "}
              {isAdmin
                ? "Выберите класс выше или создайте его здесь же."
                : "Класс заводит администратор на экране «Нормативы»."}
            </p>
          )}
          <p className="text-xs text-fg-tertiary">
            Не выбран — возьмётся класс объекта. Класс договора фиксируется снимком:
            позже переклассификация объекта не изменит отклонения этой сметы.
          </p>
        </div>

        <div className="grid gap-2 sm:grid-cols-2 sm:gap-4">
          <div className="grid gap-2">
            <Label htmlFor="contract-signer">Подписант</Label>
            <Input
              id="contract-signer"
              value={form.signer}
              onChange={(e) => patch({ signer: e.target.value })}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="contract-total">Сумма договора</Label>
            <Input
              id="contract-total"
              inputMode="decimal"
              placeholder="1234567.89"
              value={form.total_amount}
              onChange={(e) => patch({ total_amount: e.target.value })}
            />
          </div>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="contract-title">Название</Label>
          <Input
            id="contract-title"
            value={form.title}
            onChange={(e) => patch({ title: e.target.value })}
          />
        </div>

        <div className="grid gap-2">
          <Label htmlFor="contract-notes">Примечания</Label>
          <Textarea
            id="contract-notes"
            rows={2}
            value={form.notes}
            onChange={(e) => patch({ notes: e.target.value })}
          />
        </div>

        {/*
          Коммерческие условия (спека §2.5, §2.9): свёрнутая секция, не входящая в
          `canSubmit`. Условия на момент заведения карточки обычно ещё не известны,
          и форма не вправе требовать их для создания договора.
        */}
        <Collapsible open={termsOpen} onOpenChange={setTermsOpen}>
          <CollapsibleTrigger
            render={
              <Button type="button" variant="outline" size="sm" className="w-full justify-between">
                <span>Коммерческие условия</span>
                <ChevronDownIcon className="size-4 transition-transform group-aria-expanded/button:rotate-180" />
              </Button>
            }
          />
          <CollapsibleContent className="grid gap-4 pt-4">
            <p className="text-xs text-fg-tertiary">
              Комментарий без процента законен — условие есть, но одним числом не
              выражается (например, аванс траншами). Ноль в проценте отличается от
              пустого поля: «условия нет» против «не заполнено».
            </p>

            <div className="grid gap-2 sm:grid-cols-[120px_1fr] sm:items-start">
              <div className="grid gap-2">
                <Label htmlFor="contract-advance-pct">Аванс, %</Label>
                <Input
                  id="contract-advance-pct"
                  inputMode="decimal"
                  placeholder="30"
                  value={form.advance_pct}
                  onChange={(e) => patch({ advance_pct: e.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="contract-advance-note">Оговорка к авансу</Label>
                <Textarea
                  id="contract-advance-note"
                  rows={2}
                  value={form.advance_note}
                  onChange={(e) => patch({ advance_note: e.target.value })}
                />
              </div>
            </div>

            <div className="grid gap-2 sm:grid-cols-[120px_1fr] sm:items-start">
              <div className="grid gap-2">
                <Label htmlFor="contract-bank-guarantee-pct">Банк. гарантия, %</Label>
                <Input
                  id="contract-bank-guarantee-pct"
                  inputMode="decimal"
                  placeholder="10"
                  value={form.bank_guarantee_pct}
                  onChange={(e) => patch({ bank_guarantee_pct: e.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="contract-bank-guarantee-note">
                  Оговорка к банковской гарантии
                </Label>
                <Textarea
                  id="contract-bank-guarantee-note"
                  rows={2}
                  value={form.bank_guarantee_note}
                  onChange={(e) => patch({ bank_guarantee_note: e.target.value })}
                />
              </div>
            </div>

            <div className="grid gap-2 sm:grid-cols-[120px_1fr] sm:items-start">
              <div className="grid gap-2">
                <Label htmlFor="contract-retention-pct">Удержание, %</Label>
                <Input
                  id="contract-retention-pct"
                  inputMode="decimal"
                  placeholder="5"
                  value={form.retention_pct}
                  onChange={(e) => patch({ retention_pct: e.target.value })}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="contract-retention-note">Оговорка к удержанию</Label>
                <Textarea
                  id="contract-retention-note"
                  rows={2}
                  value={form.retention_note}
                  onChange={(e) => patch({ retention_note: e.target.value })}
                />
              </div>
            </div>
          </CollapsibleContent>
        </Collapsible>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button type="submit" disabled={!canSubmit || pending}>
            {isEdit ? "Сохранить" : "Создать договор"}
          </Button>
        </DialogFooter>
      </form>
    </>
  );
}
