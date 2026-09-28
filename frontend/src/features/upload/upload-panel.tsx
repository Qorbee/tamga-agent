"use client";

import { CloudUploadIcon, Download01Icon, File01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type DragEvent, type FormEvent } from "react";

import { Field } from "@/components/field";
import { NativeSelect } from "@/components/native-select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  getDocument,
  listDocuments,
  listDocumentTypes,
  setDocumentType,
  uploadDocument,
  type Document,
  type DocumentType,
} from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import type { Locale } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/** The upload page: a file from the phone or the desk, typed if the owner knows what it is. */
export function UploadPanel() {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState("");
  const [note, setNote] = useState("");
  const [dragging, setDragging] = useState(false);
  const types = useQuery({ queryKey: ["document-types"], queryFn: listDocumentTypes, staleTime: Infinity });
  const upload = useMutation({
    mutationFn: (chosen: File) => uploadDocument(chosen, { docType: docType || undefined, note }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      setFile(null);
      setNote("");
      if (input.current) input.current.value = "";
    },
  });

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files[0];
    if (dropped) setFile(dropped);
  };
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (file) upload.mutate(file);
  };
  const tooLarge = upload.error instanceof ApiError && upload.error.status === 413;

  return (
    <div className="grid w-full max-w-3xl gap-10 px-3 pb-10 md:px-6">
      <form onSubmit={onSubmit} className="grid gap-4">
        <label
          htmlFor="upload-file"
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cn(
            "border-border hover:bg-secondary/60 flex cursor-pointer flex-col items-center gap-2 rounded-2xl border border-dashed px-6 py-10 text-center transition-colors",
            dragging && "bg-secondary",
          )}
        >
          <HugeiconsIcon icon={CloudUploadIcon} strokeWidth={1.75} className="text-muted-foreground size-6" />
          <span className="text-sm font-medium">{file ? file.name : t.upload.drop}</span>
          {file ? (
            <span className="text-muted-foreground text-xs tabular-nums">{kilobytes(file.size, locale)}</span>
          ) : (
            <span className="text-muted-foreground text-xs">{t.upload.choose}</span>
          )}
          <input
            ref={input}
            id="upload-file"
            type="file"
            className="sr-only"
            accept="image/*,application/pdf,.xml,.xlsx,.xls,.csv"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label={t.upload.type} htmlFor="upload-type">
            <NativeSelect id="upload-type" value={docType} onChange={(event) => setDocType(event.target.value)}>
              <option value="">{t.upload.typeUnknown}</option>
              {types.data?.map((type) => (
                <option key={type.key} value={type.key}>
                  {type.label}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label={t.upload.note} htmlFor="upload-note">
            <Input
              id="upload-note"
              maxLength={1000}
              placeholder={t.upload.notePlaceholder}
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={!file || upload.isPending}>
            {upload.isPending ? t.upload.sending : t.upload.send}
          </Button>
          {upload.isSuccess ? (
            <p className="text-success-foreground text-sm" role="status">
              {upload.data.new_document
                ? t.upload.stored(upload.data.filename)
                : t.upload.duplicate(upload.data.filename)}
            </p>
          ) : upload.isError ? (
            <p className="text-danger-foreground text-sm" role="alert">
              {tooLarge ? t.upload.tooLarge : t.errors.generic}
            </p>
          ) : null}
        </div>
      </form>

      <RecentDocuments types={types.data ?? []} />
    </div>
  );
}

function kilobytes(bytes: number, locale: Locale): string {
  return `${formatNumber(Math.max(1, Math.round(bytes / 1024)), locale)} KB`;
}

function RecentDocuments({ types }: { types: DocumentType[] }) {
  const { t } = useI18n();
  const documents = useQuery({ queryKey: ["documents"], queryFn: () => listDocuments({ limit: 50 }) });

  return (
    <section className="grid gap-3">
      <h2 className="text-sm font-medium">{t.upload.recent}</h2>
      {documents.isPending ? (
        <Skeleton className="h-24 rounded-2xl" />
      ) : documents.isError ? (
        <p className="text-danger-foreground text-sm" role="alert">
          {t.errors.load}
        </p>
      ) : documents.data.items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t.upload.none}</p>
      ) : (
        <ul className="divide-border border-border divide-y rounded-2xl border">
          {documents.data.items.map((document) => (
            <DocumentRow key={document.id} document={document} types={types} />
          ))}
        </ul>
      )}
    </section>
  );
}

function DocumentRow({ document, types }: { document: Document; types: DocumentType[] }) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const retype = useMutation({
    mutationFn: (docType: string | null) => setDocumentType(document.id, docType),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
  });
  const download = useMutation({
    // The link is signed for five minutes, so it is fetched when asked for, not with the list.
    mutationFn: () => getDocument(document.id),
    onSuccess: (detail) => window.open(detail.download_url, "_blank", "noopener"),
  });

  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3">
      <HugeiconsIcon icon={File01Icon} strokeWidth={1.75} className="text-muted-foreground size-4" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm">{document.filename}</p>
        <p className="text-muted-foreground text-xs tabular-nums">
          {formatDate(new Date(document.created_at), locale, "dateTime")} ·{" "}
          {kilobytes(document.size, locale)}
        </p>
      </div>
      <NativeSelect
        aria-label={t.upload.setType}
        className="h-8 w-44"
        value={document.doc_type ?? ""}
        disabled={retype.isPending}
        onChange={(event) => retype.mutate(event.target.value || null)}
      >
        <option value="">{t.upload.typeUnknown}</option>
        {types.map((type) => (
          <option key={type.key} value={type.key}>
            {type.label}
          </option>
        ))}
      </NativeSelect>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={t.upload.download}
        disabled={download.isPending}
        onClick={() => download.mutate()}
      >
        <HugeiconsIcon icon={Download01Icon} strokeWidth={1.75} />
      </Button>
    </li>
  );
}
