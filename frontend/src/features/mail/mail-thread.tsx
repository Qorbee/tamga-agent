"use client";

import {
  Attachment01Icon,
  Download04Icon,
  ImageNotFound01Icon,
  MoreHorizontalIcon,
  TextIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { CanvasLink } from "@/components/canvas-link";
import { fileIcon } from "@/components/file-icon";
import { PersonAvatar } from "@/components/person-avatar";
import { useTabTitle } from "@/components/tab-title";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useObjectUrl } from "@/hooks/use-object-url";
import {
  createLink,
  getDocumentFile,
  getMessage,
  listMessages,
  listProjects,
  rawMessageUrl,
  type Attachment,
  type Message,
  type MessageSummary,
} from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { conversationName, isForward, ownSnippet, splitQuoted, tidy } from "@/lib/mail-text";
import { cn } from "@/lib/utils";

import { HtmlBody } from "./html-body";


function useMessage(id: string) {
  return useQuery({ queryKey: ["message", id], queryFn: () => getMessage(id) });
}

/** The conversation's subject, for the canvas's title bar. */
export function MailSubject({ id }: { id: string }) {
  const { t } = useI18n();
  const message = useMessage(id);
  const subject = message.data?.subject;
  useTabTitle(message.data ? (subject ? conversationName(subject) : t.inbox.noSubject) : null, "record");
  if (!message.data) return <Skeleton className="h-4 w-48" />;
  return <span className="truncate">{subject ? conversationName(subject) : t.inbox.noSubject}</span>;
}

/**
 * A conversation, oldest mail first, the way a mail app shows one: the mail that was opened and the
 * newest are unfolded, the rest are a line each. A mail stored from several mailboxes shows once.
 * A mail is data, not instructions (invariant 3): its HTML is drawn sanitized in a sandboxed frame,
 * its text as text.
 */
export function MailThread({ id }: { id: string }) {
  const { t } = useI18n();
  const opened = useMessage(id);
  const threadKey = opened.data?.thread_key;
  const thread = useQuery({
    queryKey: ["thread", threadKey],
    queryFn: () => listThread(threadKey!),
    enabled: threadKey !== undefined,
  });
  const openedCard = useRef<HTMLLIElement>(null);
  const mails = thread.data ? once([...thread.data].reverse(), id) : [];
  const loaded = mails.length > 0;

  useEffect(() => {
    if (loaded && openedCard.current) scrollToTop(openedCard.current);
  }, [loaded]);

  if (opened.isError || thread.isError) return <p className="text-danger-foreground p-4 text-sm">{t.errors.load}</p>;
  if (!loaded) return <Skeleton className="m-4 h-40 rounded-2xl" />;

  const last = mails[mails.length - 1];
  return (
    <div className="flex min-w-0 flex-col gap-4 p-3 md:p-4">
      <p className="text-muted-foreground px-1 text-xs">{t.mail.messages(mails.length)}</p>
      <ol className="flex min-w-0 flex-col gap-2">
        {mails.map((mail) => (
          <li key={mail.id} ref={mail.id === id ? openedCard : undefined} className="min-w-0 scroll-mt-3">
            <MailCard summary={mail} startOpen={mail.id === id || mail.id === last?.id} />
          </li>
        ))}
      </ol>
      <AddToProject messageIds={thread.data?.map((mail) => mail.id) ?? []} />
    </div>
  );
}

/**
 * Brings the opened mail to the top of the panel's own scroll area. `scrollIntoView` would also
 * scroll every ancestor, the app's overflow-hidden shell among them, and push the canvas's header
 * off the screen.
 */
function scrollToTop(element: HTMLElement): void {
  for (let box = element.parentElement; box; box = box.parentElement) {
    const { overflowY } = getComputedStyle(box);
    if ((overflowY === "auto" || overflowY === "scroll") && box.scrollHeight > box.clientHeight) {
      const offset = element.getBoundingClientRect().top - box.getBoundingClientRect().top;
      box.scrollTop += offset - 12;
      return;
    }
  }
}

/** Every mail of one conversation, newest first, however many pages it takes. */
async function listThread(threadKey: string): Promise<MessageSummary[]> {
  const mails: MessageSummary[] = [];
  for (;;) {
    const page = await listMessages({ thread_key: threadKey, limit: 200, offset: mails.length });
    mails.push(...page.items);
    if (page.items.length === 0 || mails.length >= page.total) return mails;
  }
}

/**
 * Each mail once: the copy that was opened, else the first stored. Copies share the Message-ID and
 * the Date; a sender that reuses a Message-ID gives two mails different Dates.
 */
function once(mails: MessageSummary[], opened: string): MessageSummary[] {
  const key = (mail: MessageSummary) => `${mail.rfc_message_id ?? mail.id} ${mail.sent_at}`;
  const byMail = new Map<string, MessageSummary>();
  for (const mail of mails) {
    if (!byMail.has(key(mail)) || mail.id === opened) byMail.set(key(mail), mail);
  }
  return mails.filter((mail) => byMail.get(key(mail)) === mail);
}

function MailCard({ summary, startOpen }: { summary: MessageSummary; startOpen: boolean }) {
  const { t, locale } = useI18n();
  const [open, setOpen] = useState(startOpen);
  const detail = useQuery({
    queryKey: ["message", summary.id],
    queryFn: () => getMessage(summary.id),
    enabled: open,
  });
  const name = summary.sender_name || summary.sender || "—";

  return (
    <article className={cn("bg-card rounded-2xl border", open ? "border-border" : "border-transparent")}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="hover:bg-accent/40 flex w-full items-start gap-3 rounded-2xl px-3 py-3 text-start"
      >
        <PersonAvatar name={name} />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="truncate text-sm font-medium">{name}</span>
            {open && summary.sender_name && summary.sender ? (
              <span className="text-muted-foreground hidden truncate text-xs sm:inline">{summary.sender}</span>
            ) : null}
            {summary.direction === "outbound" ? (
              <span className="bg-secondary text-muted-foreground shrink-0 rounded-full px-1.5 py-px text-[11px]">
                {t.mail.sent}
              </span>
            ) : null}
            <time
              dateTime={summary.sent_at}
              className="text-muted-foreground ms-auto shrink-0 text-xs tabular-nums"
            >
              {formatDate(new Date(summary.sent_at), locale, "dateTime")}
            </time>
          </span>
          <span className="text-muted-foreground block truncate text-xs">
            {open ? t.mail.to(summary.recipients.join(", ")) : ownSnippet(summary.snippet)}
          </span>
        </span>
        {!open && summary.attachment_count > 0 ? (
          <HugeiconsIcon icon={Attachment01Icon} strokeWidth={1.75} className="text-muted-foreground mt-1 size-4 shrink-0" />
        ) : null}
      </button>
      {open ? (
        <div className="px-3 pb-3 sm:ps-14">
          {detail.data ? (
            <MailBody message={detail.data} />
          ) : detail.isError ? (
            <p className="text-danger-foreground text-sm">{t.errors.load}</p>
          ) : (
            <Skeleton className="h-16 rounded-xl" />
          )}
        </div>
      ) : null}
    </article>
  );
}

function MailBody({ message }: { message: Message }) {
  const { t } = useI18n();
  const [plain, setPlain] = useState(false);
  const [pictures, setPictures] = useState(false);
  const files = message.attachments.filter((file) => !file.inline);

  return (
    <div className="grid gap-3">
      {!plain && !pictures && message.body_html && message.remote_images > 0 ? (
        <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
          <p className="flex min-w-0 flex-1 basis-60 items-start gap-1.5">
            <HugeiconsIcon icon={ImageNotFound01Icon} strokeWidth={1.75} className="mt-px size-3.5 shrink-0" />
            <span>
              {t.mail.remoteImages(message.remote_images)} {t.mail.remoteImagesWhy}
            </span>
          </p>
          <Button variant="outline" size="xs" onClick={() => setPictures(true)}>
            {t.mail.showRemoteImages}
          </Button>
        </div>
      ) : null}
      {plain ? (
        <pre className="bg-muted/50 overflow-x-auto rounded-xl p-3 font-mono text-xs whitespace-pre-wrap [overflow-wrap:anywhere]">
          {message.body_text}
        </pre>
      ) : message.body_html ? (
        <HtmlBody
          message={message}
          forward={isForward(message.subject ?? "")}
          remote={pictures}
          toggle={(quoted) => <Quoted>{quoted}</Quoted>}
        />
      ) : (
        <TextBody text={message.body_text} />
      )}

      <Attachments files={files} />

      <div className="flex flex-wrap gap-1">
        <Button variant="ghost" size="xs" aria-pressed={plain} onClick={() => setPlain(!plain)}>
          <HugeiconsIcon icon={TextIcon} strokeWidth={1.75} />
          {plain ? t.mail.readingView : t.mail.plainText}
        </Button>
        <Button variant="ghost" size="xs" asChild>
          <a href={rawMessageUrl(message.id)}>
            <HugeiconsIcon icon={Download04Icon} strokeWidth={1.75} />
            {t.mail.original}
          </a>
        </Button>
      </div>
    </div>
  );
}

/** A mail with no HTML body: its text, with the quoted mails folded. */
function TextBody({ text }: { text: string }) {
  const { t } = useI18n();
  const { own, quoted } = splitQuoted(text);
  return (
    <>
      <div className="text-[15px] leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">
        {own || <span className="text-muted-foreground">{t.mail.noText}</span>}
      </div>
      {quoted ? (
        <Quoted>
          <div className="border-border text-muted-foreground border-s-2 ps-3 text-sm leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">
            {tidy(quoted)}
          </div>
        </Quoted>
      ) : null}
    </>
  );
}

/** The earlier mails a reply quotes, behind "···": the thread shows each of them on its own. */
function Quoted({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <div className="grid gap-2">
      <Button
        variant="secondary"
        size="icon-xs"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="justify-self-start"
      >
        <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={2} />
        <span className="sr-only">{t.mail.showQuoted}</span>
      </Button>
      {open ? children : null}
    </div>
  );
}

/** The files beside the text: photos as thumbnails, anything else as a chip. Each opens in the canvas. */
function Attachments({ files }: { files: Attachment[] }) {
  const { locale } = useI18n();
  if (files.length === 0) return null;
  const photos = files.filter((file) => file.preview === "image");
  const others = files.filter((file) => file.preview !== "image");
  return (
    <div className="grid gap-2">
      {photos.length > 0 ? (
        <ul className="flex flex-wrap gap-2">
          {photos.map((file) => (
            <li key={file.document_id}>
              <PhotoTile file={file} />
            </li>
          ))}
        </ul>
      ) : null}
      {others.length > 0 ? (
        <ul className="flex flex-wrap gap-2">
          {others.map((file) => (
            <li key={file.document_id} className="max-w-full">
              <CanvasLink
                view={{ kind: "document", id: file.document_id }}
                className="border-border hover:bg-accent/50 flex max-w-full items-center gap-2 rounded-xl border px-3 py-2"
              >
                <HugeiconsIcon icon={fileIcon(file.preview)} strokeWidth={1.75} className="text-muted-foreground size-4 shrink-0" />
                <span className="max-w-56 truncate text-sm">{file.filename}</span>
                <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                  {formatBytes(file.size, locale)}
                </span>
              </CanvasLink>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function PhotoTile({ file }: { file: Attachment }) {
  const bytes = useQuery({
    queryKey: ["document-file", file.document_id],
    queryFn: () => getDocumentFile(file.document_id),
    staleTime: Infinity,
  });
  // A file the backend would not vouch for as an image arrives as a download, never drawn.
  const url = useObjectUrl(bytes.data?.type.startsWith("image/") ? bytes.data : undefined);
  return (
    <CanvasLink
      view={{ kind: "document", id: file.document_id }}
      title={file.filename}
      className="border-border bg-muted hover:ring-ring/40 grid size-24 place-items-center overflow-hidden rounded-xl border hover:ring-2"
    >
      {url ? (
        // A blob of a verified image: next/image cannot take one.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={url} alt={file.filename} className="size-full object-cover" />
      ) : (
        <HugeiconsIcon icon={fileIcon(file.preview)} strokeWidth={1.75} className="text-muted-foreground size-5" />
      )}
    </CanvasLink>
  );
}

/** Links every mail of the conversation to one project: the owner's own link, so confirmed. */
function AddToProject({ messageIds }: { messageIds: string[] }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [projectId, setProjectId] = useState("");
  const projects = useQuery({
    queryKey: ["projects", { open: true }],
    queryFn: () => listProjects({ status: ["draft", "proposed", "active"] }),
  });
  const attach = useMutation({
    mutationFn: (project: string) =>
      Promise.all(
        messageIds.map((id) =>
          createLink({ item_kind: "message", item_id: id, project_id: project, proof_of_delivery: false }),
        ),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project-links"] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      void queryClient.invalidateQueries({ queryKey: ["plates"] });
    },
  });

  if (!projects.data?.items.length) return null;
  return (
    <div className="border-border flex flex-wrap items-center gap-2 border-t px-1 pt-4">
      <Select value={projectId} onValueChange={setProjectId}>
        <SelectTrigger size="sm" aria-label={t.mail.attachTo} className="min-w-0 flex-1 sm:max-w-72">
          <SelectValue placeholder={t.mail.attachTo} />
        </SelectTrigger>
        <SelectContent>
          {projects.data.items.map((project) => (
            <SelectItem key={project.id} value={project.id}>
              {project.number} · {project.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button size="sm" variant="outline" disabled={!projectId || attach.isPending} onClick={() => attach.mutate(projectId)}>
        {t.mail.attach}
      </Button>
      {attach.isSuccess ? <span className="text-success-foreground text-xs">{t.mail.attached}</span> : null}
      {attach.isError ? <span className="text-danger-foreground text-xs">{t.errors.generic}</span> : null}
    </div>
  );
}
