"use client";

import { Download04Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { fileIcon } from "@/components/file-icon";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDownload } from "@/hooks/use-download";
import { useObjectUrl } from "@/hooks/use-object-url";
import { getDocument, getDocumentFile, getDocumentSheets, type UnreadableReason } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

import { ArchiveView, WAITING_POLL } from "./archive-view";
import { DocumentMenu } from "./document-menu";
import { ImageView } from "./image-view";
import { PdfView } from "./pdf-view";
import { SheetView } from "./sheet-view";

function useDocument(id: string) {
  return useQuery({
    queryKey: ["document", id],
    queryFn: () => getDocument(id),
    // An archive waiting to be opened is asked again until the worker has read it.
    refetchInterval: (query) =>
      query.state.data?.preview === "archive" && query.state.data.read_state === "pending" ? WAITING_POLL : false,
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

function DrawnPreview({ id, preview, name }: { id: string; preview: "image" | "pdf"; name: string }) {
  const file = useQuery({
    queryKey: ["document-file", id],
    queryFn: () => getDocumentFile(id),
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

/**
 * A file offered as a download: one no viewer takes, one that failed to draw, or an archive that
 * could not be opened (with why) or is too deep to be (`notOpened`).
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
  const title = reason ? t.files.archive.notOpenable : notOpened ? t.files.archive.notOpened : failed ? t.files.failed : t.files.noPreview;
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
