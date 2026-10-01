"use client";

import { useMutation } from "@tanstack/react-query";

import { ProjectPicker } from "@/components/project-picker";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { decidePlacement, type Placement } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { conversationName } from "@/lib/mail-text";
import { useInvalidatePlacements } from "@/lib/use-invalidate-placements";

/**
 * The owner moving a conversation to another project: its mails are tied there, confirmed, and
 * what tied them to the project it was in is taken back. Moving it back undoes it, so the tap on
 * a project is the move.
 */
export function MoveConversationDialog({
  placement,
  open,
  onOpenChange,
}: {
  placement: Placement;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const invalidate = useInvalidatePlacements();
  const move = useMutation({
    mutationFn: (projectId: string) =>
      decidePlacement(placement.id, { project_id: projectId, from_project_id: placement.project_id }),
    onSuccess: () => {
      invalidate();
      onOpenChange(false);
    },
  });
  const subject = placement.conversation?.subject;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) move.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.project.moveTitle}</DialogTitle>
          <DialogDescription>{subject ? conversationName(subject) : t.inbox.noSubject}</DialogDescription>
        </DialogHeader>
        {open ? (
          <ProjectPicker
            exclude={placement.project_id ? [placement.project_id] : []}
            disabled={move.isPending}
            onPick={(project) => move.mutate(project.id)}
          />
        ) : null}
        {move.isError ? (
          <p className="text-danger-foreground text-sm" role="alert">
            {t.errors.generic}
          </p>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
