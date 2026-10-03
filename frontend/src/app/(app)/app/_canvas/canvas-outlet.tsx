"use client";

import { useCanvasView, useForgetOpenedByLink } from "@/components/canvas-link";
import { DocumentDownload, DocumentTitle, DocumentView } from "@/features/documents/document-view";
import { MailSubject, MailThread } from "@/features/mail/mail-thread";
import { ProjectTitle, ProjectTitleMenu, ProjectView } from "@/features/projects/project-view";
import { CanvasFrame, CanvasPanel } from "@/features/shell/canvas";
import { formatView, type CanvasView } from "@/lib/canvas-view";
import { useI18n } from "@/lib/i18n/client";

/** The canvas, showing the record the address names (`?view=`), beside whichever page is open. */
export function CanvasOutlet({ initialWidth }: { initialWidth: number }) {
  const view = useCanvasView();
  useForgetOpenedByLink(view !== null);
  return (
    <CanvasFrame
      initialWidth={initialWidth}
      content={view ? <CanvasRecord key={formatView(view)} view={view} /> : null}
    />
  );
}

function CanvasRecord({ view }: { view: CanvasView }) {
  const { t } = useI18n();
  switch (view.kind) {
    case "mail":
      return (
        <CanvasPanel label={t.mail.conversation} title={<MailSubject id={view.id} />}>
          <MailThread id={view.id} />
        </CanvasPanel>
      );
    case "document":
      return (
        <CanvasPanel label={t.files.file} title={<DocumentTitle id={view.id} />} actions={<DocumentDownload id={view.id} />}>
          <DocumentView id={view.id} />
        </CanvasPanel>
      );
    case "project":
      return (
        <CanvasPanel label={t.project.title} title={<ProjectTitle id={view.id} />} actions={<ProjectTitleMenu id={view.id} />}>
          <ProjectView id={view.id} />
        </CanvasPanel>
      );
  }
}
