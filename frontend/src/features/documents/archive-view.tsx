"use client";

import { Folder01Icon, LockPasswordIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQuery } from "@tanstack/react-query";

import { CanvasLink } from "@/components/canvas-link";
import { fileIcon } from "@/components/file-icon";
import { Skeleton } from "@/components/ui/skeleton";
import { listDocumentParts, type DocumentDetail, type DocumentPart, type Preview } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";

// How often a file waiting for the worker (an archive to open, a PDF to make) asks again; the
// worker reads it within a minute or two.
export const WAITING_POLL = 5000;

/** A file the worker has yet to open or convert, said plainly while the view asks again. */
export function WaitingView({ preview, title, body }: { preview: Preview; title: string; body: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <div className="bg-secondary text-muted-foreground grid size-11 animate-pulse place-items-center rounded-2xl">
        <HugeiconsIcon icon={fileIcon(preview)} strokeWidth={1.75} className="size-5" />
      </div>
      <p className="text-sm font-medium">{title}</p>
      <p className="text-muted-foreground text-sm">{body}</p>
    </div>
  );
}

/**
 * An archive or a winmail.dat as a folder: the files taken out of it, grouped by the folders they
 * sat in, each opening in the canvas with the way back here. Its files are not in the upload list:
 * they are reached from here, as a mail's are from the mail.
 */
export function ArchiveView({ document, waiting }: { document: DocumentDetail; waiting: boolean }) {
  const { t } = useI18n();
  const parts = useQuery({
    queryKey: ["document-parts", document.id, document.read_state],
    queryFn: () => listDocumentParts(document.id),
    enabled: document.read_state === "read",
  });

  if (waiting) {
    return <WaitingView preview="archive" title={t.files.archive.opening} body={t.files.archive.openingBody} />;
  }
  if (parts.isError) return <p className="text-danger-foreground p-4 text-sm">{t.errors.load}</p>;
  if (!parts.data) return <Skeleton className="m-4 h-64 rounded-xl" />;
  if (parts.data.length === 0) return <p className="text-muted-foreground p-4 text-sm">{t.files.archive.empty}</p>;

  return (
    <div className="grid gap-4 p-3 md:p-4">
      <p className="text-muted-foreground px-1 text-xs">
        {t.files.archive.count(parts.data.length)}
        {document.truncated ? ` · ${t.files.archive.truncated}` : null}
      </p>
      {groups(parts.data).map(([folder, files]) => (
        <section key={folder} className="grid gap-1">
          {folder ? (
            <h3 className="text-muted-foreground flex items-center gap-1.5 px-1 text-xs font-medium">
              <HugeiconsIcon icon={Folder01Icon} strokeWidth={1.75} className="size-3.5" />
              <span className="truncate">{folder}</span>
            </h3>
          ) : null}
          <ul className="border-border divide-border divide-y rounded-xl border">
            {files.map((part) => (
              <li key={part.position}>
                <PartRow part={part} />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function PartRow({ part }: { part: DocumentPart }) {
  const { t, locale } = useI18n();
  const name = part.name.split("/").at(-1) || part.name;
  const size = <span className="text-muted-foreground shrink-0 text-xs tabular-nums">{formatBytes(part.size, locale)}</span>;

  if (!part.document) {
    return (
      <div className="text-muted-foreground flex items-center gap-3 px-3 py-2.5" title={name}>
        <HugeiconsIcon icon={LockPasswordIcon} strokeWidth={1.75} className="size-4 shrink-0" />
        <span className="min-w-0 flex-1 truncate text-sm">{name}</span>
        <span className="shrink-0 text-xs">{part.unreadable_reason ? t.files.reasons[part.unreadable_reason] : null}</span>
      </div>
    );
  }
  return (
    <CanvasLink
      view={{ kind: "document", id: part.document.id }}
      className="hover:bg-accent/50 flex items-center gap-3 px-3 py-2.5 first:rounded-t-xl last:rounded-b-xl"
    >
      <HugeiconsIcon icon={fileIcon(part.document.preview)} strokeWidth={1.75} className="text-muted-foreground size-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate text-sm">{name}</span>
      {size}
    </CanvasLink>
  );
}

/** The files by the folder they sat in, folders in the order their first file came, the top first. */
function groups(parts: DocumentPart[]): [string, DocumentPart[]][] {
  const byFolder = new Map<string, DocumentPart[]>([["", []]]);
  for (const part of parts) {
    const folder = part.name.includes("/") ? part.name.slice(0, part.name.lastIndexOf("/")) : "";
    byFolder.set(folder, [...(byFolder.get(folder) ?? []), part]);
  }
  return [...byFolder].filter(([, files]) => files.length > 0);
}
