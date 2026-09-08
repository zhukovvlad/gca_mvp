import { useState } from "react";
import { Plus, TrendingUp } from "lucide-react";

import { InflationSeriesDialog } from "@/components/inflation/InflationSeriesDialog";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Surface } from "@/components/ui-domain/Surface";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useInflationSeries, useUpdateInflationSeries } from "@/services/queries";
import type { InflationSeries } from "@/types/domain";

/**
 * Ряды индексов инфляции — третья вкладка экрана «Нормативы» (`AGENTS.md` §7.3
 * в редакции v6.10; спека 2026-08-18 §2.10).
 *
 * Здесь, а не отдельным экраном: §7.3 отдаёт этому экрану «классы, ставки,
 * периоды, форма переутверждения с коэффициентом инфляции», то есть индексы уже
 * его тема. Право — `admin`, как у всего, что меняет расчёт для всех (§3).
 *
 * **Архивные ряды показываются вместе с активными**, потому что архивация обратима
 * и вернуть ряд можно только со списка, где он виден. В ОПЦИИ селектора на
 * `/compare` архивные при этом не попадают: список там запрашивается тоже с
 * архивными — иначе окно правки не получило бы объект ряда, выбранного по прямой
 * ссылке, — а «не предлагаются для нового выбора» (§2.10) делает сам селектор,
 * отбрасывая неактивные из опций.
 */
export function InflationSeriesTab() {
  // `include_archived` здесь ВКЛЮЧЁН: иначе убранный в архив ряд исчезал бы из
  // интерфейса совсем, и архивация оказалась бы необратимой на практике при
  // обратимости в контракте.
  const series = useInflationSeries(true);
  const update = useUpdateInflationSeries();

  const [editing, setEditing] = useState<InflationSeries | null>(null);
  const [creating, setCreating] = useState(false);

  function setActive(row: InflationSeries, isActive: boolean) {
    // В теле ТОЛЬКО `is_active` и больше ничего: разморозка, совмещённая с
    // правкой, отвергается сервером `409` (§2.10), и «вернуть» с «изменить» —
    // осознанно два шага, каждый со своим смыслом.
    update.mutate({ id: row.id, input: { is_active: isActive } });
  }

  if (series.isLoading) return <Skeleton className="h-40" />;

  /*
    Упавший запрос списка — НЕ пустой справочник, и это важнее, чем кажется. Пока
    ветки не было, `series.data ?? []` уводил отказ в `rows.length === 0`, то есть
    экран УТВЕРЖДАЛ «Рядов индексов пока нет» и кнопкой приглашал завести первый —
    при живом справочнике, который просто не доехал. Молчание было бы полбеды;
    здесь поверхность говорила неправду и звала на действие, которое кончилось бы
    дубликатом либо `409` по занятому названию.

    Кнопки «Создать ряд» в этой ветке нет намеренно: единственное действие, которое
    тут опасно, — именно создание.
  */
  if (series.isError)
    return (
      <EmptyState
        icon={<TrendingUp className="size-5" />}
        title="Не удалось загрузить ряды индексов"
        description="Обновите страницу. Создавать ряд, не увидев списка, не стоит: он может уже существовать — тогда название окажется занято."
      />
    );

  const rows = series.data ?? [];

  return (
    <div className="grid gap-4">
      <div className="flex justify-end">
        <Button onClick={() => setCreating(true)}>
          <Plus className="size-4" />
          Создать ряд
        </Button>
      </div>

      {rows.length === 0 ? (
        /*
          Пустой справочник — это состояние СРАЗУ ПОСЛЕ МИГРАЦИИ (§2.6), а не
          редкий край: безымянные «официальный» и «неофициальный» без методики не
          заводятся, поэтому первый ряд создаёт человек. Путь обязан быть
          проходимым отсюда.
        */
        <EmptyState
          icon={<TrendingUp className="size-5" />}
          title="Рядов индексов пока нет"
          description="Ряд — это именованный показатель, например «Росстат, ИПЦ, декабрь к декабрю». Без него приведение к ценовому уровню на странице сравнения недоступно."
          action={<Button onClick={() => setCreating(true)}>Создать ряд</Button>}
        />
      ) : (
        <Surface className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Название</TableHead>
                <TableHead>Примечание</TableHead>
                <TableHead>Охват годов</TableHead>
                <TableHead>Состояние</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-medium">{row.name}</TableCell>
                  <TableCell className="text-fg-secondary">{row.note ?? "—"}</TableCell>
                  <TableCell>{coverage(row)}</TableCell>
                  <TableCell>
                    {row.is_active ? (
                      <Badge variant="outline">активный</Badge>
                    ) : (
                      <Badge variant="secondary">в архиве</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button
                        variant="outline"
                        disabled={!row.is_active}
                        title={
                          row.is_active
                            ? undefined
                            : "Ряд в архиве: сначала верните его в активные"
                        }
                        onClick={() => setEditing(row)}
                      >
                        Изменить
                      </Button>
                      {row.is_active ? (
                        <Button variant="ghost" onClick={() => setActive(row, false)}>
                          В архив
                        </Button>
                      ) : (
                        <Button variant="ghost" onClick={() => setActive(row, true)}>
                          Вернуть в активные
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Surface>
      )}

      {/*
        ОДНО окно на оба режима — правку и создание. Второй компонент под создание
        разошёлся бы с первым: и подпись про общий ряд, и расшифровка коэффициента
        нужны там ровно так же (§2.12, DoD 34).
      */}
      <InflationSeriesDialog
        open={creating || editing !== null}
        // Режим ЗАЯВЛЕН: здесь объект правки берётся из той же строки таблицы, по
        // которой человек нажал, поэтому «не нашли» тут невозможно по построению —
        // но заявить режим всё равно обязан вызывающий, иначе контракт окна
        // различал бы создание и «данные не пришли» по значению `null`.
        target={editing !== null ? { mode: "edit", series: editing } : { mode: "create" }}
        onOpenChange={(open) => {
          if (!open) {
            setCreating(false);
            setEditing(null);
          }
        }}
      />
    </div>
  );
}

function coverage(row: InflationSeries): string {
  if (row.year_from === null || row.year_to === null) return "годы не заданы";
  return row.year_from === row.year_to
    ? String(row.year_from)
    : `${row.year_from}–${row.year_to}`;
}
