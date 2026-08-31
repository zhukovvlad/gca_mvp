import { Link, useParams, useSearchParams } from "react-router-dom";

import { Breadcrumbs } from "@/components/ui-domain/Breadcrumbs";
import { EmptyState } from "@/components/ui-domain/EmptyState";
import { KpiCard } from "@/components/ui-domain/KpiCard";
import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Button } from "@/components/ui/button";
import { apiErrorCode, useStageSummary } from "@/services/queries";
import type { StageSummary, StageSummaryErrorCode } from "@/types/domain";

import { ERROR_LABEL, NOMINAL_PRICE_CAPTION, TAX_LABEL } from "./cellCopy";
import { ChangeBadge } from "./SummaryCell";
import { StageSummaryTable } from "./StageSummaryTable";
import { StageSummaryTrack } from "./StageSummaryTrack";

/**
 * Страница свода по этапам одного участника (спека
 * 2026-08-27-stage-summary-design.md §2.2, §2.11, §2.14; задача 8 плана).
 *
 * Вход — только по выбору на решётке карточки тендера (`OfferGrid`): выбор
 * живёт в URL этой страницы (`?offers=…`), сама страница его не запоминает и
 * не подсказывает — без параметров она просит вернуться на решётку.
 */

/** Слово «этап»/«этапы» и глагол «исключён»/«исключены» — по числу исключённых. */
function excludedStagesCaption(
  stages: StageSummary["participant"]["stages"]
): string | null {
  const excludedNumbers = stages.filter((stage) => !stage.selected).map((stage) => stage.stage_no);
  if (excludedNumbers.length === 0) return null;
  const many = excludedNumbers.length > 1;
  const verb = many ? "исключены" : "исключён";
  const noun = many ? "этапы" : "этап";
  return `${verb} выбором: ${noun} ${excludedNumbers.join(", ")}`;
}

/**
 * Ставка НДС для KPI-карточки — читается из `display`/`columns`, а не
 * пересчитывается, и несёт ТУ ЖЕ причину, что налоговая ось под шапкой
 * (`taxAxisCaption`/`TAX_LABEL`): валовая карточка называет единую ставку
 * («20 % во всех выбранных» — формулировка макета гейта 1, `docs/superpowers/
 * specs/2026-08-27-stage-summary-mockup.html`, `.tep` строка «Ставка НДС»); на
 * нетто и на «нет известных ставок» карточка не заводит вторую, отдельно
 * придуманную формулировку рядом с осью — она повторяет ЕЁ причину («ставки
 * этапов расходятся» / «база НДС неизвестна у всех выбранных этапов», те же
 * фразы, что `TAX_LABEL.net`/`TAX_LABEL.none`), чтобы КПИ и ось не могли
 * разойтись в двух текстах об одном факте.
 */
function vatRateKpiText(summary: StageSummary): string {
  const { tax_basis, rates_by_column } = summary.display;
  if (tax_basis === "gross") {
    const known = summary.columns.find((column) => column.vat_rate_base !== null);
    if (!known) return "—";
    const allKnown = summary.columns.every((column) => column.vat_state === "known");
    // fix round 5: «во всех выбранных» верно только когда база известна у
    // КАЖДОЙ выбранной колонки — при частично неизвестной базе (валовая ось
    // законно остаётся, спека §2.8) карточка обязана называть подмножество,
    // а не все этапы, той же причиной, что несёт `TAX_LABEL.gross`.
    return allKnown ? `${known.vat_rate_base} % во всех выбранных` : `${known.vat_rate_base} % у этапов с известной базой`;
  }
  if (tax_basis === "net") {
    const rates = (rates_by_column ?? []).map((rate) => rate ?? "—").join(", ");
    return `нетто: ставки этапов расходятся (${rates})`;
  }
  return "база НДС неизвестна у всех выбранных этапов";
}

/** Подпись налоговой оси — та же, что видит трасса и таблица: одна причина
 *  одного факта, а не три текста, которые могут разойтись. */
function taxAxisCaption(summary: StageSummary): string {
  const { tax_basis, rates_by_column } = summary.display;
  if (tax_basis === "gross") {
    const known = summary.columns.find((column) => column.vat_rate_base !== null);
    // fix round 5: ось выбирается по множеству ИЗВЕСТНЫХ ставок среди
    // выбранных колонок (спека §2.8) — колонка с неизвестной базой в это
    // множество не входит, но ось соседей не меняет, поэтому валовая ось с
    // одной известной ставкой законно сочетается с одной или более
    // `unknown_vat_base`-колонками (фикстура `stageSummaryWithUnknownSecondColumn`).
    // `TAX_LABEL.gross` читает этот флаг и не утверждает «во всех этапах»,
    // когда это неверно.
    const allKnown = summary.columns.every((column) => column.vat_state === "known");
    return TAX_LABEL.gross(known?.vat_rate_base ?? "", allKnown);
  }
  if (tax_basis === "net") {
    return TAX_LABEL.net((rates_by_column ?? []).map((rate) => rate ?? "—").join(", "));
  }
  return TAX_LABEL.none;
}

export default function StageSummaryPage() {
  const { tenderId } = useParams();
  const id = tenderId ? Number(tenderId) : undefined;
  const [params] = useSearchParams();
  // getAll().filter(Number.isFinite) — не только `.map(Number)`: строка,
  // которая не парсится в число (`?offers=abc`), не должна тихо стать NaN и
  // дальше NaN-запросом.
  const offerIds = params.getAll("offers").map(Number).filter(Number.isFinite);

  const backLink = <Button render={<Link to={`/tenders/${id}`} />}>К решётке тендера</Button>;

  const summaryQ = useStageSummary(id, offerIds);

  /*
    `container-page py-8` стоит на КАЖДОЙ ветке возврата, а не только на
    успешной (найдено просмотром на стенде 28.08.2026: корень страницы был
    единственным корнем страницы в проекте без ограничителя ширины и тянулся во
    всё окно). Отказ и загрузка — такие же страницы, как готовый свод: ширина,
    поля и центрирование у них обязаны быть те же, иначе пустое состояние
    выглядит другой поверхностью. Утилита — общая (`index.css`,
    `max-width: 1400px`), та же, что у `TenderCardPage`, `ContractsPage` и
    шапки: собственных чисел ширины страница не заводит.
  */
  if (offerIds.length === 0) {
    return (
      <div className="container-page py-8">
        <EmptyState
          title="Свод не построен"
          description="Выберите предложения на решётке тендера"
          action={backLink}
        />
      </div>
    );
  }

  if (summaryQ.isPending) {
    return (
      <div className="container-page py-8">
        <Skeleton className="h-96" />
      </div>
    );
  }

  if (summaryQ.isError) {
    const code = apiErrorCode(summaryQ.error) as StageSummaryErrorCode | undefined;
    const description = code ? ERROR_LABEL[code] : "Не удалось построить свод";
    return (
      <div className="container-page py-8">
        <EmptyState title="Свод не построен" description={description} action={backLink} />
      </div>
    );
  }

  const summary = summaryQ.data;
  if (!summary) return null;

  const { tender, participant, kpi } = summary;
  const excludedCaption = excludedStagesCaption(participant.stages);

  return (
    <div className="container-page space-y-6 py-8">
      <Breadcrumbs
        items={[
          { label: "Тендеры", to: "/tenders" },
          { label: tender.title, to: `/tenders/${tender.id}` },
          { label: "Свод по этапам" },
        ]}
      />
      <PageHeader
        serif
        title={`Свод по этапам · ${participant.title}`}
        subtitle={`${tender.title} · ${tender.object_title} · этапов в своде ${kpi.stages_selected} из ${kpi.stages_loaded}`}
      />
      {excludedCaption && <p className="text-sm text-fg-secondary">{excludedCaption}</p>}

      <div data-testid="kpi" className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <KpiCard label="Этапов в своде" value={`${kpi.stages_selected} из ${kpi.stages_loaded}`} />
        <KpiCard label="Позиций в последнем" value={String(kpi.last_stage_positions)} />
        <KpiCard label="Статей с суммой" value={`${kpi.categories_with_amount} из ${kpi.categories_total}`} />
        <KpiCard label="Ставка НДС" value={vatRateKpiText(summary)} />
        <KpiCard label="Последний к первому" suffix={<ChangeBadge change={kpi.first_to_last} dashOnNone />} />
      </div>

      <p className="text-sm text-fg-secondary">
        {taxAxisCaption(summary)}. {NOMINAL_PRICE_CAPTION}.
      </p>

      <StageSummaryTrack summary={summary} />
      <StageSummaryTable summary={summary} tenderId={id!} offerIds={offerIds} />
    </div>
  );
}
