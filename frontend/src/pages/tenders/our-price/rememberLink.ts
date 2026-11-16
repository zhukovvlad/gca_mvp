import { useState } from "react";

import { useFamilySchema } from "@/services/queries";
import type { FamilySchema, LinkKind } from "@/types/domain";

/** Выбор «без параметра» в списке: у видов с параметром стороны может не быть, но не обеих. */
export const NO_PARAM = 0;

/** Единицы вида с параметром: неупорядоченная пара (спека справочных аналогов §2.6). */
const PARAM_KIND_UNITS: Array<{ kind: Exclude<LinkKind, "constant">; units: [string, string]; root: string }> = [
  { kind: "thickness", units: ["м2", "м3"], root: "толщ" },
  { kind: "width", units: ["м", "м2"], root: "шир" },
  { kind: "density", units: ["м3", "т"], root: "плотн" },
];

/** Запись единицы без регистра, пробелов и верхних индексов: «М²» и «м2» — одна единица. */
function normalizeUnit(unit: string | null): string {
  return (unit ?? "").trim().toLowerCase().replace("²", "2").replace("³", "3");
}

/**
 * Виды связи, которые подходят паре единиц, первым — вид с параметром, если он есть; `constant` — для любых
 * двух заданных и различных единиц. Пустой список — связь пары не заводится.
 */
export function linkKindsFor(unitA: string | null, unitB: string | null): LinkKind[] {
  const a = normalizeUnit(unitA);
  const b = normalizeUnit(unitB);
  if (a === "" || b === "" || a === b) return [];
  const kinds: LinkKind[] = [];
  for (const { kind, units } of PARAM_KIND_UNITS) {
    if ((units[0] === a && units[1] === b) || (units[0] === b && units[1] === a)) kinds.push(kind);
  }
  kinds.push("constant");
  return kinds;
}

export interface ParamOption {
  id: number;
  name: string;
}

/** Параметры, годные в связь: точные значения действующей схемы; первым — «без параметра». */
export function exactParamOptions(schema: FamilySchema | undefined): ParamOption[] {
  const list = (schema?.parameters ?? []).filter((p) => p.value_kind === "exact").map((p) => ({ id: p.id, name: p.name }));
  return [{ id: NO_PARAM, name: "Без параметра" }, ...list];
}

/** Предзаполнение: первый параметр, чьё имя содержит корень вида; человек подтверждает или меняет. */
export function prefilledParam(options: ParamOption[], kind: LinkKind): number {
  const root = PARAM_KIND_UNITS.find((k) => k.kind === kind)?.root;
  if (root === undefined) return NO_PARAM;
  return options.find((o) => o.id !== NO_PARAM && o.name.toLowerCase().includes(root))?.id ?? NO_PARAM;
}

/**
 * Состояние флажка «Запомнить для семей»: включён ли он, вид и параметры сторон. Параметр, которого человек
 * не трогал, берётся предзаполнением по схеме, поэтому он появляется, когда схема загрузилась.
 */
export function useRememberLink(kinds: LinkKind[], familyA: number | null, familyB: number | null) {
  const [on, setOn] = useState(false);
  const [pickedKind, setPickedKind] = useState<LinkKind | null>(null);
  const [pickedA, setPickedA] = useState<number | null>(null);
  const [pickedB, setPickedB] = useState<number | null>(null);

  const kind = pickedKind !== null && kinds.includes(pickedKind) ? pickedKind : (kinds[0] ?? "constant");
  const isConstant = kind === "constant";
  const needSchemas = on && !isConstant;
  const schemaA = useFamilySchema(familyA ?? 0, { enabled: needSchemas && familyA !== null });
  const schemaB = useFamilySchema(familyB ?? 0, { enabled: needSchemas && familyB !== null });
  const optionsA = exactParamOptions(schemaA.data);
  const optionsB = exactParamOptions(schemaB.data);
  const paramA = pickedA ?? prefilledParam(optionsA, kind);
  const paramB = pickedB ?? prefilledParam(optionsB, kind);

  return {
    on,
    setOn,
    kind,
    isConstant,
    setKind: (next: LinkKind) => {
      setPickedKind(next);
      setPickedA(null);
      setPickedB(null);
    },
    optionsA,
    optionsB,
    paramA,
    paramB,
    setParamA: setPickedA,
    setParamB: setPickedB,
    /** Вид с параметром: нужен параметр хотя бы одной стороны. */
    paramsReady: paramA !== NO_PARAM || paramB !== NO_PARAM,
  };
}
