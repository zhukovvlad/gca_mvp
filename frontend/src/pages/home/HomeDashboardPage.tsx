import { Info } from "lucide-react";

import { PageHeader } from "@/components/ui-domain/PageHeader";
import { Skeleton } from "@/components/ui-domain/Skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useCurrentUser } from "@/hooks/useAuth";
import { formatDecimalMoney, formatNumber, pluralRu } from "@/lib/format";
import { useDashboard, useDashboardAttention } from "@/services/queries";
import { AttentionPanel } from "./AttentionPanel";
import { ObjectRanking } from "./ObjectRanking";
import { PerSqmChart } from "./PerSqmChart";
import type {
  Dashboard,
  DashboardContractCoverage,
  DashboardContractReason,
  DashboardCoverage,
} from "@/types/domain";

/**
 * ПОДПИСЬ ВЕЛИЧИНЫ ЖИВЁТ НА ПОВЕРХНОСТИ, а не в тултипе.
 *
 * Главная — единственная поверхность продукта, складывающая суммы РАЗНЫХ
 * договоров, измеренные каждая в своей действующей ставке (осознанная ревизия
 * `AGENTS.md` §10, решение 3 макета). §10 требует, чтобы читатель видел, в чём
 * измерены числа, которые он складывает, — поэтому строка постоянная и
 * видимая. Тултип у плитки ИТОГО остался, но объясняет РАСЧЁТ, а не заменяет
 * объявление величины: промежуточное решение «подпись только в тултипе» снял
 * второй круг внешнего ревью на гейте 1.
 */
const GROSS_NOTICE =
  "Все суммы и удельные показатели — с НДС, в действующей ставке договора";

const CONTRACT_REASON_LABEL: Record<DashboardContractReason, string> = {
  no_estimate: "без сметы",
  amendment: "с допсоглашением",
  no_rate: "без ставки",
  incomplete: "с неполной стоимостью",
};

/**
 * «учтено 3 договора из 6: 2 без сметы, 1 без ставки».
 *
 * Причины с нулём не называются: они описывали бы не эту базу, а список
 * возможных бед вообще, и разбивка перестала бы читаться. Прежняя формулировка
 * гейта 1 («не вошло: 2 сметы») занижала исключённое — счётчик обязан идти в
 * ДОГОВОРАХ, потому что и охват в договорах.
 */
function describeContractCoverage(coverage: DashboardContractCoverage): string {
  const named = (Object.keys(CONTRACT_REASON_LABEL) as DashboardContractReason[])
    .filter((reason) => coverage.reasons[reason] > 0)
    .map((reason) => `${coverage.reasons[reason]} ${CONTRACT_REASON_LABEL[reason]}`);
  const head = `учтено ${coverage.counted} договор${pluralRu(coverage.counted)} из ${coverage.total}`;
  return named.length > 0 ? `${head}: ${named.join(", ")}` : head;
}

/** «2/4» — охват одного слагаемого, в своих единицах. */
function coverageRatio(coverage: DashboardCoverage): string {
  return `${coverage.counted}/${coverage.total}`;
}

function areaValue(value: string | null): string {
  return value === null ? "—" : formatDecimalMoney(value, "", 0);
}

interface TileProps {
  label: string;
  children: React.ReactNode;
  className?: string;
  testId?: string;
}

function Tile({ label, children, className, testId }: TileProps) {
  return (
    <div
      data-testid={testId}
      className={`rounded-lg border border-border-subtle bg-surface p-4 ${className ?? ""}`}
    >
      <div className="text-2xs uppercase tracking-wider text-fg-tertiary">{label}</div>
      {children}
    </div>
  );
}

function MoneyTile({ data }: { data: Dashboard }) {
  return (
    <div className="col-span-2 rounded-lg border border-accent-border bg-accent-soft p-4">
      <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wider text-accent-text">
        Итого по базе
        <Tooltip>
          <TooltipTrigger aria-label="Как считается">
            <Info size={13} />
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">
            Каждый договор взят с НДС в действующей ставке: из файла сметы, а если ставку
            пересчитали вручную — в назначенной. Ставки договоров различаются, поэтому
            итог складывает суммы, измеренные в разных ставках.
          </TooltipContent>
        </Tooltip>
      </div>
      <div className="mt-2 font-mono text-2xl text-accent-text">
        {formatDecimalMoney(data.money.amount, "₽", 0)}
      </div>
      <div className="mt-2 text-xs text-warning">
        {describeContractCoverage(data.money.coverage)}
      </div>
    </div>
  );
}

function AreaTile({ areas }: { areas: Dashboard["areas"] }) {
  const parts: { key: string; label: string; block: Dashboard["areas"]["total"] }[] = [
    { key: "aboveground", label: "Надземная", block: areas.aboveground },
    { key: "underground", label: "Подземная", block: areas.underground },
    { key: "useful", label: "Полезная", block: areas.useful },
  ];

  return (
    <Tile label="Площадь объектов" className="col-span-2">
      <div className="mt-2 flex flex-wrap items-start gap-6">
        <div className="min-w-0">
          <div className="font-mono text-2xl text-fg">
            {areaValue(areas.total.value)}
            <span className="ml-1.5 text-sm font-normal text-fg-secondary">м²</span>
          </div>
          <div className="mt-1 text-xs text-fg-secondary">
            по {areas.total.coverage.counted} объект
            {pluralRu(areas.total.coverage.counted) === "" ? "у" : "ам"} из{" "}
            {areas.total.coverage.total}
          </div>
        </div>
        <div className="ml-auto flex min-w-[11rem] flex-col gap-1">
          {parts.map((part) => (
            <div
              key={part.key}
              data-testid={`area-${part.key}`}
              className="flex items-baseline gap-3"
            >
              <span className="text-xs text-fg-tertiary">{part.label}</span>
              <span className="ml-auto font-mono text-sm text-fg">
                {areaValue(part.block.value)}
              </span>
              {/* Охват СВОЕГО слагаемого: у полезной он свой, отличный от пары. */}
              <span className="w-9 text-right font-mono text-2xs text-fg-muted">
                {coverageRatio(part.block.coverage)}
              </span>
            </div>
          ))}
        </div>
      </div>
      <div
        data-testid="area-extremes"
        className="mt-3 flex flex-col gap-0.5 border-t border-border-subtle pt-2 text-2xs text-fg-tertiary"
      >
        <span>
          Наибольший — <b className="font-medium text-fg-secondary">{areas.largest?.title ?? "—"}</b>
          {areas.largest && ` · ${areaValue(areas.largest.area_total_sp)} м²`}
        </span>
        <span>
          Наименьший —{" "}
          <b className="font-medium text-fg-secondary">{areas.smallest?.title ?? "—"}</b>
          {areas.smallest && ` · ${areaValue(areas.smallest.area_total_sp)} м²`}
        </span>
      </div>
    </Tile>
  );
}

function CountersTile({ counters }: { counters: Dashboard["counters"] }) {
  return (
    <div data-testid="base-counters" className="flex flex-col gap-3">
      {/*
        Объекты и договоры — РАЗНЫЕ сущности и стоят порознь (решение 7 макета:
        парой в одной ячейке, но двумя числами). Сложить их в одно число значило
        бы сообщить величину, которой не существует.
      */}
      <Tile label="Объектов" testId="counter-objects" className="py-3">
        <div className="mt-1 font-mono text-lg text-fg">{formatNumber(counters.objects)}</div>
        <div className="text-2xs text-fg-secondary">в {counters.classes} классах</div>
      </Tile>
      <Tile label="Договоров" testId="counter-contracts" className="py-3">
        <div className="mt-1 font-mono text-lg text-fg">{formatNumber(counters.contracts)}</div>
        <div className="text-2xs text-fg-secondary">
          со сметой — {counters.contracts_with_estimate}
        </div>
      </Tile>
    </div>
  );
}

function PerSqmTile({ perSqm }: { perSqm: Dashboard["per_sqm"] }) {
  const suffix = `из ${perSqm.coverage.counted}`;
  return (
    <div className="flex flex-col gap-3">
      {(["max", "min"] as const).map((side) => {
        const point = perSqm[side];
        return (
          <Tile
            key={side}
            label={`${side === "max" ? "Максимум" : "Минимум"} ₽/м² · ${suffix}`}
            testId={`per-sqm-${side}`}
            className="py-3"
          >
            <div className="mt-1 font-mono text-lg text-fg">
              {point ? formatDecimalMoney(point.per_sqm, "", 0) : "—"}
              <span className="ml-1 text-xs font-normal text-fg-secondary">₽/м²</span>
            </div>
            <div className="truncate text-2xs text-fg-secondary">{point?.title ?? "—"}</div>
          </Tile>
        );
      })}
    </div>
  );
}

function MainTab({ data, isAdmin }: { data: Dashboard; isAdmin: boolean }) {
  return (
    <>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-6">
        <MoneyTile data={data} />
        <AreaTile areas={data.areas} />
        <CountersTile counters={data.counters} />
        <PerSqmTile perSqm={data.per_sqm} />
      </div>
      <PerSqmChart chart={data.chart} />
      <ObjectRanking data={data} isAdmin={isAdmin} />
    </>
  );
}

/**
 * Стартовый дашборд (маршрут `/`). Два таба; второй — только `admin`.
 *
 * У `member` полоса табов не рисуется ВОВСЕ, а не рисуется заблокированной:
 * полоса из одной вкладки была бы обещанием второй, которой у читателя нет
 * (решение 1 макета).
 */
export default function HomeDashboardPage() {
  const { data: user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  const { data, isPending, isError } = useDashboard();
  // Хук УСЛОВНЫЙ: `member` не должен слать запрос, который сервер обязан
  // отклонить (§2.8). Стережёт это счётчик вызовов в MSW, а не отсутствие
  // данных на экране — их у `member` нет в обеих ветвях.
  const attention = useDashboardAttention(isAdmin);

  return (
    <div className="container-page py-8">
      <PageHeader
        serif
        title="База расценок генподряда"
        subtitle="Договоры ГП 2024–2027, объекты и загруженные сметы"
      />
      <p className="mt-2 text-xs text-fg-tertiary">{GROSS_NOTICE}</p>

      {isError && (
        <p className="mt-8 text-sm text-warning">
          Не удалось загрузить сводку базы. Обновите страницу.
        </p>
      )}

      {isPending && !isError && (
        <div className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-6">
          {Array.from({ length: 4 }, (_, index) => (
            <Skeleton key={index} className="col-span-2 h-28" />
          ))}
        </div>
      )}

      {data &&
        (isAdmin ? (
          <Tabs defaultValue="main" className="mt-6">
            <TabsList>
              <TabsTrigger value="main">Основной дашборд</TabsTrigger>
              <TabsTrigger value="attention">На что обратить внимание</TabsTrigger>
            </TabsList>
            <TabsContent value="main" className="mt-4">
              <MainTab data={data} isAdmin />
            </TabsContent>
            <TabsContent value="attention" className="mt-4">
              {attention.data && <AttentionPanel data={attention.data} />}
            </TabsContent>
          </Tabs>
        ) : (
          <div className="mt-6">
            <MainTab data={data} isAdmin={false} />
          </div>
        ))}
    </div>
  );
}
