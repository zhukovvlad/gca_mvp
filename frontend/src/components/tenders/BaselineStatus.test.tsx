import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BaselineStatus } from "./BaselineStatus";
import { sampleTenderCard } from "@/test/fixtures";

const base = sampleTenderCard.rounds[0];

describe("BaselineStatus — четыре состояния (спека §2.14)", () => {
  it("текущий done-job и baseline есть → «загружена» с итогом", () => {
    render(<BaselineStatus round={{ ...base, current_job_id: 9101, baseline_estimate_id: 8100, baseline_total_including_vat: "1150.00" }} hasEstimates />);
    expect(screen.getByText(/Расчётная стоимость загружена/)).toBeInTheDocument();
    expect(screen.getByText(/Итого с НДС/)).toHaveTextContent(/1\s150,00/);
  });
  it("текущий done-job, baseline нет → «в файле не заполнена»", () => {
    render(<BaselineStatus round={{ ...base, current_job_id: 9101, baseline_estimate_id: null }} hasEstimates />);
    expect(screen.getByText(/в файле не заполнена/)).toBeInTheDocument();
  });
  it("текущего job нет, смет нет → «Файл раунда не загружен»", () => {
    render(<BaselineStatus round={{ ...base, current_job_id: null, baseline_estimate_id: null }} hasEstimates={false} />);
    expect(screen.getByText(/Файл раунда не загружен/)).toBeInTheDocument();
    expect(screen.queryByText(/не заполнена/)).toBeNull();
  });
  it("текущего job нет, сметы остались → «состав изменён — требуется полная замена»", () => {
    render(<BaselineStatus round={{ ...base, current_job_id: null, baseline_estimate_id: null }} hasEstimates />);
    expect(screen.getByText(/Состав раунда изменён после импорта/)).toBeInTheDocument();
  });
});
