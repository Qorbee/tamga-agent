"use client";

import { ArrowDown01Icon, Delete02Icon, Download04Icon, PencilEdit02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { useCanvasBack, useCloseCanvas } from "@/components/canvas-link";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { useDownload } from "@/hooks/use-download";
import { ApiError, deleteDocument, renameDocument, type DocumentDetail } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";

/**
 * The open file's name in the panel's title, with a menu: what it is, rename it in place, download
 * it, and delete it when only uploads keep it. A file a mail brought stays with the mail, under the
 * name it came with.
 */
export function DocumentMenu({ document }: { document: DocumentDetail }) {
  const { t, locale } = useI18n();
  const download = useDownload(document.id);
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Set when a menu item takes focus elsewhere (the name field, the dialog), so the closing menu
  // does not pull focus back to its trigger.
  const keepFocus = useRef(false);
  // What named the file: the name it came with is the sender's, and the backend refuses another.
  // Its note leads, since it explains both items it turns off.
  const named = document.held_by.find((holder) => holder === "mail");
  const kept = named ?? document.held_by[0];

  if (renaming) return <RenameField document={document} onDone={() => setRenaming(false)} />;

  return (
    <>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="-ms-2 h-8 max-w-full min-w-0 gap-1 rounded-lg px-2 text-sm font-medium">
            <span className="truncate">{document.filename}</span>
            {document.preview ? (
              <span className="text-muted-foreground shrink-0 font-normal">· {t.files.kinds[document.preview]}</span>
            ) : null}
            <HugeiconsIcon icon={ArrowDown01Icon} strokeWidth={1.75} className="text-muted-foreground" />
            <span className="sr-only">{t.files.menu.options}</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="w-64"
          onCloseAutoFocus={(event) => {
            if (keepFocus.current) event.preventDefault();
            keepFocus.current = false;
          }}
        >
          <DropdownMenuLabel className="grid gap-0.5 font-normal">
            <span className="text-muted-foreground text-xs tabular-nums">
              {[document.preview ? t.files.kinds[document.preview] : null, formatBytes(document.size, locale)]
                .filter(Boolean)
                .join(" · ")}
            </span>
            <span className="text-muted-foreground text-xs">
              {t.files.menu.added(formatDate(new Date(document.created_at), locale, "dateTime"))}
            </span>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={named !== undefined}
            onSelect={() => {
              keepFocus.current = true;
              setRenaming(true);
            }}
          >
            <HugeiconsIcon icon={PencilEdit02Icon} strokeWidth={1.75} />
            {t.files.menu.rename}
          </DropdownMenuItem>
          <DropdownMenuItem disabled={download.isPending} onSelect={() => download.mutate()}>
            <HugeiconsIcon icon={Download04Icon} strokeWidth={1.75} />
            {t.files.download}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            disabled={kept !== undefined}
            onSelect={() => {
              keepFocus.current = true;
              setDeleting(true);
            }}
          >
            <HugeiconsIcon icon={Delete02Icon} strokeWidth={1.75} />
            {t.files.menu.delete}
          </DropdownMenuItem>
          {kept ? <p className="text-muted-foreground px-2 pb-1.5 text-xs">{t.files.menu.kept[kept]}</p> : null}
        </DropdownMenuContent>
      </DropdownMenu>
      <DeleteDocumentDialog document={document} open={deleting} onOpenChange={setDeleting} />
    </>
  );
}

/** The last dot starts the extension, as the backend reads it; a leading dot starts none. */
function splitName(filename: string): [string, string] {
  const dot = filename.lastIndexOf(".");
  const slash = filename.lastIndexOf("/");
  return dot > slash + 1 ? [filename.slice(0, dot), filename.slice(dot)] : [filename, ""];
}

/**
 * The name, without its extension, as a field: Enter or leaving it saves, Escape keeps the old name.
 * A name the backend refuses stays in the field, marked, until it is changed or given up.
 */
function RenameField({ document, onDone }: { document: DocumentDetail; onDone: () => void }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [stem, extension] = splitName(document.filename);
  const [value, setValue] = useState(stem);
  const rename = useMutation({
    mutationFn: (filename: string) => renameDocument(document.id, filename),
    onSuccess: (renamed) => {
      queryClient.setQueryData<DocumentDetail>(["document", document.id], (old) => old && { ...old, ...renamed });
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      onDone();
    },
    // Refused, a mail has brought the same file since the menu was opened: the menu learns it.
    onError: () => queryClient.invalidateQueries({ queryKey: ["document", document.id] }),
  });

  const finish = (save: boolean) => {
    if (rename.isPending) return;
    const next = value.trim();
    if (!save || !next || next === stem) onDone();
    else rename.mutate(next + extension);
  };

  return (
    <div className="-ms-2 flex w-full max-w-md min-w-0 items-center gap-1">
      <Input
        autoFocus
        aria-label={t.files.menu.renameField}
        aria-invalid={rename.isError || undefined}
        title={rename.isError ? t.errors.generic : undefined}
        value={value}
        disabled={rename.isPending}
        onFocus={(event) => event.currentTarget.select()}
        onChange={(event) => {
          setValue(event.target.value);
          if (rename.isError) rename.reset();
        }}
        onBlur={() => {
          if (!rename.isError) finish(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            finish(true);
          } else if (event.key === "Escape") {
            event.preventDefault();
            finish(false);
          }
        }}
        className="h-8 min-w-0 flex-1 rounded-lg px-2 text-sm font-medium"
      />
      {extension ? <span className="text-muted-foreground shrink-0 text-sm">{extension}</span> : null}
    </div>
  );
}

/** Asks before an uploaded file is deleted: it cannot be undone, so it is never a single click. */
function DeleteDocumentDialog({
  document,
  open,
  onOpenChange,
}: {
  document: DocumentDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const back = useCanvasBack();
  const close = useCloseCanvas();
  const remove = useMutation({
    mutationFn: () => deleteDocument(document.id),
    onSuccess: () => {
      onOpenChange(false);
      if (back) back.go();
      else close();
      queryClient.removeQueries({ queryKey: ["document", document.id] });
      queryClient.removeQueries({ queryKey: ["document-file", document.id] });
      queryClient.removeQueries({ queryKey: ["document-sheets", document.id] });
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    // Refused, it is kept by something since the menu was opened: the menu learns what.
    onError: () => queryClient.invalidateQueries({ queryKey: ["document", document.id] }),
  });
  const refused = remove.error instanceof ApiError && remove.error.status === 409;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) remove.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{t.files.menu.deleteTitle(document.filename)}</DialogTitle>
          <DialogDescription>{t.files.menu.deleteBody}</DialogDescription>
        </DialogHeader>
        {remove.isError ? (
          <p className="text-danger-foreground text-sm" role="alert">
            {refused ? t.files.menu.deleteRefused : t.errors.generic}
          </p>
        ) : null}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">{t.chat.cancel}</Button>
          </DialogClose>
          <Button variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate()}>
            {t.files.menu.delete}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
