import { useState } from "react";

import { EntityCombobox } from "@/components/domain/EntityCombobox";
import { Button } from "@/components/ui/button";
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

  async function handleCreateObject(query: string) {
    const title = query.trim();
    if (!title) return;
    try {
      const created = await createObject.mutateAsync({ title });
      setObjectLabel(created.title);
      // У нового объекта класса нет — его дефолт задаётся отдельно, а класс
      // договора придётся выбрать здесь.
      setObjectClass(null);
      patch({ object_id: created.id });
    } catch {
      // Причина уже в тосте — как правило, название занято.
    }
  }

  async function handleCreateRateClass(query: string) {
    const title = query.trim();
    if (!title) return;
    try {
      const created = await createRateClass.mutateAsync({ title, description: null });
      setClassLabel(created.title);
      patch({ rate_class_id: created.id });
    } catch {
      // Причина уже в тосте — как правило, название занято.
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
            onCreateRequest={handleCreateObject}
            createLabel="Создать объект"
            disabled={createObject.isPending}
          />
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
            onCreateRequest={isAdmin ? handleCreateRateClass : undefined}
            createLabel="Создать класс"
            disabled={createRateClass.isPending}
          />
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
