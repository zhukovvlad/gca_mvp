import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";

import { UnallocatedWorkbench } from "@/components/unallocated/UnallocatedWorkbench";
import {
  conflictMark,
  DIAGNOSTIC_REASON,
  DIAGNOSTICS_HEADING_HINT,
  diagnosticsHeading,
  LOAD_ERROR,
  MANUAL_EMPTY,
  MANUAL_HEADING,
  MANUAL_HINT,
  partialMark,
  PENDING_EMPTY,
  PENDING_HINT,
  pendingHeading,
  positionsLabel,
  ROUND_GONE,
  sectionKeyOf,
  sheetSubtitle,
  sheetTitle,
} from "@/components/unallocated/roundUnallocatedCopy";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import {
  apiErrorStatus,
  useClearRoundCategoryOverride,
  useRoundUnallocated,
  useSetRoundCategoryOverride,
} from "@/services/queries";
import { qk } from "@/services/queryKeys";
import type { RoundManualAssignment, RoundUnallocatedSection, TenderRoundRow } from "@/types/domain";

/**
 * Sheet-верстак этапного разноса «Нераспределённого» (спека
 * 2026-09-01-round-unallocated-design.md §2.3, §2.4, §2.5, §2.7; макет
 * 2026-09-01-offer-unallocated-mockup.html, раздел 2/2а; план, задача 11).
 *
 * Тонкая обёртка над `UnallocatedWorkbench` (задача 10): правая колонка дерева
 * несёт счётчик позиций, а не деньги (§2.3 — денег в ответе нет вовсе),
 * сортировка — файловый порядок ведомости (`compareSiblings` не передаётся),
 * пометки `partial`/`conflict` — под названием раздела, поле «Заметка» в
 * пикере получает существующие заметки РАЗДЕЛА (не участника — раздел один на
 * этап), а диагностика §2.5 идёт `children`-блоком после «Разнесено вручную».
 *
 * GET ленивый — `useRoundUnallocated` получает `open` как `enabled`: закрытый
 * Sheet не шлёт запрос вовсе (§2.7). Свои loading/error/404 — состояния карты
 * тендера сюда не наследуются.
 */
export function UnallocatedSheet({
  tenderId,
  round,
  open,
  onOpenChange,
}: {
  tenderId: number;
  round: TenderRoundRow | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const query = useRoundUnallocated(tenderId, round?.id, open);
  const setOverride = useSetRoundCategoryOverride();
  const clearOverride = useClearRoundCategoryOverride();
  const disabled = setOverride.isPending || clearOverride.isPending;

  const data = query.data;
  const isGone = query.isError && apiErrorStatus(query.error) === 404;

  // 404 «раунд/сметы больше недоступны» — карточка тендера держит устаревший
  // счётчик и устаревший состав раундов, поэтому «Обновить карточку»
  // инвалидирует именно её, а не сам GET Sheet (Sheet и так закрывается).
  function handleRoundGone() {
    queryClient.invalidateQueries({ queryKey: qk.tenders.card(tenderId) });
    onOpenChange(false);
  }

  return (
    // Гейт K (ревью задачи 11): открывать диалог БЕЗ раунда некуда — заголовка
    // нет и не будет (он несёт `round.stage_no`), а незакрытый диалог без
    // `Title` — предупреждение доступности примитива `@base-ui/react/dialog`.
    // Путь недостижим из вызова задачи 12 (`open={unallocatedRound !== undefined}`),
    // но ничто не мешает вызвать компонент так напрямую — гейт на входе дешевле
    // документации «так не делайте».
    <Sheet open={open && round !== undefined} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-2xl">
        {round && (
          <>
            <SheetHeader>
              <SheetTitle>{sheetTitle(round.stage_no)}</SheetTitle>
              {data && <SheetDescription>{sheetSubtitle(data.offers_count)}</SheetDescription>}
            </SheetHeader>

            {query.isPending && (
              <div data-testid="unallocated-sheet-skeleton" className="space-y-2 px-4 py-3">
                {/* Пропорции макета (раздел 2а): полосы 80% / 100% / 60%, не
                    ближайшие доли Tailwind. */}
                <Skeleton className="h-4 w-[80%]" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-[60%]" />
              </div>
            )}

            {query.isError && (
              // Отказ на боковом Sheet — не абзац бегущего текста: та же форма
              // тёплого бокса, что у `InflationRefusalBanner.tsx` и предупреждений
              // `PassportHeader.tsx` (`rounded-* border-warning-border bg-warning-soft
              // text-warning-text`, `role="alert"`) — единственная такая «коробка» в
              // проекте, готового обёрточного компонента под неё нет.
              <div
                role="alert"
                className="mx-4 mt-3 flex items-start gap-2 rounded-lg border border-warning-border bg-warning-soft px-3 py-2 text-sm text-warning-text"
              >
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                <div className="min-w-0 grow">
                  <p>{isGone ? ROUND_GONE : LOAD_ERROR}</p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => (isGone ? handleRoundGone() : void query.refetch())}
                  >
                    {isGone ? "Обновить карточку" : "Повторить"}
                  </Button>
                </div>
              </div>
            )}

            {data && (
              <UnallocatedWorkbench
                sections={data.sections.map(toSheetSection)}
                manual={data.manual.map(toSheetManualRow)}
                categoryOptions={data.category_options}
                copy={{
                  sectionsHeading: pendingHeading(data.sections.length),
                  sectionsHint: PENDING_HINT,
                  sectionsEmpty: PENDING_EMPTY,
                  manualHeading: MANUAL_HEADING,
                  manualHint: MANUAL_HINT,
                  manualEmpty: MANUAL_EMPTY,
                }}
                testId={(section) => section.key}
                // Файловый порядок ведомости (§2.7, макет) — компаратор
                // сиблингов не передаётся, дерево остаётся в порядке прихода
                // из ответа сервера (представительная смета, §2.3).
                renderAside={renderAside}
                // Мокап (раздел 2, блок «Разнесено вручную», `.mrow`) не несёт
                // третьей колонки у ручных решений — только раздел и «снять»;
                // счётчик позиций там неуместен так же, как и деньги.
                renderManualAside={() => null}
                renderMark={renderMark}
                noteField={{ existingNotes: sectionNotes }}
                onPick={(section, option, note) =>
                  setOverride.mutate({
                    tenderId,
                    roundId: round.id,
                    lotKey: section.lot_key,
                    positionKey: section.position_key_in_proposal,
                    workCategoryId: option.id,
                    note,
                  })
                }
                onClear={(row) =>
                  clearOverride.mutate({
                    tenderId,
                    roundId: round.id,
                    lotKey: row.lot_key,
                    positionKey: row.position_key_in_proposal,
                  })
                }
                disabled={disabled}
              >
                {data.diagnostics.length > 0 && (
                  <div className="border-t border-border-subtle px-4 py-3">
                    <h3 className="text-sm font-semibold text-fg">
                      {diagnosticsHeading(data.diagnostics.reduce((sum, d) => sum + d.rows, 0))}
                    </h3>
                    <p className="mt-0.5 text-2xs text-fg-tertiary">{DIAGNOSTICS_HEADING_HINT}</p>
                    <div className="px-2 py-1">
                      {data.diagnostics.map((d, i) => (
                        <div
                          key={`${d.contractor_title}:${d.title}:${i}`}
                          data-testid="diagnostic-row"
                          className="border-b border-border-subtle px-2 py-2 text-xs last:border-b-0"
                        >
                          <b className="font-semibold">{d.contractor_title}</b> · «{d.title}»
                          <p className="mt-0.5 text-2xs text-fg-tertiary">{DIAGNOSTIC_REASON[d.code]}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </UnallocatedWorkbench>
            )}
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
//  Маппинг RoundUnallocated → пропсы UnallocatedWorkbench
// ---------------------------------------------------------------------------

/** `key`/`parentKey` дерева — `lot_key` и `position_key_in_proposal` вместе
 *  (§2.3 — логический ключ раздела в тендере составной, не единственный id,
 *  каким был `position_item_id` паспорта). */
type SheetSection = RoundUnallocatedSection & { key: string; parentKey: string | null };

function toSheetSection(section: RoundUnallocatedSection): SheetSection {
  return {
    ...section,
    key: sectionKeyOf(section.lot_key, section.position_key_in_proposal),
    parentKey: section.parent_key ? sectionKeyOf(section.parent_key[0], section.parent_key[1]) : null,
  };
}

type SheetManualRow = RoundManualAssignment & { key: string };

function toSheetManualRow(row: RoundManualAssignment): SheetManualRow {
  return { ...row, key: sectionKeyOf(row.lot_key, row.position_key_in_proposal) };
}

/** Правая колонка дерева — счётчик позиций, НЕ деньги (§2.3: ответ раундового
 *  GET вообще не несёт сумм). `rows` — полный размер файлового поддерева,
 *  стабилен независимо от вложенных решений. */
function renderAside(section: SheetSection) {
  return (
    <div data-testid={`rows-${section.key}`} className="text-right text-2xs text-fg-tertiary">
      {positionsLabel(section.rows)}
    </div>
  );
}

/** Пометка под названием раздела — только `partial`/`conflict`; у
 *  `unassigned` пометки нет вовсе. */
function renderMark(section: SheetSection) {
  if (section.state === "partial") {
    return <p className="text-2xs text-warning-text">{partialMark(section.partial)}</p>;
  }
  if (section.state === "conflict") {
    return <p className="text-2xs text-warning-text">{conflictMark(section.conflict)}</p>;
  }
  return null;
}

/** Существующие заметки РАЗДЕЛА для пикера (§2.4, §2.7) — из блока `partial`
 *  или `conflict`; у `unassigned` заметок ещё нет ни у кого. */
function sectionNotes(section: SheetSection): (string | null)[] {
  if (section.state === "partial") return section.partial.notes;
  if (section.state === "conflict") return section.conflict.notes;
  return [];
}
