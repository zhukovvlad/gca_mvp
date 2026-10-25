import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { InflationRefusalBanner } from "./InflationRefusalBanner";
import {
  AMENDMENT_DATE_CODE,
  INFLATION_REFUSAL_CODES,
  isInflationRefusal,
  MISSING_YEARS_CODE,
  TARGET_MISSING_CODE,
} from "./refusalCodes";

const FILL_BUTTON = "Заполнить недостающие годы";

function renderBanner(props: Partial<React.ComponentProps<typeof InflationRefusalBanner>>) {
  return render(
    <InflationRefusalBanner
      code={MISSING_YEARS_CODE}
      message="Сообщение сервера."
      canEdit={false}
      onFillMissingYears={vi.fn()}
      {...props}
    />
  );
}

describe("InflationRefusalBanner: три ветки по коду отказа", () => {
  it("inflation_target_missing: сообщение сервера дословно и без кнопки даже у admin", () => {
    const message =
      "У этапа нет даты проведения, и у КП нет даты подготовки — приведение по ИПЦ не к чему привязать.";
    renderBanner({
      code: TARGET_MISSING_CODE,
      message,
      canEdit: true,
      // Годы переданы нарочно: у этой ветки кнопки нет, что бы ни лежало в контексте.
      missingYears: [2024],
      seriesName: "Ряд А",
    });
    expect(TARGET_MISSING_CODE).toBe("inflation_target_missing");
    expect(screen.getByTestId("inflation-refusal")).toHaveTextContent(message);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    // Месяца в адресе нет — его не от чего было разрешить; ряд не назван: правка ряда не лечит.
    expect(screen.queryByText(/ряд и месяц остались/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Выбран ряд/)).not.toBeInTheDocument();
  });

  it("missing_inflation_years: кнопка у admin, названный ряд; у остальных кнопки нет", () => {
    const onFill = vi.fn();
    const { unmount } = renderBanner({
      code: MISSING_YEARS_CODE,
      canEdit: true,
      missingYears: [2024],
      seriesName: "Ряд А",
      onFillMissingYears: onFill,
    });
    expect(screen.getByRole("button", { name: FILL_BUTTON })).toBeInTheDocument();
    expect(screen.getByTestId("inflation-refusal")).toHaveTextContent("Выбран ряд «Ряд А».");
    screen.getByRole("button", { name: FILL_BUTTON }).click();
    expect(onFill).toHaveBeenCalledWith([2024]);
    unmount();

    renderBanner({ code: MISSING_YEARS_CODE, canEdit: false, missingYears: [2024] });
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("amendment_date_missing: сообщение сервера и без кнопки даже у admin", () => {
    renderBanner({
      code: AMENDMENT_DATE_CODE,
      message: "У допсоглашения договора 1 нет даты подготовки сметы.",
      canEdit: true,
      missingYears: [2024],
    });
    expect(screen.getByTestId("inflation-refusal")).toHaveTextContent(
      "У допсоглашения договора 1 нет даты подготовки сметы."
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("InflationRefusalBanner: фраза про адрес", () => {
  const CLAUSE = /Выбранные ряд и месяц остались в\s+адресе/;

  it.each([[MISSING_YEARS_CODE], [AMENDMENT_DATE_CODE]])(
    "%s: по умолчанию фраза про ряд и месяц в адресе есть, с monthInAddress=false - нет",
    (code) => {
      const { unmount } = renderBanner({ code });
      expect(screen.getByTestId("inflation-refusal").textContent).toMatch(CLAUSE);
      unmount();

      renderBanner({ code, monthInAddress: false });
      const text = screen.getByTestId("inflation-refusal").textContent ?? "";
      expect(text).not.toMatch(CLAUSE);
      expect(text).toContain("Числа показаны номинально, без приведения.");
    }
  );
});

describe("isInflationRefusal", () => {
  it("true ровно на трёх кодах приведения", () => {
    expect([...INFLATION_REFUSAL_CODES].sort()).toEqual(
      [AMENDMENT_DATE_CODE, MISSING_YEARS_CODE, TARGET_MISSING_CODE].sort()
    );
    for (const code of [MISSING_YEARS_CODE, AMENDMENT_DATE_CODE, TARGET_MISSING_CODE]) {
      expect(isInflationRefusal(code)).toBe(true);
    }
  });

  it("false на unknown_rung, пустом и неопределённом коде", () => {
    expect(isInflationRefusal("unknown_rung")).toBe(false);
    expect(isInflationRefusal("")).toBe(false);
    expect(isInflationRefusal(undefined)).toBe(false);
  });
});
