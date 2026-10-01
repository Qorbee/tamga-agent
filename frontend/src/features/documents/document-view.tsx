"use client";

import { Download04Icon, ImageNotFound01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";

import { fileIcon } from "@/components/file-icon";
import { HtmlFrame } from "@/components/html-frame";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDownload } from "@/hooks/use-download";
import { useObjectUrl } from "@/hooks/use-object-url";
import {
  getDocument,
  getDocumentFile,
  getDocumentHtml,
  getDocumentRendition,
  getDocumentSheets,
  getDocumentText,
  type DocumentDetail,
  type UnreadableReason,
} from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { bodyAttributes, parseSafeHtml, takeStyles } from "@/lib/safe-html";
import { cn } from "@/lib/utils";

import { ArchiveView, WAITING_POLL, WaitingView } from "./archive-view";
import { DocumentMenu } from "./document-menu";
import { ImageView } from "./image-view";
import { PdfView } from "./pdf-view";
import { SheetView } from "./sheet-view";

function useDocument(id: string) {
  return useQuery({
    queryKey: ["document", id],
    queryFn: () => getDocument(id),
    // An archive waiting to be opened, or a PDF to be made, is asked again until the worker is done.
    refetchInterval: (query) => (query.state.data && waiting(query.state.data) ? WAITING_POLL : false),
  });
}

/** The canvas's title for a file: its name and kind, opening its menu. */
export function DocumentTitle({ id }: { id: string }) {
  const document = useDocument(id);
  if (!document.data) return <Skeleton className="h-4 w-40" />;
  return <DocumentMenu document={document.data} />;
}

export function DocumentDownload({ id }: { id: string }) {
  const { t } = useI18n();
  const download = useDownload(id);
  return (
    <Button variant="ghost" size="icon-sm" disabled={download.isPending} onClick={() => download.mutate()}>
      <HugeiconsIcon icon={Download04Icon} strokeWidth={1.75} />
      <span className="sr-only">{t.files.download}</span>
    </Button>
  );
}

/** A file drawn by the viewer its kind needs; a file no viewer takes is offered as a download. */
export function DocumentView({ id, className }: { id: string; className?: string }) {
  const { t } = useI18n();
  const document = useDocument(id);

  if (document.isError) return <p className="text-danger-foreground p-4 text-sm">{t.errors.load}</p>;
  if (!document.data) return <Skeleton className="m-4 h-64 rounded-xl" />;
  const { preview, read_state: read, unreadable_reason: reason } = document.data;

  return (
    <div className={cn("h-full", className)}>
      {preview === "sheet" ? (
        <SheetPreview id={id} />
      ) : preview === "image" || preview === "pdf" ? (
        <DrawnPreview id={id} preview={preview} name={document.data.filename} />
      ) : preview === "archive" && (read === "read" || read === "pending") ? (
        <ArchiveView document={document.data} waiting={read === "pending"} />
      ) : preview === "archive" ? (
        <NoPreview id={id} failed={reason !== null} reason={reason} notOpened={read === null} />
      ) : preview === "office" && document.data.converted ? (
        <DrawnPreview id={id} preview="pdf" name={document.data.filename} rendition />
      ) : preview === "office" && waiting(document.data) ? (
        <WaitingView preview="office" title={t.files.office.converting} body={t.files.office.convertingBody} />
      ) : preview === "office" ? (
        <NoPreview id={id} failed={reason !== null} reason={reason} />
      ) : preview === "html" ? (
        <PagePreview id={id} />
      ) : preview === "text" ? (
        <TextPreview id={id} />
      ) : (
        <NoPreview id={id} />
      )}
    </div>
  );
}

function SheetPreview({ id }: { id: string }) {
  const sheets = useQuery({ queryKey: ["document-sheets", id], queryFn: () => getDocumentSheets(id) });
  if (sheets.isError) return <NoPreview id={id} failed />;
  if (!sheets.data) return <Skeleton className="m-4 h-64 rounded-xl" />;
  return <SheetView workbook={sheets.data} />;
}

/**
 * Whether the worker has yet to open this archive, or make this office file's PDF. An office file
 * not asked for yet came in a mail still being processed, which asks for it; an archive not asked
 * for is one too deep to open.
 */
function waiting(document: DocumentDetail): boolean {
  if (document.preview === "office") {
    return !document.converted && document.read_state !== "read" && document.read_state !== "unreadable";
  }
  return document.preview === "archive" && document.read_state === "pending";
}

/** An image or PDF drawn from its bytes; with `rendition`, the PDF made of an office file. */
function DrawnPreview({
  id,
  preview,
  name,
  rendition = false,
}: {
  id: string;
  preview: "image" | "pdf";
  name: string;
  rendition?: boolean;
}) {
  const file = useQuery({
    queryKey: [rendition ? "document-rendition" : "document-file", id],
    queryFn: () => (rendition ? getDocumentRendition(id) : getDocumentFile(id)),
    staleTime: Infinity,
  });
  const [failed, setFailed] = useState(false);
  const fail = useCallback(() => setFailed(true), []);
  // A file the backend would not vouch for arrives as a download, never as something to draw.
  const drawable = file.data && file.data.type !== "application/octet-stream";

  if (file.isError || failed || (file.data && !drawable)) return <NoPreview id={id} failed />;
  if (!file.data) return <Skeleton className="m-4 h-64 rounded-xl" />;
  return preview === "pdf" ? (
    <PdfView file={file.data} onFailed={fail} />
  ) : (
    <DrawnImage file={file.data} name={name} onFailed={fail} />
  );
}

function DrawnImage({ file, name, onFailed }: { file: Blob; name: string; onFailed: () => void }) {
  const url = useObjectUrl(file);
  return url ? <ImageView url={url} name={name} onFailed={onFailed} /> : null;
}

/** A web page as its sender laid it out, sanitized by the backend and framed; pictures on request. */
function PagePreview({ id }: { id: string }) {
  const { t } = useI18n();
  const page = useQuery({ queryKey: ["document-html", id], queryFn: () => getDocumentHtml(id), staleTime: Infinity });
  const [pictures, setPictures] = useState(false);
  const shown = useMemo(() => {
    if (!page.data) return undefined;
    const doc = parseSafeHtml(page.data.html, {}, pictures);
    const styles = takeStyles(doc);
    return { html: styles + doc.body.innerHTML, body: bodyAttributes(doc) };
  }, [page.data, pictures]);

  if (page.isError) return <NoPreview id={id} failed />;
  if (!page.data || !shown) return <Skeleton className="m-4 h-64 rounded-xl" />;
  return (
    <div className="grid gap-3 p-3 md:p-4">
      {!pictures && page.data.remote_images > 0 ? (
        <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
          <p className="flex min-w-0 flex-1 basis-60 items-start gap-1.5">
            <HugeiconsIcon icon={ImageNotFound01Icon} strokeWidth={1.75} className="mt-px size-3.5 shrink-0" />
            <span>
              {t.mail.remoteImages(page.data.remote_images)} {t.files.page.remoteImagesWhy}
            </span>
          </p>
          <Button variant="outline" size="xs" onClick={() => setPictures(true)}>
            {t.mail.showRemoteImages}
          </Button>
        </div>
      ) : null}
      <HtmlFrame html={shown.html} body={shown.body} remote={pictures} title={t.files.page.title} />
    </div>
  );
}

/** An XML or text file as text; XML written on one line is laid out a tag to a line. */
function TextPreview({ id }: { id: string }) {
  const { t } = useI18n();
  const file = useQuery({ queryKey: ["document-text", id], queryFn: () => getDocumentText(id), staleTime: Infinity });
  const text = useMemo(() => (file.data ? laidOut(file.data.text) : undefined), [file.data]);

  if (file.isError) return <NoPreview id={id} failed />;
  if (!file.data || text === undefined) return <Skeleton className="m-4 h-64 rounded-xl" />;
  return (
    <div className="grid gap-2 p-3 md:p-4">
      {file.data.truncated ? <p className="text-muted-foreground px-1 text-xs">{t.files.text.truncated}</p> : null}
      <pre
        aria-label={t.files.text.title}
        className="bg-muted/50 overflow-x-auto rounded-xl p-3 font-mono text-xs whitespace-pre-wrap [overflow-wrap:anywhere]"
      >
        {text}
      </pre>
    </div>
  );
}

/**
 * XML laid out a tag to a line, indented by depth, when its tags run together (an e-invoice is often
 * one line). Only the whitespace between tags changes; a value stays on its tag's line. Anything
 * else is shown as it is.
 */
function laidOut(text: string): string {
  if (!text.trimStart().startsWith("<") || !/>\s*</.test(text)) return text;
  const lines: string[] = [];
  let depth = 0;
  for (const line of text.trim().replace(/>\s*</g, ">\n<").split("\n")) {
    if (line.startsWith("</")) depth = Math.max(depth - 1, 0);
    lines.push("  ".repeat(depth) + line);
    // A start tag alone on its line opens a level; one closed on the same line, or self-closed, none.
    if (/^<[^/?!][^>]*>$/.test(line) && !line.endsWith("/>")) depth++;
  }
  return lines.join("\n");
}

/**
 * A file offered as a download: one no viewer takes, one that failed to draw, an archive that
 * could not be opened (with why) or is too deep to be (`notOpened`), or an office file no PDF could
 * be made of.
 */
function NoPreview({
  id,
  failed = false,
  reason = null,
  notOpened = false,
}: {
  id: string;
  failed?: boolean;
  reason?: UnreadableReason | null;
  notOpened?: boolean;
}) {
  const { t, locale } = useI18n();
  const document = useDocument(id);
  const download = useDownload(id);
  const preview = document.data?.preview;
  const title = reason
    ? preview === "office"
      ? t.files.office.notConverted
      : t.files.archive.notOpenable
    : notOpened
      ? t.files.archive.notOpened
      : failed
        ? t.files.failed
        : t.files.noPreview;
  const body = reason ? t.files.reasons[reason] : failed ? null : t.files.noPreviewBody;
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <div className="bg-secondary text-muted-foreground grid size-11 place-items-center rounded-2xl">
        <HugeiconsIcon icon={fileIcon(document.data?.preview)} strokeWidth={1.75} className="size-5" />
      </div>
      <div>
        <p className="text-sm font-medium">{title}</p>
        {body ? <p className="text-muted-foreground mt-1 text-sm">{body}</p> : null}
      </div>
      {document.data ? (
        <Button variant="outline" size="sm" disabled={download.isPending} onClick={() => download.mutate()}>
          <HugeiconsIcon icon={Download04Icon} strokeWidth={1.75} />
          {t.files.download} · {formatBytes(document.data.size, locale)}
        </Button>
      ) : null}
    </div>
  );
}
