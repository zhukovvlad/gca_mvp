import { Fragment } from "react";

import { findMatchRanges } from "./matchRanges";

/** Строка с подсвеченными совпавшими словами: куски и `<mark>`, без разметки из данных. */
export function HighlightedText({ text, needles }: { text: string; needles: readonly string[] }) {
  const ranges = findMatchRanges(text, needles);
  const parts: Array<{ value: string; hit: boolean }> = [];
  let cursor = 0;
  for (const range of ranges) {
    if (range.from > cursor) parts.push({ value: text.slice(cursor, range.from), hit: false });
    parts.push({ value: text.slice(range.from, range.to), hit: true });
    cursor = range.to;
  }
  if (cursor < text.length) parts.push({ value: text.slice(cursor), hit: false });
  return (
    <>
      {parts.map((part, index) => (
        <Fragment key={index}>
          {part.hit ? (
            <mark className="rounded-sm bg-warning-soft px-0.5 text-warning-text">{part.value}</mark>
          ) : (
            part.value
          )}
        </Fragment>
      ))}
    </>
  );
}
