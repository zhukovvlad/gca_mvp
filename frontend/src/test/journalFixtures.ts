import type { JournalItemDetail } from "@/types/domain";

/**
 * Записи «Журнала ИИ» по форме провода (`backend/crud/ai_journal.py`): у замка без события поля
 * `null`, у отменённой записи — `undone`, у вернувшей автоматику — `restored_at`. Названия и
 * числа выдуманы. Время — около полудня UTC, чтобы день записи не зависел от часового пояса.
 */

const FAMILY_ROOF = { id: 43, title: "Кровельные работы" };
const FAMILY_FLOORS = { id: 2, title: "Устройство покрытий полов" };
const MATERIAL = { id: 101, name: "Материал" };

export const JOURNAL_ADMIN_EMAIL = "admin@example.com";

/** Деление, ещё доступное для отмены: две части, в первой после деления прибавилось строк. */
export const JOURNAL_SPLIT_ID = 104;
/** Деление с замком: после него часть правили вручную. */
export const JOURNAL_LOCKED_SPLIT_ID = 103;
/** Деление с замком без события (архивная часть): `locked_by` с пустыми полями. */
export const JOURNAL_BARE_LOCK_SPLIT_ID = 108;
export const JOURNAL_COMPOSITE_ID = 105;
export const JOURNAL_EQUATED_ID = 106;
export const JOURNAL_VALUE_ADDED_ID = 107;
export const JOURNAL_UNDONE_COMPOSITE_ID = 102;
export const JOURNAL_RESTORED_EQUATED_ID = 101;

export function initialJournal(): JournalItemDetail[] {
  return [
    {
      id: JOURNAL_VALUE_ADDED_ID,
      kind: "value_added",
      created_at: "2026-09-11T12:40:00+00:00",
      family: FAMILY_ROOF,
      context: { id: 601, title: "Устройство кровли из металло-черепицы" },
      parameter: MATERIAL,
      text: "металло-черепица",
      summary_values: ["металло-черепица"],
      state: "active",
      undo: { available: false, locked_by: null },
      undone: null,
      restored_at: null,
    },
    {
      id: JOURNAL_EQUATED_ID,
      kind: "equated",
      created_at: "2026-09-11T12:38:00+00:00",
      family: FAMILY_ROOF,
      context: null,
      parameter: MATERIAL,
      text: "профлист",
      summary_values: ["профлист", "профнастил"],
      state: "active",
      undo: { available: true, locked_by: null },
      undone: null,
      restored_at: null,
      details: {
        contexts: [
          { id: 611, title: "Устройство кровли из профлиста", parameter_value: "профнастил" },
          { id: 612, title: "Монтаж кровельного покрытия", parameter_value: "профнастил" },
        ],
        undo_text: "профлист",
      },
    },
    {
      id: JOURNAL_COMPOSITE_ID,
      kind: "composite",
      created_at: "2026-09-10T12:14:00+00:00",
      family: FAMILY_ROOF,
      context: null,
      parameter: MATERIAL,
      text: "профнастил, металлочерепица",
      summary_values: ["профнастил", "металлочерепица"],
      state: "active",
      undo: { available: true, locked_by: null },
      undone: null,
      restored_at: null,
      details: {
        contexts: [
          { id: 621, title: "Устройство кровли, скат", parameter_value: "профнастил + металлочерепица" },
          { id: 622, title: "Устройство кровли, парапет", parameter_value: "профнастил + металлочерепица" },
        ],
        undo_text: "профнастил, металлочерепица",
      },
    },
    {
      id: JOURNAL_BARE_LOCK_SPLIT_ID,
      kind: "split",
      created_at: "2026-09-10T12:13:30+00:00",
      family: FAMILY_FLOORS,
      context: { id: 731, title: "Устройство покрытий из плитки" },
      parameter: null,
      text: null,
      summary_values: ["Санузлы", "Коридоры"],
      state: "locked",
      undo: { available: false, locked_by: { event_type: null, actor_email: null, at: null } },
      undone: null,
      restored_at: null,
      details: {
        level: 1,
        parts: [
          { context_id: 731, title: "Санузлы", members_now: 3, members_after: 7 },
          { context_id: 732, title: "Коридоры", members_now: 4, members_after: 0 },
        ],
      },
    },
    {
      id: JOURNAL_LOCKED_SPLIT_ID,
      kind: "split",
      created_at: "2026-09-10T12:13:00+00:00",
      family: FAMILY_FLOORS,
      context: { id: 721, title: "Бетонирование лестниц" },
      parameter: null,
      text: null,
      summary_values: ["Марши", "Площадки"],
      state: "locked",
      undo: {
        available: false,
        locked_by: {
          event_type: "members_moved",
          actor_email: JOURNAL_ADMIN_EMAIL,
          at: "2026-09-10T14:05:00+00:00",
        },
      },
      undone: null,
      restored_at: null,
      details: {
        level: 2,
        parts: [
          { context_id: 721, title: "Марши", members_now: 6, members_after: 11 },
          { context_id: 722, title: "Площадки", members_now: 5, members_after: 0 },
        ],
      },
    },
    {
      id: JOURNAL_SPLIT_ID,
      kind: "split",
      created_at: "2026-09-10T12:12:00+00:00",
      family: FAMILY_ROOF,
      context: { id: 701, title: "Устройство кровли" },
      parameter: null,
      text: null,
      summary_values: ["Скатная кровля", "Плоская кровля"],
      state: "active",
      undo: { available: true, locked_by: null },
      undone: null,
      restored_at: null,
      details: {
        level: 2,
        parts: [
          { context_id: 701, title: "Скатная кровля", members_now: 14, members_after: 23 },
          { context_id: 702, title: "Плоская кровля", members_now: 9, members_after: 0 },
        ],
      },
    },
    {
      id: JOURNAL_UNDONE_COMPOSITE_ID,
      kind: "composite",
      created_at: "2026-09-10T12:10:00+00:00",
      family: FAMILY_ROOF,
      context: null,
      parameter: MATERIAL,
      text: "битум, рубероид",
      summary_values: ["битум", "рубероид"],
      state: "undone",
      undo: { available: false, locked_by: null },
      undone: { at: "2026-09-10T15:42:00+00:00", by_email: JOURNAL_ADMIN_EMAIL },
      restored_at: null,
      details: {
        contexts: [{ id: 631, title: "Устройство гидроизоляции", parameter_value: "битум, рубероид" }],
        undo_text: "битум, рубероид",
      },
    },
    {
      id: JOURNAL_RESTORED_EQUATED_ID,
      kind: "equated",
      created_at: "2026-09-10T12:05:00+00:00",
      family: FAMILY_ROOF,
      context: null,
      parameter: MATERIAL,
      text: "металлочерепица Монтеррей",
      summary_values: ["металлочерепица Монтеррей", "металлочерепица"],
      state: "undone",
      undo: { available: false, locked_by: null },
      undone: { at: "2026-09-10T15:50:00+00:00", by_email: JOURNAL_ADMIN_EMAIL },
      restored_at: "2026-09-10T16:00:00+00:00",
      details: {
        contexts: [{ id: 641, title: "Монтаж металлочерепицы", parameter_value: "металлочерепица Монтеррей" }],
        undo_text: "металлочерепица Монтеррей",
      },
    },
  ];
}
