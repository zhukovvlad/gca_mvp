import { describe, expect, it } from "vitest";

import { JOB_POLL_INTERVAL_MS, isTerminal, jobRefetchInterval } from "./jobPolling";
import type { ImportJob, ImportJobStatus } from "@/types/domain";

function job(status: ImportJobStatus): ImportJob {
  return {
    id: 1,
    owner_type: "contract",
    contract_id: 100,
    amendment_no: null,
    filename: "смета.xlsx",
    file_sha256: "a".repeat(64),
    status,
    error_text: null,
    warnings: [],
    counters: {
      positions_total: 0,
      matched_cache: 0,
      matched_exact: 0,
      matched_nonposition: 0,
      to_review: 0,
    },
    estimate_id: null,
    estimates_created: 1,
    created_at: null,
    started_at: null,
    finished_at: null,
  };
}

describe("jobRefetchInterval", () => {
  it("опрашивает, пока задание в работе", () => {
    for (const status of ["pending", "parsing", "importing", "matching"] as ImportJobStatus[]) {
      expect(jobRefetchInterval(job(status))).toBe(JOB_POLL_INTERVAL_MS);
    }
  });

  it("останавливается на терминальном статусе", () => {
    // Без этого экран опрашивал бы сервер вечно: задание в done больше не
    // меняется, а запросы шли бы, пока открыта вкладка.
    expect(jobRefetchInterval(job("done"))).toBe(false);
    expect(jobRefetchInterval(job("error"))).toBe(false);
  });

  it("опрашивает, когда данных ещё нет", () => {
    // Первый рендер после 202: задание как раз в pending.
    expect(jobRefetchInterval(undefined)).toBe(JOB_POLL_INTERVAL_MS);
  });
});

describe("isTerminal", () => {
  it("различает работу и завершение", () => {
    expect(isTerminal(job("matching"))).toBe(false);
    expect(isTerminal(job("done"))).toBe(true);
    expect(isTerminal(job("error"))).toBe(true);
    expect(isTerminal(undefined)).toBe(false);
    expect(isTerminal(null)).toBe(false);
  });
});
