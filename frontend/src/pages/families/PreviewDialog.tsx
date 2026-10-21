import { useEffect, useState } from "react";

import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatUsd } from "@/lib/format";
import {
  apiErrorCode,
  useFamilySchema,
  useReaskConfirm,
  useReaskPreview,
  useRebuildSchema,
} from "@/services/queries";
import type { FamilySchema, PreviewTarget } from "@/types/domain";

import {
  BATCH_SOURCE_LABEL,
  PARAMETER_KIND_LABEL,
  PARAMETER_ORIGIN_LABEL,
  MAX_SCHEMA_PARAMETERS,
  PARAMETER_ROLE_LABEL,
  pluralRu,
  schemaRefusalLabel,
} from "./labels";
import { rowsTotal } from "./schema-edit/drafts";

const PREVIEW_CHANGED_NOTICE =
  "Оценка изменилась, пока окно было открыто. Проверьте новые числа и подтвердите ещё раз.";

function titleOf(target: PreviewTarget, unitLabel: string): string {
  switch (target.kind) {
    case "unit":
      return `Перезапросить ${unitLabel}?`;
    case "config":
      return "Перезапросить всё?";
    case "batch":
      return "Поставить удержанную пачку?";
    case "schema":
      return "Пересобрать схему?";
  }
}

function descriptionOf(target: PreviewTarget, unitLabel: string): string {
  switch (target.kind) {
    case "unit":
      return `Модель заново выберет семью для контекстов единицы ${unitLabel}, у которых нет семьи, назначенной человеком. Подтверждённые контексты не трогаются.`;
    case "config":
      return "Промпт, модель или параметры изменились: заново будут запрошены все применимые контексты. Подтверждённые человеком не трогаются.";
    case "batch":
      return "Задания ставятся по текущим отпечаткам входа, а не по сохранённым в момент импорта. Если бюджета не хватит, часть заданий подождёт следующих суток.";
    case "schema":
      return "Модель построит свою часть схемы заново. Текущая схема остаётся в силе, пока новая не будет готова.";
  }
}

const valuesLabel = (n: number) => `${n} ${pluralRu(n, "значение", "значения", "значений")}`;
const rowsLabel = (n: number) => `${n} ${pluralRu(n, "строка", "строки", "строк")}`;

/** Число параметров, которое модель предлагает за раз (спека 3в §2.8). */
const MODEL_PARAMETERS_PER_REBUILD = 3;

function schemaDescription(schema: FamilySchema | undefined): string {
  const base = descriptionOf({ kind: "schema", familyId: 0 }, "");
  if (!schema) return base;
  const human = schema.parameters.filter((parameter) => parameter.origin === "human").length;
  if (human === 0) return base;
  if (human >= MAX_SCHEMA_PARAMETERS) {
    // Модель не вызывается: версия собирается из параметров человека (спека 3в §2.8).
    return `Схема соберётся заново из ваших параметров; модель новых параметров не получит: все ${MAX_SCHEMA_PARAMETERS} мест заняты. Текущая схема остаётся в силе, пока новая не будет готова.`;
  }
  return `${base} Ваши параметры она увидит в запросе и не будет повторять.`;
}

function SchemaRebuildGroups({ schemaQ }: { schemaQ: ReturnType<typeof useFamilySchema> }) {
  if (schemaQ.isError) {
    return (
      <p role="alert" className="text-sm text-danger-text">
        Не удалось получить схему семьи: параметры, которые останутся и заменятся, не показаны.
      </p>
    );
  }
  const schema = schemaQ.data;
  if (!schema) return <Skeleton className="h-16 w-full" />;

  const human = schema.parameters.filter((parameter) => parameter.origin === "human");
  const model = schema.parameters.filter((parameter) => parameter.origin === "model");

  return (
    <>
      {human.length > 0 && (
        <section aria-label="Останутся — ваши" className="grid gap-1">
          <h5 className="text-xs font-normal tracking-wide text-fg-tertiary uppercase">Останутся — ваши</h5>
          <ul className="grid gap-1 text-sm">
            {human.map((parameter) => (
              <li key={parameter.id} className="flex items-baseline justify-between gap-2">
                <span>
                  <span className="font-medium">{parameter.name}</span>
                  <span className="text-xs text-fg-tertiary">
                    {" · "}
                    {PARAMETER_ROLE_LABEL[parameter.role].toLowerCase()}
                    {" · "}
                    {PARAMETER_KIND_LABEL[parameter.value_kind].toLowerCase()}
                  </span>
                </span>
                <Badge variant="outline">{PARAMETER_ORIGIN_LABEL.human}</Badge>
              </li>
            ))}
          </ul>
        </section>
      )}
      {model.length > 0 && (
        <section aria-label="Заменятся — параметры модели, которые никто не правил" className="grid gap-1">
          <h5 className="text-xs font-normal tracking-wide text-fg-tertiary uppercase">
            Заменятся — параметры модели, которые никто не правил
          </h5>
          <ul className="grid gap-1 text-sm">
            {model.map((parameter) => (
              <li key={parameter.id} className="flex items-baseline justify-between gap-2">
                <span>
                  <span className="font-medium">{parameter.name}</span>
                  <span className="text-xs text-fg-tertiary">
                    {" · "}
                    {valuesLabel(parameter.values.filter((value) => value.merged_into_id === null).length)}
                    {" · "}
                    {rowsLabel(rowsTotal(parameter))}
                  </span>
                </span>
                <Badge variant="outline">{PARAMETER_ORIGIN_LABEL.model}</Badge>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

/** Строка «Что произойдёт» про параметры модели; пока схемы нет, ничего не утверждается. */
function modelLimitLine(schema: FamilySchema | undefined): string | null {
  if (!schema) return null;
  const human = schema.parameters.filter((parameter) => parameter.origin === "human").length;
  const room = Math.min(MODEL_PARAMETERS_PER_REBUILD, MAX_SCHEMA_PARAMETERS - human);
  const taken = `занято ${human} из ${MAX_SCHEMA_PARAMETERS}`;
  if (room <= 0) return `Модель новых параметров не предложит: ${taken}.`;
  if (room === 1) return `Модель предложит не больше одного нового параметра: ${taken}.`;
  return `Модель предложит до ${room} новых параметров: ${taken}.`;
}

const VALUES_NOT_INCLUDED_NOTICE =
  "Оценка не включает задания значений: у семьи пока нет схемы, их стоимость появится после заморозки схемы. Показанная сумма неполная.";

const VALUES_APPROXIMATE_NOTICE =
  "Оценка значений приближённая: параметров новой схемы до ответа модели нет, поэтому считаются нынешние параметры модели.";

function confirmLabelOf(target: PreviewTarget): string {
  if (target.kind === "schema") return "Пересобрать";
  return target.kind === "batch" ? "Поставить" : "Поставить в очередь";
}

interface PreviewDialogProps {
  /** Что перезапрашивается; `null` — диалог закрыт. */
  target: PreviewTarget | null;
  /** Читаемая подпись единицы для заголовка (`м²`); нужна только для `kind: "unit"`. */
  unitLabel?: string;
  onClose: () => void;
}

/**
 * Общий диалог четырёх действий, ставящих задания на деньги: перезапрос единицы,
 * перезапрос по конфигурации, постановка удержанной пачки (спека
 * semantic-suggestions §2.10) и пересборка схемы семьи (спека
 * 2026-10-02-catalog-variants-design.md §2.12). Открытие запрашивает preview;
 * подтверждение шлёт `preview_hash` ИЗ ПОКАЗАННОГО preview. Ответ `409
 * preview_changed` не повторяет подтверждение молча. У трёх первых действий диалог
 * сообщает «оценка изменилась» и запрашивает preview заново, человек видит новые
 * числа и решает сам; у пересборки схемы показанная оценка сбрасывается, подтвердить
 * нельзя, пока окно не откроют заново. `target` обязан быть стабильной ссылкой:
 * новая цель на каждый рендер перезапускает предпросмотр.
 */
export function PreviewDialog({ target, unitLabel = "", onClose }: PreviewDialogProps) {
  const preview = useReaskPreview();
  const reaskConfirm = useReaskConfirm();
  const rebuild = useRebuildSchema();
  const [notice, setNotice] = useState<string | null>(null);
  // Тот же ключ запроса, что у блока схемы: второго запроса не будет.
  const schemaQ = useFamilySchema(target?.kind === "schema" ? target.familyId : 0, {
    enabled: target?.kind === "schema",
  });

  const { mutate: requestPreview, reset: resetPreview } = preview;
  const { reset: resetReaskConfirm } = reaskConfirm;
  const { reset: resetRebuild } = rebuild;

  useEffect(() => {
    if (target === null) return;
    requestPreview(target);
    return () => {
      resetPreview();
      resetReaskConfirm();
      resetRebuild();
    };
  }, [target, requestPreview, resetPreview, resetReaskConfirm, resetRebuild]);

  function handleConfirm() {
    if (target === null || !preview.data) return;
    const previewHash = preview.data.preview_hash;
    const callbacks = {
      onSuccess: () => {
        setNotice(null);
        onClose();
      },
      onError: (error: unknown) => {
        if (apiErrorCode(error) !== "preview_changed") return;
        if (target.kind === "schema") {
          // Состояние семьи ушло от показанной оценки: старый хэш повторно не шлётся.
          setNotice(schemaRefusalLabel("preview_changed"));
          resetPreview();
          return;
        }
        setNotice(PREVIEW_CHANGED_NOTICE);
        requestPreview(target);
      },
    };
    if (target.kind === "schema") {
      rebuild.mutate({ familyId: target.familyId, previewHash }, callbacks);
    } else {
      reaskConfirm.mutate({ target, previewHash }, callbacks);
    }
  }

  function handleOpenChange(open: boolean) {
    if (open) return;
    setNotice(null);
    onClose();
  }

  const data = preview.data;
  const limitLine = modelLimitLine(schemaQ.data);
  // Пометки о значениях приходят только в предпросмотре пересборки, то есть внутри рамки
  // «Что произойдёт»: своей рамки им не нужно.
  const noteClass = "text-warning-text";
  const estimate = (
    <>
      {data && (
        <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-2 text-sm">
          <dt className="text-fg-secondary">Контекстов</dt>
          <dd className="text-right font-medium tabular-nums">{data.context_count.toLocaleString("ru-RU")}</dd>
          <dt className="text-fg-secondary">Резерв</dt>
          <dd className="text-right font-medium tabular-nums">{formatUsd(data.reserve_usd)}</dd>
          <dt className="text-fg-secondary">Ожидаемая цена при попадании в кэш</dt>
          <dd className="text-right font-medium tabular-nums">≈ {formatUsd(data.expected_cached_usd)}</dd>
        </dl>
      )}

      {data && data.values_included === false && (
        <p role="note" className={noteClass}>
          {VALUES_NOT_INCLUDED_NOTICE}
        </p>
      )}

      {data && data.values_approximate === true && (
        <p role="note" className={noteClass}>
          {VALUES_APPROXIMATE_NOTICE}
        </p>
      )}
    </>
  );

  const busy = preview.isPending || reaskConfirm.isPending || rebuild.isPending;

  return (
    <Dialog open={target !== null} onOpenChange={handleOpenChange}>
      {target !== null && (
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{titleOf(target, unitLabel)}</DialogTitle>
            <DialogDescription>
              {target.kind === "batch"
                ? BATCH_SOURCE_LABEL[target.source]
                : target.kind === "schema"
                  ? schemaDescription(schemaQ.data)
                  : descriptionOf(target, unitLabel)}
            </DialogDescription>
          </DialogHeader>

          {target.kind === "schema" && <SchemaRebuildGroups schemaQ={schemaQ} />}

          {notice && (
            <p role="status" className="rounded-md border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text">
              {notice}
            </p>
          )}

          {preview.isError && (
            <p role="alert" className="text-sm text-danger-text">
              Не удалось получить оценку. Закройте окно и откройте его заново.
            </p>
          )}

          {preview.isPending && !data && <Skeleton className="h-20 w-full" />}

          {target.kind === "schema" ? (
            (data || limitLine !== null) && (
              <section
                aria-label="Что произойдёт"
                className="grid gap-2 rounded-md border border-warning-border bg-warning-soft px-3 py-2 text-sm"
              >
                <h5 className="font-medium text-fg">Что произойдёт</h5>
                {limitLine !== null && <p className="text-fg-secondary">{limitLine}</p>}
                {estimate}
              </section>
            )
          ) : (
            estimate
          )}

          {target.kind === "batch" && (
            <p className="text-sm text-fg-secondary">{descriptionOf(target, unitLabel)}</p>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => handleOpenChange(false)}>
              Отмена
            </Button>
            <Button
              disabled={busy || !data}
              loading={reaskConfirm.isPending || rebuild.isPending}
              onClick={handleConfirm}
            >
              {confirmLabelOf(target)}
            </Button>
          </DialogFooter>
        </DialogContent>
      )}
    </Dialog>
  );
}
