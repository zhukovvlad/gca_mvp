/**
 * @vitest-environment node
 *
 * Тесты чистых функций: DOM здесь не наблюдается ни разу, а jsdom стоит около
 * секунды на файл. Выигрыш — на ТОЧЕЧНОМ прогоне (эти девять файлов: 9,1 с →
 * 3,8 с), а НЕ на полном наборе: там окружения поднимаются параллельно, и
 * 42,3 с накопленного `environment` уходят в тень тяжёлых компонентных файлов
 * (замер 2026-09-02: 98,6 с до, 100,2 с после — в пределах разброса). Признак
 * «нужен ли jsdom» объявляется ФАЙЛОМ, а не глобом в конфиге: глоб пришлось бы
 * держать в синхронизации с деревом, и молчаливое возвращение файла в jsdom
 * заметить было бы нечем.
 */
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
