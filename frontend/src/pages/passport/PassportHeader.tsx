import type { ReactNode } from "react";
import { AlertTriangle, Printer } from "lucide-react";

import { MoneyCell } from "@/components/ui-domain/MoneyCell";
import { StatusPill } from "@/components/ui-domain/StatusPill";
import { Button } from "@/components/ui/button";
import { formatDate, formatDecimalMoney, pluralRu } from "@/lib/format";
import type { ID } from "@/types/common";
import type { ProjectPassport, ProjectPassportEstimate } from "@/types/domain";

import { VatRateDialog } from "./VatRateDialog";

/**
 * Титульная полоса, линейка показателей и оговорки коммерческих условий паспорта
 * проекта (Ф6 фазы 7, спека §2.9 пп. 1-4, 10, 11, 14; план, задача 7).
 *
 * Референс раскладки — `docs/superpowers/specs/2026-08-09-project-passport-mockup.html`,
 * одобренный на гейте 1. Файл там — не код для копирования: здесь примитивы
 * shadcn/ui и токены темы проекта, а не литеральные цвета макета.
 *
 * Строка сверки и баннер порчи данных живут здесь же, а не в отдельных
 * компонентах: обе читают только `passport.totals` и по месту в документе стоят
 * сразу после оговорок — там же, где в референсе.
 */

/**
 * Известно ли значение и равно ли оно строго нулю — БЕЗ приведения к `number`.
 * Деньги — `Decimal`-строки (AGENTS.md §3), и `Number("0.00") === 0` работал бы
 * здесь по случайности; на суммах, которые не влезают в double без потерь,
 * `Number()` уже стоил фазе 5 отдельного разбора. Проверка идёт знаками строки:
 * значение — ноль тогда и только тогда, когда все цифры в нём — нули.
 */
function isZeroDecimal(value: string): boolean {
  return /^-?0+(\.0+)?$/.test(value.trim());
}

/**
 * Заявленный процент как есть, строкой, без `Number()` — та же причина, что у
 * `isZeroDecimal`: `vat_rate`, `advance_pct` и однокашники входят в список полей
 * §2.6, которые нельзя приводить к `number` ни на одном шаге. Хвостовые нули
 * после точки отбрасываются («20.00» → «20»), а не показываются — это не
 * округление и ничего не теряет.
 */
function formatPercentDecimal(value: string): string {
  const trimmed = value.includes(".")
    ? value.replace(/0+$/, "").replace(/\.$/, "")
    : value;
  return `${trimmed} %`;
}

/**
 * Текст о ставке НДС сметы (спека пересчёта §2.7, задача 10).
 *
 * Три состояния складываются из ПОЛЕЙ сметы (`vat_rate`,
 * `vat_rate_base_override`, `vat_rate_target`) — они объясняют ПРОИСХОЖДЕНИЕ
 * ставки: файл, назначенная вручную база, заданная цель. Это НЕ то же самое,
 * что `vat_display_rate` — ставка, в которой ФАКТИЧЕСКИ выражены суммы паспорта
 * (`totals.amount`, `per_sqm`, статьи). Приоритет цель→база→заявленная уже
 * реализован на сервере (`money.vat.effective_display_rate`,
 * `crud/project_passport.py:1321`), и здесь он НЕ повторяется арифметикой —
 * `vat_display_rate` только СРАВНИВАЕТСЯ с базой/заявленной, чтобы решить,
 * упоминать ли фактическую ставку показа отдельно. Вторая копия самого
 * правила приоритета разъехалась бы с сервером молча при первой же его правке
 * (задача 10, приложение оркестратора п. 4).
 */
function vatSummary(estimate: ProjectPassportEstimate): string {
  const declared = estimate.vat_rate;
  const override = estimate.vat_rate_base_override;
  const target = estimate.vat_rate_target;
  const base = override ?? declared;

  if (base === null) return "ставка НДС не заявлена в файле";

  // Ни база, ни цель не назначены вручную — показ совпадает с заявленной по
  // построению, и `vat_display_rate` можно не читать вовсе.
  if (override === null && target === null) {
    return `ставка НДС ${formatPercentDecimal(base)}`;
  }

  const display = estimate.vat_display_rate;

  if (override !== null) {
    const suffix = declared === null ? "" : ` (файл заявил ${formatPercentDecimal(declared)})`;
    const head = `база НДС ${formatPercentDecimal(override)} назначена вручную${suffix}`;
    const displayDiffers = display !== null && display !== override;
    return displayDiffers ? `${head}; показано в ставке ${formatPercentDecimal(display)}` : head;
  }

  // override === null, target !== null.
  const displayDiffers = display !== null && display !== base;
  return displayDiffers
    ? `показано в ставке ${formatPercentDecimal(display)}, пересчитано с ${formatPercentDecimal(base)}`
    : `ставка НДС ${formatPercentDecimal(base)}`;
}

/**
 * Печатная сноска о поправке ставки НДС (спека пересчёта §2.7, задача 10).
 *
 * `null` — ставки не правились вовсе (ни база, ни цель), сноски нет.
 * Присутствует БЕЗУСЛОВНО, когда поправка действует — тот же приём, что у
 * печатной сноски о ручном разносе (`AGENTS.md` §7.4): банк не должен принять
 * пересчитанные числа за содержимое файла молча.
 */
function vatFootnote(estimate: ProjectPassportEstimate): string | null {
  const override = estimate.vat_rate_base_override;
  const target = estimate.vat_rate_target;
  if (override === null && target === null) return null;

  const declared = estimate.vat_rate;
  const display = estimate.vat_display_rate;

  if (display !== null && declared !== null && display !== declared) {
    return (
      `Суммы паспорта показаны в ставке НДС ${formatPercentDecimal(display)}, ` +
      `пересчитано из заявленной в файле ${formatPercentDecimal(declared)} — ` +
      "ставка НДС этой сметы скорректирована вручную."
    );
  }
  return "Ставка НДС этой сметы скорректирована вручную.";
}

function Metric({
  label,
  value,
  caption,
  testId,
}: {
  label: string;
  value: ReactNode;
  caption: ReactNode;
  /** Якорь для утверждений о САМОМ значении показателя. Без него проверка шла бы
   *  по тексту всей линейки `passport-metrics`, где рядом стоят другие числа. */
  testId?: string;
}) {
  return (
    <div className="px-6 py-3">
      <dt className="text-2xs tracking-wider text-fg-tertiary uppercase">{label}</dt>
      <dd data-testid={testId} className="mt-1 font-mono text-lg font-semibold tabular-nums text-fg">
        {value}
      </dd>
      <p className="mt-0.5 text-xs text-fg-secondary">{caption}</p>
    </div>
  );
}

/**
 * Одна оговорка коммерческого условия: подпись + свободный текст, зажатый по
 * высоте. **`data-print="clamp"` есть, а класса `block` рядом с ним НЕТ** —
 * `block` отменяет `display: -webkit-box` и снимает зажим вовсе (AGENTS.md §11);
 * ловушка была живой у экрана фазы 6 (`PassportPage.tsx`, удалён этой же
 * веткой): там `data-print="clamp"` соседствовал с `className="block"`.
 */
function Term({ label, note }: { label: string; note: string | null }) {
  return (
    <div className="flex min-w-0 gap-2 text-xs text-fg-secondary">
      <span className="shrink-0 font-mono font-semibold text-fg">{label}</span>
      {note ? (
        <span data-print="clamp" className="line-clamp-2 min-w-0">
          {note}
        </span>
      ) : (
        <span className="text-fg-tertiary">—</span>
      )}
    </div>
  );
}

export function PassportHeader({
  passport,
  contractId,
}: {
  passport: ProjectPassport;
  /**
   * Id договора из МАРШРУТА, а не `passport.contract.id` из ответа — тот же
   * приём, что у `CategoryTable` (task-8-controller-notes: два источника id
   * молча расходятся в инвалидации мутации).
   */
  contractId: ID;
}) {
  const { contract, object, estimate, totals } = passport;

  const noTep = object.area_total_sp === null;
  const showMultiBadge = contract.object_contracts_count > 1;

  // Правило 8 (§2.9): молчание — нормальный вид, сверка видна только при ДВУХ
  // известных операндах и настоящем расхождении.
  const showReconcile =
    totals.delta_to_file_total !== null && !isZeroDecimal(totals.delta_to_file_total);
  const showCorruption = totals.positions_rows_not_finite > 0;
  // Сверка нетто (спека пересчёта §2.10) — видна ТОЛЬКО при mismatch, как и
  // существующая сверка `delta_to_file_total`: `ok`/`not_applicable`/
  // `unknown_base` на экран не выводятся вовсе.
  const netMismatch = totals.net_reconciliation.status === "mismatch";

  return (
    <header className="border border-border-subtle bg-surface">
      {/* титульная полоса */}
      <div className="flex flex-wrap items-start justify-between gap-4 border-b-2 border-fg px-6 py-4">
        <div className="min-w-0">
          <p className="text-2xs tracking-widest text-fg-tertiary uppercase">
            Паспорт проекта · свод по статьям классификатора
          </p>
          <h1 className="mt-1 font-serif text-2xl leading-tight text-fg">
            {contract.object_title}
            <span className="mx-2 text-fg-tertiary">·</span>
            <span className="font-mono text-lg">{contract.contract_number}</span>
          </h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-fg-secondary">
            <span>{contract.contractor_title}</span>
            <span className="text-fg-tertiary">·</span>
            <span>подписан {formatDate(contract.signed_date)}</span>
            <span className="text-fg-tertiary">·</span>
            <span>класс {contract.rate_class_title}</span>
            <span className="text-fg-tertiary">·</span>
            <span>подписант {contract.signer ?? "—"}</span>
            {showMultiBadge && (
              <StatusPill
                tone="info"
                label={`у объекта ${contract.object_contracts_count} договор${pluralRu(
                  contract.object_contracts_count
                )}`}
              />
            )}
          </p>
        </div>
        <div data-print="hide" className="shrink-0">
          <Button variant="outline" onClick={() => window.print()}>
            <Printer className="size-4" /> Печать
          </Button>
        </div>
      </div>

      {/* линейка показателей (§2.9 п. 3) */}
      <dl
        data-testid="passport-metrics"
        className="grid grid-cols-1 divide-y divide-border-subtle border-b border-border-subtle sm:grid-cols-2 sm:divide-x sm:divide-y-0 lg:grid-cols-4"
      >
        <Metric
          label="Площадь общая"
          value={
            noTep ? (
              <StatusPill tone="warning" label="ТЭП не заведены" />
            ) : (
              <>
                <MoneyCell value={object.area_total_sp} currency="" /> м²
              </>
            )
          }
          caption={
            noTep
              ? "площади вводятся в карточке объекта"
              : `подземная ${formatDecimalMoney(object.area_underground_sp, "")} · надземная ${formatDecimalMoney(
                  object.area_aboveground_sp,
                  ""
                )}`
          }
        />

        <Metric
          label="Стоимость по смете, с НДС"
          value={<MoneyCell value={totals.amount} />}
          caption={
            /*
              Без сметы подпись про файл ЛОЖНА: «не заявлена в файле» и
              «исходная смета договора» утверждают факты о документе, которого
              ещё нет. Различение обязательно — «ставка не заявлена в
              загруженном файле» и «файла нет вовсе» это разные состояния, и
              смешивать их значит повторять ту же ошибку, за которую фаза
              платила на «нет норматива» против «0 %».
            */
            estimate === null ? (
              "смета не загружена"
            ) : (
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span data-testid="vat-summary">
                  {vatSummary(estimate)} · исходная смета договора
                </span>
                <span data-print="hide">
                  <VatRateDialog estimate={estimate} contractId={contractId} />
                </span>
              </span>
            )
          }
        />

        <Metric
          label="Стоимость за м²"
          testId="metric-per-sqm"
          value={
            noTep ? (
              <span className="font-sans text-sm font-semibold text-warning-text">нет ТЭП</span>
            ) : (
              // Вычисленная величина — округляется на слое показа, точное
              // значение уходит в `title` (та же причина, что в `CategoryTable`).
              <MoneyCell value={totals.per_sqm} maxFractionDigits={2} />
            )
          }
          caption={noTep ? "удельные показатели не считаются" : "по общей площади"}
        />

        <Metric
          label="Аванс · гарантия · удержание"
          value={
            <>
              {contract.advance_pct === null ? "—" : formatPercentDecimal(contract.advance_pct)}
              {" / "}
              {contract.bank_guarantee_pct === null
                ? "—"
                : formatPercentDecimal(contract.bank_guarantee_pct)}
              {" / "}
              {contract.retention_pct === null ? "—" : formatPercentDecimal(contract.retention_pct)}
            </>
          }
          caption="условия договора"
        />
      </dl>

      {/* оговорки коммерческих условий — свободный текст, зажат по высоте */}
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 bg-surface-sunken px-6 py-3 sm:grid-cols-3">
        <Term label="Аванс" note={contract.advance_note} />
        <Term label="Гарантия" note={contract.bank_guarantee_note} />
        <Term label="Удержание" note={contract.retention_note} />
      </dl>

      {/* строка сверки — молчит, пока сходится (§2.9 п. 8) */}
      {showReconcile && (
        <p className="flex items-start gap-2 border-b border-warning-border bg-warning-soft px-6 py-2 text-sm text-warning-text">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>
            Сумма по статьям не сходится с ИТОГО сметы: расхождение{" "}
            <span className="font-mono tabular-nums">
              {formatDecimalMoney(totals.delta_to_file_total)}
            </span>
            . Числа ниже показаны как есть; расхождение означает потерянные или задвоенные строки
            и требует разбора.
          </span>
        </p>
      )}

      {/* баннер порчи данных — только при ненулевом счётчике (§2.9 п. 9) */}
      {showCorruption && (
        <p className="flex items-start gap-2 border-b border-warning-border bg-warning-soft px-6 py-2 text-sm text-warning-text">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>
            Итог неполон: {totals.positions_rows_not_finite} позиций сметы содержат нечисловые
            суммы (не поддающиеся сложению) и не вошли в общую сумму. Это не заменяет подпись
            неполноты у конкретной статьи — она называет, в какой статье, а баннер — сколько всего.
          </span>
        </p>
      )}

      {/*
        Сверка выведенного нетто с файловым (спека пересчёта §2.10) — видна
        ТОЛЬКО при mismatch. Печатается число предложений-нарушителей, а не их
        перечень идентификаторов: перечень на бумаге бессмыслен, а аналитик без
        числа увидел бы расхождение и не нашёл бы источник.
      */}
      {netMismatch && (
        <p
          data-testid="net-reconciliation"
          className="flex items-start gap-2 border-b border-warning-border bg-warning-soft px-6 py-2 text-sm text-warning-text"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>
            Выведенное нетто расходится с заявленным в файле; предложений:{" "}
            {totals.net_reconciliation.mismatched_proposal_ids.length}.
          </span>
        </p>
      )}

      {/*
        Печатная сноска о поправке ставки НДС (спека пересчёта §2.7) — лежит
        под остальным содержимым и раскрытию дерева не подчинена (в этом
        документе раскрытия и вовсе нет): `data-print="hide"` на ней стоять не
        имеет права, тот же приём, что у сноски о ручном разносе (`AGENTS.md`
        §7.4) — банк не должен принять пересчитанные числа за содержимое
        файла молча.
      */}
      {estimate !== null && vatFootnote(estimate) !== null && (
        <p data-testid="vat-print-note" className="px-6 py-2 text-xs text-fg-secondary">
          {vatFootnote(estimate)}
        </p>
      )}
    </header>
  );
}
