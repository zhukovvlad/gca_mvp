import { Link, useParams } from "react-router-dom";
import { Printer } from "lucide-react";

import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { DeviationCell } from "@/components/ui-domain/DeviationCell";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { KpiCard } from "@/components/ui-domain/KpiCard";
import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDate, formatDecimalMoney } from "@/lib/format";
import { usePassport } from "@/services/queries";
import type { Passport, PassportKeyRate } from "@/types/domain";

/**
 * Паспорт объекта (AGENTS.md §7.4, DoD §10).
 *
 * Печатная форма: А4, `@media print`, PDF в MVP = Ctrl+P. Состав по §1 пункт 2 —
 * реквизиты договора (номер, подписант, дата, сумма, класс), ключевые расценки,
 * отклонения с подсветкой.
 *
 * **«Ключевые расценки» = топ-N позиций последней сметы по `total_cost_total`**,
 * где N хранится в БД (§7.4) и правится на экране настроек. Топ считает сервер —
 * экран не режет список сам, иначе N существовал бы в двух местах.
 *
 * **Одна страница А4 обеспечена по построению, а не обрезкой при печати** (решение
 * §6.5, `docs/phase6-analytics.md` §1.5): N ограничен сверху `CHECK`-ом в БД под эту
 * раскладку. Обрезка списка на печати означала бы, что форма молча теряет часть
 * того, что сама объявила своим составом.
 *
 * Что помечено `data-print`:
 * * `sheet` — сам документ (печатная типографика, лист целиком);
 * * `hide` — то, что документом не является: хлебные крошки, кнопка печати;
 * * `clamp` — наименование работы, зажатое по высоте: оно бывает на килобайты
 *   (§11 AGENTS.md), и одна такая ячейка распирает лист сильнее всех строк вместе.
 */
export default function PassportPage() {
  const { contractId } = useParams();
  const id = contractId ? Number(contractId) : undefined;

  const passportQ = usePassport(id);
  const passport = passportQ.data;

  if (passportQ.isPending) {
    return (
      <div className="container-page py-8">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="mt-4 h-64 w-full" />
      </div>
    );
  }

  if (passportQ.isError || !passport) {
    return (
      <div className="container-page py-8">
        <EmptyState
          title="Паспорт не построен"
          description="Договор не найден — возможно, он удалён."
          action={
            <Button variant="outline" render={<Link to="/contracts">К списку договоров</Link>} />
          }
        />
      </div>
    );
  }

  const { contract, estimate, totals, key_rates: keyRates } = passport;

  return (
    <div className="container-page py-8">
      <div data-print="hide">
        <Breadcrumbs
          items={[
            { label: "Договоры", to: "/contracts" },
            { label: contract.contract_number, to: `/contracts/${contract.id}` },
            { label: "Паспорт" },
          ]}
        />
        <div className="mt-4 flex items-center justify-between gap-4">
          <p className="text-sm text-fg-secondary">
            Печать — <kbd className="rounded border border-border-subtle px-1">Ctrl</kbd>+
            <kbd className="rounded border border-border-subtle px-1">P</kbd>. Форма рассчитана на
            один лист А4.
          </p>
          <Button variant="outline" onClick={() => window.print()}>
            <Printer className="size-4" /> Печать
          </Button>
        </div>
      </div>

      <article data-print="sheet" className="mt-6">
        <PassportHeader passport={passport} />

        {estimate === null ? (
          <p className="mt-6 rounded-md border border-border-subtle bg-surface-sunken px-4 py-3 text-sm text-fg-secondary">
            Смета к договору ещё не загружена, поэтому ключевых расценок нет. Реквизиты договора
            выше — актуальные.
          </p>
        ) : (
          <>
            <PassportSummary passport={passport} />
            <KeyRatesTable
              rates={keyRates}
              pendingReview={totals.positions_pending_review}
              nonWork={totals.positions_non_work}
            />
            {keyRates.length > 0 && (
              <p className="mt-2 text-xs text-fg-tertiary">
                Показаны {totals.positions_shown} из {totals.positions_priced} расценённых работ
                сметы — работы с наибольшей стоимостью. Отклонение считается от норматива класса
                «{contract.rate_class_title}» на дату сметы; у {totals.without_standard} работ
                норматива на эту дату нет, и они не сравниваются.
                {totals.positions_pending_review > 0 &&
                  ` Ещё ${totals.positions_pending_review} позиций ждут ручного матчинга и в расчёт не вошли.`}
              </p>
            )}
          </>
        )}

        <PassportSignatures />
      </article>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-2xs uppercase tracking-wider text-fg-tertiary">{label}</dt>
      <dd className="mt-0.5 text-sm text-fg">{children ?? "—"}</dd>
    </div>
  );
}

/** Шапка документа: реквизиты §1 пункт 2. */
function PassportHeader({ passport }: { passport: Passport }) {
  const { contract, estimate } = passport;
  return (
    <header>
      <div className="flex items-baseline justify-between gap-4 border-b border-border-default pb-2">
        <h1 className="font-serif text-xl text-fg">Паспорт объекта</h1>
        <span className="font-mono text-sm text-fg-secondary">
          договор {contract.contract_number}
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-8 gap-y-2 sm:grid-cols-3">
        <Field label="Объект">{contract.object_title}</Field>
        <Field label="Подрядчик">{contract.contractor_title}</Field>
        <Field label="Класс объекта">{contract.rate_class_title}</Field>
        <Field label="Подписант">{contract.signer}</Field>
        <Field label="Дата договора">{formatDate(contract.signed_date)}</Field>
        <Field label="Сумма договора">
          <MoneyCell value={contract.total_amount} />
        </Field>
        <Field label="Смета">
          {estimate === null
            ? "не загружена"
            : estimate.amendment_no === null
              ? "исходная"
              : `доп. соглашение № ${estimate.amendment_no}`}
        </Field>
        <Field label="Дата сметы">
          {estimate === null ? "—" : formatDate(estimate.data_prepared_on_date)}
        </Field>
        <Field label="Договор">{contract.title}</Field>
      </dl>
    </header>
  );
}

/**
 * Сводка: сколько работ, на какую сумму, сколько превышает норматив.
 *
 * **Два вида, экранный и печатный.** KPI-карточки с крупным кеглем и отбивкой `p-5`
 * съедали на листе около 25 мм — по замеру стенда именно они были главной причиной,
 * по которой паспорт не сходился на одну А4 (284 мм против 277 доступных при
 * значении N по умолчанию). На бумаге те же три числа умещаются в одну строку.
 *
 * Печатный вид — не урезанный: в нём те же данные, включая раздельные счётчики
 * «превышают» и «без норматива», которых требует §10.
 */
function PassportSummary({ passport }: { passport: Passport }) {
  const { totals } = passport;
  return (
    <>
      <div data-print="hide" className="mt-5 grid grid-cols-3 gap-3">
        <KpiCard label="Расценённых работ" value={String(totals.positions_priced)} />
        <KpiCard
          label="Стоимость расценённых работ"
          value={formatDecimalMoney(totals.priced_amount)}
        />
        <KpiCard
          label="Превышают норматив"
          value={String(totals.over_standard)}
          caption={
            /*
              «Нет норматива» показано ОТДЕЛЬНО от превышений — этого требует §10:
              слей их в один счётчик, и работа без норматива читалась бы как
              уложившаяся в него.
            */
            `из ${totals.with_standard} сравнимых; без норматива ${totals.without_standard}`
          }
        />
      </div>

      <dl
        data-print="only"
        className="flex flex-wrap gap-x-6 gap-y-1 border-y border-border-default py-1 text-xs"
      >
        <div className="flex gap-1">
          <dt className="text-fg-tertiary">Расценённых работ:</dt>
          <dd className="font-mono">{totals.positions_priced}</dd>
        </div>
        <div className="flex gap-1">
          <dt className="text-fg-tertiary">Стоимость:</dt>
          <dd className="font-mono">{formatDecimalMoney(totals.priced_amount)}</dd>
        </div>
        <div className="flex gap-1">
          <dt className="text-fg-tertiary">Превышают норматив:</dt>
          <dd className="font-mono">
            {totals.over_standard} из {totals.with_standard}
          </dd>
        </div>
        <div className="flex gap-1">
          <dt className="text-fg-tertiary">Без норматива:</dt>
          <dd className="font-mono">{totals.without_standard}</dd>
        </div>
      </dl>
    </>
  );
}

function KeyRatesTable({
  rates,
  pendingReview,
  nonWork,
}: {
  rates: PassportKeyRate[];
  pendingReview: number;
  nonWork: number;
}) {
  if (rates.length === 0) {
    /*
      Причины пустого топа две, и путать их нельзя. **Найдено прогоном стенда:**
      на живой базе все позиции реальной сметы имели цену, но каталог целиком
      состоял из TO_REVIEW, и паспорт сообщал «не заполнена цена за единицу» — то
      есть указывал на несуществующую проблему и отправлял искать её не там.
    */
    if (pendingReview > 0) {
      return (
        <div className="mt-5 rounded-md border border-warning-border bg-warning-soft px-4 py-3 text-sm">
          <p className="text-fg">
            Расценок пока нет, хотя цены в смете заполнены: {pendingReview} позиций ждут ручного
            матчинга. Пока работа не утверждена в каталоге, она не сравнивается с нормативами
            (AGENTS.md §4) и в паспорт не попадает.
          </p>
          <Button
            variant="outline"
            className="mt-3"
            data-print="hide"
            render={<Link to="/review">Разобрать очередь ручного матчинга</Link>}
          />
        </div>
      );
    }
    if (nonWork > 0) {
      /*
        Третья причина, вскрытая правкой по замечанию ревью. Пока HEADER/TRASH
        ошибочно считались «ожидающими матчинга», этот случай был не виден; как только
        счётчик стал верным, фолбэк начал утверждать «не заполнена цена» — неправду,
        потому что цена как раз заполнена. Такие строки уже разобраны (§5.4.3), и
        делать с ними ничего не надо: сказать об этом честнее, чем звать в очередь.
      */
      return (
        <p className="mt-5 text-sm text-fg-secondary">
          Расценок нет: все {nonWork} расценённых позиций сметы отнесены к строкам,
          помеченным как не-работа (раздел, заголовок лота или мусор). Такие строки с
          нормативами не сравниваются — это нормально и правки не требует.
        </p>
      );
    }
    return (
      <p className="mt-5 text-sm text-fg-secondary">
        В смете нет расценённых работ: у позиций не заполнена цена за единицу.
      </p>
    );
  }

  return (
    <div className="mt-5">
      <h2 className="text-sm font-medium text-fg">Ключевые расценки</h2>
      <Table className="mt-2">
        <TableHeader>
          <TableRow>
            <TableHead>Работа</TableHead>
            <TableHead className="w-16">Ед.</TableHead>
            <TableHead className="w-24 text-right">Объём</TableHead>
            <TableHead className="w-28 text-right">Ставка</TableHead>
            <TableHead className="w-28 text-right">Норматив</TableHead>
            <TableHead className="w-24 text-right">Откл.</TableHead>
            <TableHead className="w-32 text-right">Стоимость</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rates.map((rate) => (
            <TableRow key={rate.position_item_id} data-print="row">
              <TableCell>
                <span data-print="clamp" className="block" title={rate.job_title}>
                  {rate.job_title}
                </span>
              </TableCell>
              <TableCell className="text-fg-secondary">{rate.unit_code ?? "—"}</TableCell>
              <TableCell className="text-right">
                <MoneyCell value={rate.weight} currency="" />
              </TableCell>
              <TableCell className="text-right">
                <MoneyCell value={rate.unit_cost_total} currency="" />
              </TableCell>
              <TableCell className="text-right">
                {rate.standard_unit_rate === null ? (
                  <span className="text-fg-tertiary">—</span>
                ) : (
                  <MoneyCell value={rate.standard_unit_rate} currency="" />
                )}
              </TableCell>
              <TableCell className="text-right">
                <DeviationCell value={rate.deviation_pct} variant="compact" />
              </TableCell>
              <TableCell className="text-right">
                <MoneyCell value={rate.total_cost_total} currency="" />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/**
 * Блок подписей. Есть и на экране, и на печати: паспорт — форма, которую
 * подписывают, и место под подпись должно быть видно до того, как его напечатали.
 */
function PassportSignatures() {
  return (
    <footer className="mt-6 grid grid-cols-2 gap-8 text-xs text-fg-secondary">
      {["Составил", "Утвердил"].map((role) => (
        <div key={role}>
          <div className="mt-4 border-t border-border-default pt-1">
            {role} — должность, подпись, расшифровка
          </div>
        </div>
      ))}
    </footer>
  );
}
