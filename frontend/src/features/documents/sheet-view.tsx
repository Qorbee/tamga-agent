"use client";

import { useState } from "react";

import type { Sheet } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

// The backend's caps (documents/sheets.py), for the note under a cut sheet.
const MAX_ROWS = 1000;
const MAX_COLUMNS = 50;
const NUMBER = /^[-+]?[\d.,\s]*\d[\d.,\s]*%?$/;

/** A spreadsheet as Excel shows it: its sheets as tabs, lettered columns and numbered rows. */
export function SheetView({ sheets }: { sheets: Sheet[] }) {
  const { t } = useI18n();
  const [active, setActive] = useState(0);
  const sheet = sheets[active] ?? sheets[0];
  if (!sheet) return <p className="text-muted-foreground p-4 text-sm">{t.files.emptySheet}</p>;
  const columns = Math.max(0, ...sheet.rows.map((row) => row.length));

  return (
    <div className="flex h-full min-h-0 flex-col">
      {sheets.length > 1 ? (
        <div role="tablist" className="border-border flex shrink-0 gap-1 overflow-x-auto border-b px-2 py-1.5">
          {sheets.map((item, index) => (
            <button
              key={index}
              type="button"
              role="tab"
              aria-selected={index === active}
              onClick={() => setActive(index)}
              className={cn(
                "text-muted-foreground hover:bg-accent shrink-0 rounded-md px-2.5 py-1 text-xs",
                index === active && "bg-secondary text-foreground font-medium",
              )}
            >
              {item.name}
            </button>
          ))}
        </div>
      ) : null}
      {sheet.rows.length === 0 ? (
        <p className="text-muted-foreground p-4 text-sm">{t.files.emptySheet}</p>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="border-separate border-spacing-0 text-xs">
            <thead>
              <tr>
                <th className="bg-secondary border-border sticky start-0 top-0 z-20 min-w-10 border-e border-b" />
                {Array.from({ length: columns }, (_, index) => (
                  <th
                    key={index}
                    scope="col"
                    className="bg-secondary text-muted-foreground border-border sticky top-0 z-10 border-e border-b px-2 py-1 font-normal"
                  >
                    {columnName(index)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sheet.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  <th
                    scope="row"
                    className="bg-secondary text-muted-foreground border-border sticky start-0 z-10 border-e border-b px-2 py-1 text-end font-normal tabular-nums"
                  >
                    {rowIndex + 1}
                  </th>
                  {Array.from({ length: columns }, (_, index) => {
                    const cell = row[index] ?? null;
                    return (
                      <td
                        key={index}
                        title={cell ?? undefined}
                        className={cn(
                          "border-border max-w-80 truncate border-e border-b px-2 py-1 whitespace-nowrap",
                          cell && NUMBER.test(cell) && "text-end tabular-nums",
                        )}
                      >
                        {cell}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {sheet.truncated ? (
        <p className="text-muted-foreground border-border shrink-0 border-t px-3 py-2 text-xs">
          {t.files.truncated(MAX_ROWS, MAX_COLUMNS)}
        </p>
      ) : null}
    </div>
  );
}

/** A, B, …, Z, AA, AB, … as Excel names its columns. */
function columnName(index: number): string {
  let name = "";
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) {
    name = String.fromCharCode(65 + ((n - 1) % 26)) + name;
  }
  return name;
}
