import { Link } from "react-router-dom";

import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { formatDate, formatDecimalMoney, pluralRu } from "@/lib/format";
import type {
  Dashboard,
  DashboardContractCoverage,
  DashboardContractReason,
  DashboardObjectCoverage,
} from "@/types/domain";

const EXCLUDED_REASON_LABEL: Record<DashboardContractReason, string> = {
  no_estimate: "без загруженной сметы",
  amendment: "с допсоглашением",
  no_rate: "без заявленной ставки НДС",
  incomplete: "с неполной стоимостью",
};

/** Доля полосы относительной величины: первая карточка всегда 100 %. */
function relativeWidth(amount: string | null, top: string | null): number {
  if (amount === null || top === null) return 0;
  const value = Number(amount);
  const max = Number(top);
  if (!Number.isFinite(value) || !Number.isFinite(max) || max <= 0) return 0;
  return Math.max(2, Math.round((value / max) * 100));
}

/**
 * Сноска исключённых (решение 9 макета).
 *
 * Сохраняет ровно два факта, которых больше нигде нет:
 *
 * 1. **Счётчики В СВОИХ ЕДИНИЦАХ.** «3 договора» и «1 объект» — разные
 *    сущности, они НЕ складываются. Одно число вместо двух сообщило бы
 *    величину, которой не существует.
 * 2. **РАЗНЫЕ МЕСТА ИСКЛЮЧЕНИЯ.** Договоры без сметы и без ставки выпали и из
 *    рейтинга, и из ИТОГО, а объект с несколькими договорами — ТОЛЬКО из
 *    рейтинга: деньги его договоров в итогах есть, они настоящие.
 *
 * Ссылка на второй таб — только `admin`: у читателя таба нет, и вести его туда
 * некуда.
 */
function ExcludedNote({
  contracts,
  objects,
  isAdmin,
}: {
  contracts: DashboardContractCoverage;
  objects: DashboardObjectCoverage;
  isAdmin: boolean;
}) {
  const excludedContracts = contracts.total - contracts.counted;
  const manyContracts = objects.reasons.many_contracts;
  const noContracts = objects.reasons.no_contracts;
  // `no_counted_contract` в сноске НЕ называется намеренно: такой объект уже
  // объяснён договорной половиной — его единственный договор назван там со
  // своей причиной, и второе упоминание описывало бы одну беду как две.
  // `no_contracts` называется обязательно: о нём не говорит ни одна строка
  // договорной половины, и без него объект пропадал бы молча.
  const excludedObjects = manyContracts + noContracts;
  const named = (Object.keys(EXCLUDED_REASON_LABEL) as DashboardContractReason[])
    .filter((reason) => contracts.reasons[reason] > 0)
    .map((reason) => `${contracts.reasons[reason]} ${EXCLUDED_REASON_LABEL[reason]}`);

  if (excludedContracts === 0 && excludedObjects === 0) return null;

  return (
    <div
      data-testid="excluded-note"
      className="border-t border-border-subtle px-4 py-3 text-xs text-fg-tertiary"
    >
      {excludedContracts > 0 && (
        <>
          В рейтинг не вошли{" "}
          <b className="font-medium text-fg-secondary">
            {excludedContracts} договор{pluralRu(excludedContracts)}
          </b>
          {named.length > 0 && ` — ${named.join(", ")}`} (их нет и в итогах)
        </>
      )}
      {excludedContracts > 0 && excludedObjects > 0 && " — и "}
      {manyContracts > 0 && (
        <>
          <b className="font-medium text-fg-secondary">
            {manyContracts} объект{pluralRu(manyContracts)}
          </b>{" "}
          с несколькими договорами ГП: действующий не определён, хотя сами договоры в итогах
          учтены.
        </>
      )}
      {manyContracts > 0 && noContracts > 0 && " "}
      {noContracts > 0 && (
        <>
          <b className="font-medium text-fg-secondary">
            {noContracts} объект{pluralRu(noContracts)}
          </b>{" "}
          без договоров: считать по ним нечего.
        </>
      )}
      {isAdmin && (
        <span> Причины — во вкладке «На что обратить внимание».</span>
      )}
    </div>
  );
}

export function ObjectRanking({
  data,
  isAdmin,
}: {
  data: Dashboard;
  isAdmin: boolean;
}) {
  const top = data.ranking[0]?.amount ?? null;

  return (
    <section className="mt-5 overflow-hidden rounded-lg border border-border-subtle bg-surface">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-border-subtle px-4 py-3">
        <h2 className="text-sm font-medium text-fg">Крупнейшие объекты</h2>
        <span className="text-xs text-fg-tertiary">
          первые 10 по сумме договоров · суммы с НДС
        </span>
      </div>

      <div className="divide-y divide-border-subtle">
        {data.ranking.map((card, index) => (
          <article
            key={card.object_id}
            data-testid={`rank-card-${card.object_id}`}
            className="relative px-4 py-3"
          >
            <div className="flex flex-wrap items-start gap-3">
              <span className="font-mono text-sm text-fg-tertiary">{index + 1}</span>
              <div className="min-w-0 flex-1">
                <h3 className="truncate text-sm font-medium text-fg">{card.title}</h3>
                <div className="mt-0.5 flex flex-wrap items-center gap-2 text-2xs text-fg-tertiary">
                  {card.rate_class_title && <span>{card.rate_class_title}</span>}
                  <span className="font-mono">
                    {card.area_total_sp === null
                      ? "площадь не заведена"
                      : `${formatDecimalMoney(card.area_total_sp, "", 0)} м²`}
                  </span>
                </div>
                <div className="mt-0.5 text-2xs text-fg-secondary">
                  {card.contract.contract_number}
                  {card.contract.signed_date && ` от ${formatDate(card.contract.signed_date)}`}
                  {card.contract.contractor_title && ` · ${card.contract.contractor_title}`}
                </div>
              </div>
              <div className="text-right">
                <MoneyCell value={card.amount} currency="₽" maxFractionDigits={0} />
                <div className="mt-0.5 text-2xs text-fg-tertiary">
                  {card.display_rate === "0" ? "без НДС" : `НДС ${card.display_rate} %`}
                  {" · "}
                  {card.per_sqm === null
                    ? "₽/м² не считается"
                    : `${formatDecimalMoney(card.per_sqm, "", 0)} ₽/м²`}
                </div>
              </div>
              {card.contract.id !== null && (
                <Link
                  to={`/contracts/${card.contract.id}/passport`}
                  className="self-center text-xs text-accent-text hover:underline"
                >
                  Паспорт →
                </Link>
              )}
            </div>
            {/* Полоса относительной величины: доля от суммы первого объекта. */}
            <div className="mt-2 h-1 rounded-full bg-surface-sunken">
              <i
                data-testid={`rank-bar-${card.object_id}`}
                className="block h-1 rounded-full bg-accent"
                style={{ width: `${relativeWidth(card.amount, top)}%` }}
              />
            </div>
          </article>
        ))}
      </div>

      <ExcludedNote
        contracts={data.money.coverage}
        objects={data.ranking_coverage}
        isAdmin={isAdmin}
      />
    </section>
  );
}
