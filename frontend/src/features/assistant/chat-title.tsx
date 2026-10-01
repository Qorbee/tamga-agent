"use client";

import { useAui, useAuiState } from "@assistant-ui/react";
import { Archive02Icon, ArrowDown01Icon, Delete02Icon, PencilEdit02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useRef, useState } from "react";

import { DeleteChatDialog } from "@/components/delete-chat-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/lib/i18n/client";

/** The open chat's name in the page bar, with a menu to rename it in place or delete it. */
export function ChatTitle() {
  const { t } = useI18n();
  const aui = useAui();
  const title = useAuiState((s) => s.threadListItem.title) ?? t.chat.newChat;
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Set when a menu item takes focus elsewhere (the name field, the dialog), so the closing menu
  // does not pull focus back to its trigger.
  const keepFocus = useRef(false);

  if (renaming) return <RenameField title={title} onDone={() => setRenaming(false)} />;

  return (
    <>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            className="-ms-2 h-8 max-w-full min-w-0 gap-1 rounded-lg px-2 text-sm font-medium"
          >
            <span className="truncate">{title}</span>
            <HugeiconsIcon icon={ArrowDown01Icon} strokeWidth={1.75} className="text-muted-foreground" />
            <span className="sr-only">{t.chat.chatOptions}</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="w-auto"
          onCloseAutoFocus={(event) => {
            if (keepFocus.current) event.preventDefault();
            keepFocus.current = false;
          }}
        >
          <DropdownMenuItem
            onSelect={() => {
              keepFocus.current = true;
              setRenaming(true);
            }}
          >
            <HugeiconsIcon icon={PencilEdit02Icon} strokeWidth={1.75} />
            {t.chat.rename}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void aui.threadListItem.archive()}>
            <HugeiconsIcon icon={Archive02Icon} strokeWidth={1.75} />
            {t.chat.archive}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => {
              keepFocus.current = true;
              setDeleting(true);
            }}
          >
            <HugeiconsIcon icon={Delete02Icon} strokeWidth={1.75} />
            {t.chat.delete}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <DeleteChatDialog open={deleting} onOpenChange={setDeleting} />
    </>
  );
}

/** The name as a field: Enter or leaving it saves, Escape keeps the old name. */
function RenameField({ title, onDone }: { title: string; onDone: () => void }) {
  const { t } = useI18n();
  const aui = useAui();
  const [value, setValue] = useState(title);
  const settled = useRef(false);

  const inputRef = useRef<HTMLInputElement>(null);

  const finish = (save: boolean) => {
    if (settled.current) return;
    settled.current = true;
    const next = value.trim();
    if (!save || !next || next === title) {
      onDone();
      return;
    }
    // The field stays, with what was typed, until the server has the name: a rename that
    // failed is there to try again.
    Promise.resolve()
      .then(() => aui.threadListItem.rename(next))
      .then(onDone, () => {
        settled.current = false;
        inputRef.current?.focus();
      });
  };

  return (
    <Input
      ref={inputRef}
      autoFocus
      aria-label={t.chat.renameThread}
      value={value}
      onFocus={(event) => event.currentTarget.select()}
      onChange={(event) => setValue(event.target.value)}
      onBlur={() => finish(true)}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          finish(true);
        } else if (event.key === "Escape") {
          event.preventDefault();
          finish(false);
        }
      }}
      className="-ms-2 h-8 w-full max-w-md rounded-lg px-2 text-sm font-medium"
    />
  );
}
