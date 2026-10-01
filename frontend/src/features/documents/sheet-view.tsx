"use client";

import { useMemo, useRef, useState, type CSSProperties } from "react";

import type { Sheet, SheetStyle, Workbook } from "@/lib/api";
import { formatSheetCell } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

import { clamp, useWheelZoom, ViewerToolbar, ZOOM_STEP } from "./viewer-toolbar";

// The backend's caps (documents/sheets.py), for the note under a cut sheet.
const MAX_ROWS = 1000;
const MAX_COLUMNS = 50;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4;
const BORDERS: Record<NonNullable<SheetStyle["top"]>, string> = {
  thin: "1px solid",
  medium: "2px solid",
  thick: "3px solid",
  double: "3px double",
  dashed: "1px dashed",
  dotted: "1px dotted",
};

type Cell = NonNullable<Sheet["rows"][number]["cells"][number]>;
type Span = { rows: number; columns: number };
// How far text runs on over empty cells, in pixels from its own cell's far edge; null runs on past
// the last column shown, where nothing can be in its way.
type Spill = { side: "left" | "right"; width: number | null };
type Edges = { right: SheetStyle["right"] | undefined; bottom: SheetStyle["bottom"] | undefined };

/**
 * A spreadsheet as Excel lays it out: its sheets as tabs, lettered columns and numbered rows, and
 * each sheet's merged cells, widths, heights, fonts, fills, borders and number formats. It is drawn
 * on a white page, as the fills and font colours in the file were chosen for one.
 */
export function SheetView({ workbook }: { workbook: Workbook }) {
  const { t } = useI18n();
  const [active, setActive] = useState(0);
  const [zoom, setZoom] = useState(1);
  const sheets = workbook.sheets;
  const sheet = sheets[active] ?? sheets[0];
  if (!sheet) return <p className="text-muted-foreground p-4 text-sm">{t.files.emptySheet}</p>;

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
        <div className="relative flex min-h-0 flex-1 flex-col">
          <SheetGrid
            key={active}
            sheet={sheet}
            font={workbook.font}
            size={workbook.font_size}
            zoom={zoom}
            onZoom={(next) => setZoom(clamp(next, MIN_ZOOM, MAX_ZOOM))}
          />
          <ViewerToolbar
            zoom={zoom}
            canZoomIn={zoom < MAX_ZOOM}
            canZoomOut={zoom > MIN_ZOOM}
            onZoomIn={() => setZoom(clamp(zoom * ZOOM_STEP, MIN_ZOOM, MAX_ZOOM))}
            onZoomOut={() => setZoom(clamp(zoom / ZOOM_STEP, MIN_ZOOM, MAX_ZOOM))}
            onFit={() => setZoom(1)}
            className="absolute bottom-3 left-1/2 -translate-x-1/2"
          />
        </div>
      )}
      {sheet.truncated || workbook.omitted > 0 ? (
        <div className="text-muted-foreground border-border shrink-0 border-t px-3 py-2 text-xs">
          {sheet.truncated ? <p>{t.files.truncated(MAX_ROWS, MAX_COLUMNS)}</p> : null}
          {workbook.omitted > 0 ? <p>{t.files.omittedSheets(workbook.omitted)}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

function SheetGrid({
  sheet,
  font,
  size,
  zoom,
  onZoom,
}: {
  sheet: Sheet;
  font: string | null;
  size: number;
  zoom: number;
  onZoom: (zoom: number) => void;
}) {
  const { locale } = useI18n();
  const { spans, covered } = useMemo(() => mergeMap(sheet), [sheet]);
  const scroller = useRef<HTMLDivElement>(null);
  useWheelZoom(scroller, (factor) => onZoom(zoom * factor));
  const columns = sheet.columns.length;
  const taken = (key: string) => covered.has(key) || spans.has(key);

  return (
    <div ref={scroller} className="min-h-0 flex-1 overflow-auto bg-[Canvas] pb-14 text-[CanvasText] scheme-light">
      <table
        className="table-fixed border-collapse leading-tight"
        style={{
          width: sheet.columns.reduce((total, column) => total + pixels(column, sheet), 40),
          fontFamily: family(font),
          fontSize: `${size}pt`,
          // Scales the widths, heights and fonts Excel set together, as its own zoom does.
          zoom,
        }}
      >
        <colgroup>
          <col style={{ width: 40 }} />
          {sheet.columns.map((column, index) => (
            <col
              key={index}
              style={{ width: pixels(column, sheet), visibility: column.hidden ? "collapse" : undefined }}
            />
          ))}
        </colgroup>
        <thead>
          <tr>
            <th className="sticky start-0 top-0 z-20 bg-[color-mix(in_srgb,CanvasText_6%,Canvas)]" />
            {sheet.columns.map((_, index) => (
              <th
                key={index}
                scope="col"
                className="sticky top-0 z-10 bg-[color-mix(in_srgb,CanvasText_6%,Canvas)] px-1 py-0.5 text-xs font-normal text-[GrayText] shadow-[inset_0_-1px_color-mix(in_srgb,CanvasText_15%,transparent)]"
              >
                {columnName(index)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sheet.rows.map((row, rowIndex) => (
            <tr
              key={rowIndex}
              style={{
                // Points to pixels.
                height: ((row.height ?? sheet.default_height) * 4) / 3,
                visibility: row.hidden ? "collapse" : undefined,
              }}
            >
              <th
                scope="row"
                className="sticky start-0 z-10 bg-[color-mix(in_srgb,CanvasText_6%,Canvas)] px-1 text-end text-xs font-normal text-[GrayText] tabular-nums shadow-[inset_-1px_0_color-mix(in_srgb,CanvasText_15%,transparent)]"
              >
                {rowIndex + 1}
              </th>
              {Array.from({ length: columns }, (_, columnIndex) => {
                const key = `${rowIndex}:${columnIndex}`;
                if (covered.has(key)) return null;
                const cell = row.cells[columnIndex] ?? null;
                const span = spans.get(key);
                const style = sheet.styles[cell?.style ?? 0];
                const spill = span || cell === null ? null : spillOf(cell, sheet, rowIndex, columnIndex, taken);
                const text = cell && cell.kind !== "blank" ? formatSheetCell(cell, locale) : null;
                return (
                  <td
                    key={columnIndex}
                    rowSpan={span?.rows}
                    colSpan={span?.columns}
                    style={cellStyle(cell, style, spill !== null, span && mergeEdges(sheet, rowIndex, columnIndex, span))}
                  >
                    {spill?.width != null ? (
                      // A box as wide as the empty run, clipped where it ends; running on to the
                      // left, it reaches back from the cell's right edge.
                      <span
                        className="block overflow-hidden"
                        style={{
                          width: spill.width - padding(style) - 3,
                          marginInlineStart:
                            spill.side === "left" ? pixels(sheet.columns[columnIndex]!, sheet) - spill.width : undefined,
                        }}
                      >
                        {text}
                      </span>
                    ) : (
                      text
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Where each merge starts and how far it reaches, and every cell it covers. */
function mergeMap(sheet: Sheet): { spans: Map<string, Span>; covered: Set<string> } {
  const spans = new Map<string, Span>();
  const covered = new Set<string>();
  for (const merge of sheet.merges) {
    spans.set(`${merge.row}:${merge.column}`, { rows: merge.rows, columns: merge.columns });
    for (let row = merge.row; row < merge.row + merge.rows; row++) {
      for (let column = merge.column; column < merge.column + merge.columns; column++) {
        if (row !== merge.row || column !== merge.column) covered.add(`${row}:${column}`);
      }
    }
  }
  return { spans, covered };
}

/**
 * Excel lets text run on over the empty cells beside it, up to the next one with a value or in a
 * merge: to the right, or to the left when it is aligned right. A figure never runs on, and neither
 * does wrapped or centred text.
 */
function spillOf(
  cell: Cell,
  sheet: Sheet,
  row: number,
  column: number,
  taken: (key: string) => boolean,
): Spill | null {
  const style = sheet.styles[cell.style];
  if (cell.kind !== "text" || style?.wrap) return null;
  const step = !style?.align || style.align === "left" ? 1 : style.align === "right" ? -1 : 0;
  const free = (index: number) => {
    if (index < 0 || taken(`${row}:${index}`)) return false;
    const beside = sheet.rows[row]?.cells[index];
    return beside === undefined || beside === null || beside.kind === "blank";
  };
  if (step === 0 || !free(column + step)) return null;
  let width = pixels(sheet.columns[column]!, sheet);
  let index = column + step;
  for (; index < sheet.columns.length && free(index); index += step) width += pixels(sheet.columns[index]!, sheet);
  return { side: step > 0 ? "right" : "left", width: index >= sheet.columns.length ? null : width };
}

/** A merge's right and bottom borders, which Excel keeps on the cells along those edges; the
 *  anchor's own lie inside the merge. */
function mergeEdges(sheet: Sheet, row: number, column: number, span: Span): Edges {
  const style = (at: number, of: number) => {
    const cell = sheet.rows[at]?.cells[of];
    return cell ? sheet.styles[cell.style] : undefined;
  };
  return {
    right: style(row, column + span.columns - 1)?.right,
    bottom: style(row + span.rows - 1, column)?.bottom,
  };
}

/** The room before a cell's text: Excel's 3 pixels, and 9 more for each step of indent. */
function padding(style: SheetStyle | undefined): number {
  return 3 + (style?.indent ?? 0) * 9;
}

function cellStyle(
  cell: Cell | null,
  style: SheetStyle | undefined,
  spills: boolean,
  edges: Edges | undefined,
): CSSProperties {
  // Excel's faint gridlines, drawn in the style a table border loses to any other, so a border the
  // file sets wins on either side of the line.
  const grid = "1px ridge color-mix(in srgb, CanvasText 12%, Canvas)";
  const line = (side: SheetStyle["top"] | undefined) => (side ? `${BORDERS[side]} CanvasText` : grid);
  const figure = cell?.kind === "number" || cell?.kind === "date";
  return {
    padding: "0 3px",
    paddingInlineStart: padding(style),
    borderTop: line(style?.top),
    borderRight: line(edges ? edges.right : style?.right),
    borderBottom: line(edges ? edges.bottom : style?.bottom),
    borderLeft: line(style?.left),
    fontFamily: family(style?.font),
    fontWeight: style?.bold ? 700 : undefined,
    fontStyle: style?.italic ? "italic" : undefined,
    textDecoration: [style?.underline && "underline", style?.strike && "line-through"].filter(Boolean).join(" ") || undefined,
    fontSize: style?.size ? `${style.size}pt` : undefined,
    color: style?.color ?? undefined,
    backgroundColor: style?.fill ?? undefined,
    textAlign: style?.align ?? (figure ? "right" : cell?.kind === "bool" || cell?.kind === "error" ? "center" : "left"),
    verticalAlign: style?.valign ?? "bottom",
    whiteSpace: style?.wrap ? "pre-wrap" : "pre",
    overflow: spills ? "visible" : "hidden",
    overflowWrap: style?.wrap ? "anywhere" : undefined,
    maxWidth: 0,
  };
}

function pixels(column: Sheet["columns"][number], sheet: Sheet): number {
  return column.hidden ? 0 : (column.width ?? sheet.default_width);
}

/** The file's font where the reader's computer has it, and the app's where it does not. The name
 *  is a plain one: the API passes no other. */
function family(font: string | null | undefined): string | undefined {
  return font ? `"${font}", var(--font-sans)` : undefined;
}

/** A, B, …, Z, AA, AB, … as Excel names its columns. */
function columnName(index: number): string {
  let name = "";
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) {
    name = String.fromCharCode(65 + ((n - 1) % 26)) + name;
  }
  return name;
}
