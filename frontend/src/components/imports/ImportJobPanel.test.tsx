import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ImportJobPanel } from "./ImportJobPanel";
import type { ImportJob, ImportJobStatus } from "@/types/domain";

/**
 * `ImportJobPanel` — общая часть загрузки (спека §2.14, задача 11). Тесты
 * этого файла проверяют панель саму по себе, без владельца: `job` собирается
 * вручную, ниоткуда не поллится — сеть здесь не участвует.
 */

/** Минимальный `ImportJob` с полями, которые панель читает; остальное — форма. */
function baseJob(overrides: Partial<ImportJob> & { status: ImportJobStatus }): ImportJob {
  return {
    id: 900,
    owner_type: "contract",
    filename: "смета.xlsx",
    file_sha256: "a".repeat(64),
    error_text: null,
    warnings: [],
    counters: {
      positions_total: 0,
      matched_cache: 0,
      matched_exact: 0,
      matched_nonposition: 0,
      to_review: 0,
    },
    estimates_created: null,
    created_at: null,
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

const noop = () => {};

describe("ImportJobPanel (спека §2.14)", () => {
  it("job в статусе done показывает пять счётчиков", () => {
    const job = baseJob({
      status: "done",
      counters: {
        positions_total: 1830,
        matched_cache: 400,
        matched_exact: 1200,
        matched_nonposition: 30,
        to_review: 200,
      },
    });
    render(
      <ImportJobPanel
        job={job}
        uploading={false}
        idempotent={false}
        idempotentNote="неважно — idempotent=false, панель эту подпись не покажет"
        runningHint="неважно — статус не running, панель эту подпись не покажет"
        rejection={null}
        disabled={false}
        hint="XLSX или XLSM, до 25 МБ"
        onDrop={noop}
      />
    );

    expect(screen.getByText("Позиций")).toBeInTheDocument();
    expect(screen.getByText("Из кэша")).toBeInTheDocument();
    expect(screen.getByText("Точно")).toBeInTheDocument();
    expect(screen.getByText("Не работы")).toBeInTheDocument();
    expect(screen.getByText("На разбор")).toBeInTheDocument();
    expect(screen.getByText("1830")).toBeInTheDocument();
  });

  it("job в статусе error показывает error_text ролью alert", () => {
    const job = baseJob({ status: "error", error_text: "Не удалось разобрать файл." });
    render(
      <ImportJobPanel
        job={job}
        uploading={false}
        idempotent={false}
        idempotentNote="неважно — idempotent=false, панель эту подпись не покажет"
        runningHint="неважно — статус не running, панель эту подпись не покажет"
        rejection={null}
        disabled={false}
        hint="XLSX или XLSM, до 25 МБ"
        onDrop={noop}
      />
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Не удалось разобрать файл.");
  });

  it("rejection показывает data-testid upload-rejection", () => {
    render(
      <ImportJobPanel
        job={undefined}
        uploading={false}
        idempotent={false}
        idempotentNote="неважно — idempotent=false, панель эту подпись не покажет"
        runningHint="неважно — статус не running, панель эту подпись не покажет"
        rejection="Не удалось загрузить файл."
        disabled={false}
        hint="XLSX или XLSM, до 25 МБ"
        onDrop={noop}
      />
    );

    expect(screen.getByTestId("upload-rejection")).toHaveTextContent("Не удалось загрузить файл.");
  });

  it("idempotent показывает подпись, ЦЕЛИКОМ пришедшую от вызывающего", () => {
    // Панель не знает своего владельца (ревью задачи 11, finding 2) и не несёт
    // зашитого текста про смету или раунд — подпись целиком приходит пропом
    // `idempotentNote`. Текст здесь намеренно НЕ похож ни на формулировку
    // `EstimateUploadPanel`, ни на формулировку `RoundUploadPanel`: если бы
    // панель игнорировала проп и рисовала собственный текст, этот тест не
    // прошёл бы ни при каком старом хардкоде.
    const job = baseJob({ status: "done" });
    const note = "Проверочная подпись идемпотентности — источник только проп.";
    render(
      <ImportJobPanel
        job={job}
        uploading={false}
        idempotent
        idempotentNote={note}
        runningHint="неважно — статус не running, панель эту подпись не покажет"
        rejection={null}
        disabled={false}
        hint="XLSX или XLSM, до 25 МБ"
        onDrop={noop}
      />
    );

    expect(screen.getByText(note)).toBeInTheDocument();
  });

  it("children рендерится над dropzone", () => {
    render(
      <ImportJobPanel
        job={undefined}
        uploading={false}
        idempotent={false}
        idempotentNote="неважно — idempotent=false, панель эту подпись не покажет"
        runningHint="неважно — статус не running, панель эту подпись не покажет"
        rejection={null}
        disabled={false}
        hint="XLSX или XLSM, до 25 МБ"
        onDrop={noop}
      >
        <div data-testid="slot">Номер допсоглашения</div>
      </ImportJobPanel>
    );

    const slot = screen.getByTestId("slot");
    const dropzoneInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(slot).toBeInTheDocument();
    expect(dropzoneInput).not.toBeNull();
    // `compareDocumentPosition` — узел `slot` обязан идти ДО input-а dropzone
    // в порядке документа, то есть выше него, а не просто присутствовать
    // где-то на странице.
    expect(
      slot.compareDocumentPosition(dropzoneInput) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });
});
