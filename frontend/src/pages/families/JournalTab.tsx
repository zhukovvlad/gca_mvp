import { Fragment, useEffect, useState } from "react";
import { Search } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { EntityCombobox } from "@/components/domain/EntityCombobox";
import { Pager } from "@/components/domain/Pager";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Surface } from "@/components/ui-domain/Surface";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { parseId } from "@/lib/urlParams";
import { cn } from "@/lib/utils";
import { useDebounce } from "@/lib/useDebounce";
import { useJournal, useWorkFamilies } from "@/services/queries";
import type { JournalItem, JournalKind, JournalState } from "@/types/domain";

import { JournalCard } from "./JournalCard";
import {
  JOURNAL_KIND_FILTER_LABEL,
  JOURNAL_KIND_LABEL,
  JOURNAL_KIND_ORDER,
  JOURNAL_KIND_TINT,
  JOURNAL_STATE_FILTER_LABEL,
} from "./labels";
import { FetchingBar, ListSkeleton } from "./pending";
import { usePersistedPageSize } from "./usePersistedPageSize";

const ALL = "all";
const ANY = "any";
const DEFAULT_PAGE_SIZE = 20;
const STATE_OPTIONS: JournalState[] = ["active", "undone", "locked"];

/** Параметр адреса, которым выбранная запись открывается ссылкой (`/families?tab=journal&entry=<id>`). */
const ENTRY_PARAM = "entry";

/** Ключ дня записи по местному времени: записи одного дня стоят под одним заголовком. */
function dayKey(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/** «11 ноября 2026» — без «г.», как в макете. */
function dayLabel(iso: string): string {
  const d = new Date(iso);
  return `${d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" })} ${d.getFullYear()}`;
}

function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

/** Что произошло, одной строкой: части деления, склейка, добавленное значение, приравнивание. */
function lineOf(item: JournalItem): string {
  const quoted = (text: string) => `«${text}»`;
  if (item.kind === "split") {
    return `Контекст ${item.context ? `${quoted(item.context.title)} ` : ""}разделён на ${item.summary_values.length}: ${item.summary_values.join(", ")}`;
  }
  if (item.kind === "composite") {
    return `${quoted(item.text ?? "")} → ${item.summary_values.map(quoted).join(", ")}`;
  }
  if (item.kind === "value_added") {
    return `Добавлено ${quoted(item.text ?? "")} в ${quoted(item.parameter?.name ?? "")}`;
  }
  return `${quoted(item.text ?? "")} = ${quoted(item.summary_values[1] ?? "")} в ${quoted(item.parameter?.name ?? "")}`;
}

function stateChip(item: JournalItem): string | null {
  if (item.state === "undone") return item.restored_at !== null ? "автоматика возвращена" : "отменено";
  if (item.state === "locked") return "правили вручную";
  return null;
}

interface JournalTabProps {
  /** Вкладка открыта: журнал считается просмотренным (счётчик новых на вкладке сбрасывается). */
  onViewed?: () => void;
}

/**
 * Вкладка «Журнал ИИ» (спека упрощения каталога §2.7): лента автодействий по дням с отборами —
 * вид, семья, состояние, поиск — слева и карточка выбранной записи справа (раскладка
 * {@link ContextsTab}). Выбор записи отражается в адресе (`?tab=journal&entry=<id>`), и запись
 * открывается ссылкой: карточка грузится по id, а не из страницы ленты.
 */
export function JournalTab({ onViewed }: JournalTabProps = {}) {
  const [kind, setKind] = useState<string>(ALL);
  const [state, setState] = useState<string>(ANY);
  const [familyId, setFamilyId] = useState<number | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = usePersistedPageSize("gca.families.journal.pageSize", DEFAULT_PAGE_SIZE);

  const [searchParams, setSearchParams] = useSearchParams();
  const urlEntryId = parseId(searchParams.get(ENTRY_PARAM));
  const [selectedId, setSelectedId] = useState<number | null>(urlEntryId);
  // Адрес сменили снаружи (ссылка на другую запись): выбор следует за ним. Собственную запись в
  // адрес за внешнюю смену не считаем. Правка состояния во время рендера — приём `ContextsTab`.
  const [seenUrlEntryId, setSeenUrlEntryId] = useState(urlEntryId);
  if (urlEntryId !== seenUrlEntryId) {
    setSeenUrlEntryId(urlEntryId);
    if (urlEntryId !== selectedId) setSelectedId(urlEntryId);
  }
  useEffect(() => {
    if (urlEntryId === selectedId) return;
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (selectedId === null) {
          next.delete(ENTRY_PARAM);
        } else {
          next.set("tab", "journal");
          next.set(ENTRY_PARAM, String(selectedId));
        }
        return next;
      },
      { replace: true }
    );
  }, [selectedId, urlEntryId, setSearchParams]);

  useEffect(() => {
    onViewed?.();
  }, [onViewed]);

  const search = useDebounce(searchInput, 300);
  const familiesQ = useWorkFamilies();
  const journalQ = useJournal({
    kind: kind === ALL ? undefined : (kind as JournalKind),
    family_id: familyId ?? undefined,
    state: state === ANY ? undefined : (state as JournalState),
    q: search.trim() || undefined,
    limit: pageSize,
    offset: (page - 1) * pageSize,
  });
  const data = journalQ.data;

  // Зажим страницы: выдача сузилась (отмена, отбор), и текущая страница ушла за её конец.
  if (data) {
    const lastValidPage = Math.max(1, Math.ceil(data.total / pageSize));
    if (page > lastValidPage) setPage(lastValidPage);
  }

  const total = data ? JOURNAL_KIND_ORDER.reduce((sum, name) => sum + data.counts[name], 0) : null;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,560px)] lg:items-start">
      <div className="grid gap-4">
        <div className="flex flex-wrap items-end gap-3">
          <ToggleGroup
            variant="outline"
            size="sm"
            spacing={0}
            aria-label="Вид записи"
            value={[kind]}
            onValueChange={(value) => {
              // Нажатая кнопка не снимается: отбор по виду всегда назван.
              if (value.length > 0) {
                setKind(value[0]);
                setPage(1);
              }
            }}
          >
            <ToggleGroupItem value={ALL} className="aria-pressed:bg-primary aria-pressed:text-primary-foreground">
              {`Все${total === null ? "" : ` ${total}`}`}
            </ToggleGroupItem>
            {JOURNAL_KIND_ORDER.map((name) => (
              <ToggleGroupItem key={name} value={name} className="aria-pressed:bg-primary aria-pressed:text-primary-foreground">
                {`${JOURNAL_KIND_FILTER_LABEL[name]}${data ? ` ${data.counts[name]}` : ""}`}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>

          <div className="grid min-w-56 gap-1">
            <Label htmlFor="journal-family" className="text-xs text-fg-tertiary">Семья</Label>
            <EntityCombobox
              id="journal-family"
              items={familiesQ.data ?? []}
              value={familyId}
              onChange={(family) => {
                setFamilyId(family?.id ?? null);
                setPage(1);
              }}
              getLabel={(family) => family.title}
              getHint={(family) => family.unit_symbol ?? undefined}
              placeholder="Все семьи"
              searchPlaceholder="Найти семью…"
              emptyText="Ничего не найдено"
            />
          </div>

          <div className="grid gap-1">
            <Label htmlFor="journal-state" className="text-xs text-fg-tertiary">Состояние</Label>
            <Select
              value={state}
              onValueChange={(next) => {
                setState(next ?? ANY);
                setPage(1);
              }}
            >
              <SelectTrigger id="journal-state" className="w-48">
                <SelectValue>
                  {(raw) => (!raw || raw === ANY ? "Действуют и отменённые" : JOURNAL_STATE_FILTER_LABEL[raw as JournalState])}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Действуют и отменённые</SelectItem>
                {STATE_OPTIONS.map((option) => (
                  <SelectItem key={option} value={option}>
                    {JOURNAL_STATE_FILTER_LABEL[option]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <InputGroup className="w-60 flex-none">
            <InputGroupInput
              aria-label="Поиск по журналу"
              placeholder="Значение, контекст, семья"
              value={searchInput}
              onChange={(e) => {
                setSearchInput(e.target.value);
                setPage(1);
              }}
            />
            <InputGroupAddon align="inline-start">
              <Search size={13} />
            </InputGroupAddon>
          </InputGroup>
        </div>

        <FetchingBar active={journalQ.isFetching && !journalQ.isPending} />

        {journalQ.isPending && <ListSkeleton />}

        {journalQ.isError && <EmptyState title="Ошибка загрузки" description="Не удалось получить журнал." />}

        {data && data.items.length === 0 && (
          <EmptyState title="Записей с такими отборами нет" description="Ни одна запись не подходит под текущие отборы." />
        )}

        {data && data.items.length > 0 && (
          <>
            <Surface padding="none" className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-40 text-xs font-normal text-fg-tertiary">Что</TableHead>
                    <TableHead className="text-xs font-normal text-fg-tertiary">Запись</TableHead>
                    <TableHead className="w-52 text-xs font-normal text-fg-tertiary">Семья</TableHead>
                    <TableHead className="w-20 text-xs font-normal text-fg-tertiary">Когда</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((item, index) => {
                    const newDay = index === 0 || dayKey(data.items[index - 1].created_at) !== dayKey(item.created_at);
                    const chip = stateChip(item);
                    const struck = item.state === "undone";
                    return (
                      <Fragment key={item.id}>
                        {newDay && (
                          <TableRow className="hover:bg-transparent">
                            <TableCell
                              colSpan={4}
                              className="bg-surface-sunken py-1.5 text-[11.5px] tracking-wide text-fg-tertiary uppercase"
                            >
                              {dayLabel(item.created_at)}
                            </TableCell>
                          </TableRow>
                        )}
                        <TableRow
                          data-journal-id={item.id}
                          role="row"
                          tabIndex={0}
                          aria-selected={item.id === selectedId}
                          onClick={() => setSelectedId(item.id)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              setSelectedId(item.id);
                            }
                          }}
                          className={cn(
                            "cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                            item.id === selectedId && "bg-accent-soft hover:bg-accent-soft"
                          )}
                        >
                          <TableCell className="align-top">
                            <span className={cn("inline-flex rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold whitespace-nowrap", JOURNAL_KIND_TINT[item.kind])}>
                              {JOURNAL_KIND_LABEL[item.kind]}
                            </span>
                          </TableCell>
                          <TableCell className="max-w-md min-w-0 align-top whitespace-normal">
                            <span className={cn("break-words", struck && "text-fg-tertiary line-through")}>{lineOf(item)}</span>
                            {chip && (
                              <span className="ml-2 inline-flex rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] whitespace-nowrap text-fg-secondary">
                                {chip}
                              </span>
                            )}
                          </TableCell>
                          <TableCell className="align-top text-xs whitespace-normal text-fg-secondary">{item.family.title}</TableCell>
                          <TableCell className="align-top text-xs text-fg-tertiary tabular-nums">{timeLabel(item.created_at)}</TableCell>
                        </TableRow>
                      </Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            </Surface>

            <p className="text-sm text-fg-tertiary tabular-nums">
              {(page - 1) * pageSize + 1}–{(page - 1) * pageSize + data.items.length} из {data.total}
            </p>

            <Pager
              page={page}
              total={data.total}
              pageSize={pageSize}
              onPageChange={setPage}
              onPageSizeChange={(n) => {
                setPageSize(n);
                setPage(1);
              }}
              showPageNumbers
            />
          </>
        )}
      </div>

      {/* Карточка держится в зоне видимости при прокрутке ленты. */}
      <div className="min-w-0 lg:sticky lg:top-[4.5rem] lg:max-h-[calc(100vh-5.5rem)] lg:overflow-y-auto">
        <JournalCard key={selectedId} id={selectedId} />
      </div>
    </div>
  );
}
