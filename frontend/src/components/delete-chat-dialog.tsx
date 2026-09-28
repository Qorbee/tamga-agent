"use client";

import { useAui } from "@assistant-ui/react";

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
import { useI18n } from "@/lib/i18n/client";

/**
 * Asks before a chat is deleted: the one in scope where the dialog renders, a sidebar row's or the
 * open chat's. Deleting cannot be undone, so it is never a single click.
 */
export function DeleteChatDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const aui = useAui();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{t.chat.deleteChatTitle}</DialogTitle>
          <DialogDescription>{t.chat.deleteChatBody}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">{t.chat.cancel}</Button>
          </DialogClose>
          <Button
            variant="destructive"
            onClick={() => {
              onOpenChange(false);
              aui.threadListItem.delete();
            }}
          >
            {t.chat.delete}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
