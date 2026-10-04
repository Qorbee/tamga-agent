"use client";

import { HttpAgent } from "@ag-ui/client";
import {
  AssistantRuntimeProvider,
  fromThreadMessageLike,
  generateId,
  useAui,
  useAuiState,
  type AssistantRuntime,
  type ThreadMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  fromAgUiMessages,
  useAgUiRuntime,
  type UseAgUiThreadListAdapter,
} from "@assistant-ui/react-ag-ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  ApiError,
  createChat,
  deleteChat,
  getAssistant,
  getChatMessages,
  markChatRead,
  runChat,
  stopChatRun,
  updateChat,
  type Assistant,
  type ChatMessages,
  type ChatPage,
} from "@/lib/api";
import type { Dictionary } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { useInvalidatePlacements } from "@/lib/use-invalidate-placements";
import {
  CHATS_KEY,
  chatMessagesKey,
  chatTitle,
  useArchivedChats,
  useChats,
  useInvalidateChats,
} from "@/lib/use-chats";

import { ChatCards } from "./cards";
import { ASSISTANT_KEY, chosen, readChoice } from "./run-choice";

/** How often a chat's messages are read again while Tamga answers in it for someone else. */
const WHILE_ANSWERING = 3_000;

type Failures = Dictionary["assistant"]["errors"];

/**
 * What a chat session holds outside React's render, for the handlers that run between renders:
 * the protocol's request and the runtime's callbacks.
 */
class Session {
  /** The open chat. */
  chatId: string;
  /** Since when the messages on screen are known to be the server's: only a newer copy replaces them. */
  currentSince = 0;
  /** Whether the chat page is showing: an answer that ends on a page nobody looks at stays unread. */
  watched = false;
  /** Asks in the open chat: the chat protocol's client, sending through the API client. */
  readonly agent: HttpAgent;
  /** The chats the server is known to hold; any other is opened there with its first message. */
  private readonly stored = new Set<string>();
  private failures: Failures;
  private readonly asked: () => void;
  /** What the server offers to ask with; `fresh` reads it again instead of the copy at hand. */
  private readonly offered: (fresh: boolean) => Promise<Assistant>;

  constructor(
    chatId: string,
    failures: Failures,
    asked: () => void,
    offered: (fresh: boolean) => Promise<Assistant>,
  ) {
    this.chatId = chatId;
    this.failures = failures;
    this.asked = asked;
    this.offered = offered;
    // The url is not used: `ask` sends to the open chat.
    this.agent = new HttpAgent({ url: "/chats", fetch: (_url, init) => this.ask(init) });
  }

  private async ask(init: RequestInit): Promise<Response> {
    const chatId = this.chatId;
    try {
      if (!this.stored.has(chatId)) {
        await createChat({ id: chatId, title: "" });
        this.stored.add(chatId);
      }
      // The question goes with the model and effort the picker shows: what the viewer chose,
      // where the server still offers it. The protocol carries what a client adds in
      // `forwardedProps`; the server checks it against its own settings.
      const send = async (fresh: boolean) => {
        const choice = chosen(await this.offered(fresh), readChoice());
        const body = typeof init.body === "string" ? { ...JSON.parse(init.body), forwardedProps: choice } : null;
        return runChat(chatId, body ? { ...init, body: JSON.stringify(body) } : init);
      };
      let response: Response;
      try {
        response = await send(false);
      } catch (error) {
        // The server's settings changed since the offer was read, and it refused the choice
        // before storing anything: the offer is read again, and the question sent once more.
        if (!(error instanceof ApiError && error.field === "forwardedProps")) throw error;
        response = await send(true);
      }
      // The question is stored and names a new chat: the list shows it while Tamga answers.
      this.asked();
      return response;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new Error(failure(error, this.failures), { cause: error });
    }
  }

  speak(failures: Failures) {
    this.failures = failures;
  }

  /** Another chat is on screen, and its messages are on their way. */
  open(chatId: string, stored: boolean) {
    this.chatId = chatId;
    if (stored) this.stored.add(chatId);
    this.currentSince = Number.POSITIVE_INFINITY;
  }

  /** What is on screen now is what the server held at ``since``. */
  settle(since = Date.now()) {
    this.currentSince = since;
  }

  watch(shown: boolean) {
    this.watched = shown;
  }
}

const SessionContext = createContext<Session | null>(null);

/**
 * Says, for as long as the caller is mounted, that the chat is on screen. The server never takes
 * an answer as read because its stream was still open; the page says when it was shown.
 */
export function useChatShown() {
  const session = useContext(SessionContext);
  useEffect(() => {
    session?.watch(true);
    return () => session?.watch(false);
  }, [session]);
}

/**
 * One chat runtime for the whole owner app: the sidebar's chat list and the chat page read the same
 * chats, and a chat keeps its place while the owner looks at another page.
 *
 * The chats and what was said in them are the server's (ADR-0006). The list is a query; a chat's
 * messages are loaded when it is opened; a question is sent with nothing but itself, and the answer
 * is the chat protocol's event stream (AG-UI). A new chat has its id from the start, and the server
 * opens it with its first message.
 */
export function ChatRuntime({ children }: { children: ReactNode }) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const invalidateChats = useInvalidateChats();
  const chats = useChats();
  const archived = useArchivedChats();
  const [session] = useState(
    () =>
      new Session(
        newChatId(),
        t.assistant.errors,
        () => void queryClient.invalidateQueries({ queryKey: CHATS_KEY }),
        (fresh) =>
          fresh
            ? queryClient.fetchQuery({ queryKey: ASSISTANT_KEY, queryFn: getAssistant, staleTime: 0 })
            : queryClient.ensureQueryData({ queryKey: ASSISTANT_KEY, queryFn: getAssistant }),
      ),
  );
  const [chatId, setChatId] = useState(() => session.chatId);
  const runtimeRef = useRef<AssistantRuntime | null>(null);

  useEffect(() => {
    session.speak(t.assistant.errors);
  }, [session, t]);

  const threads = useMemo(
    () =>
      (chats.data?.items ?? []).map((chat) => ({
        id: chat.id,
        status: "regular" as const,
        title: chatTitle(chat, t, locale) || undefined,
        lastMessageAt: new Date(chat.last_message_at ?? chat.created_at),
      })),
    [chats.data, t, locale],
  );
  const archivedThreads = useMemo(
    () =>
      (archived.data?.items ?? []).map((chat) => ({
        id: chat.id,
        status: "archived" as const,
        title: chatTitle(chat, t, locale) || undefined,
      })),
    [archived.data, t, locale],
  );

  const threadList = useMemo<UseAgUiThreadListAdapter>(() => {
    // The chat on screen is gone from the list: a new one takes its place.
    const leave = (id: string) => {
      if (session.chatId === id) void runtimeRef.current?.threads.switchToNewThread();
    };
    return {
      threadId: chatId,
      isLoading: chats.isPending,
      threads,
      archivedThreads,
      onSwitchToNewThread: () => {
        const id = newChatId();
        session.open(id, false);
        session.settle();
        setChatId(id);
      },
      onSwitchToThread: async (id) => {
        session.open(id, true);
        setChatId(id);
        // A copy already read (an earlier visit, or the pointer resting on its row) is shown at
        // once, so the chat never stands empty while it loads: when it holds what the list knows
        // of, because nothing waits unread in the chat or the copy was read after the list was.
        // An unread message is never marked read on a copy that lacks it. Once the copy is no
        // longer fresh, `ServerCopy` reads the server's behind it, which replaces it only where
        // it says something else.
        const known = queryClient.getQueryState<ChatMessages>(chatMessagesKey(id));
        const chats = queryClient.getQueryState<ChatPage>(CHATS_KEY);
        const listed = chats?.data?.items.find((chat) => chat.id === id);
        if (
          known?.data &&
          chats !== undefined &&
          listed !== undefined &&
          !listed.running &&
          (!listed.unread || known.dataUpdatedAt >= chats.dataUpdatedAt)
        ) {
          session.settle(known.dataUpdatedAt);
          return { messages: shown(known.data.messages).map(asThreadMessage) };
        }
        try {
          const kept = await queryClient.fetchQuery({
            queryKey: chatMessagesKey(id),
            queryFn: () => getChatMessages(id),
            staleTime: 0,
          });
          return { messages: shown(kept.messages).map(asThreadMessage) };
        } finally {
          // Also when the messages could not be loaded: the next copy that arrives is shown.
          session.settle();
        }
      },
      onRename: async (id, title) => {
        await updateChat(id, { title });
        invalidateChats();
      },
      onArchive: async (id) => {
        await updateChat(id, { archived: true });
        leave(id);
        invalidateChats();
      },
      onUnarchive: async (id) => {
        await updateChat(id, { archived: false });
        invalidateChats();
      },
      onDelete: async (id) => {
        // An answer still being written for it would be kept for nobody.
        await stopChatRun(id).catch(() => undefined);
        await deleteChat(id);
        leave(id);
        invalidateChats();
      },
    };
  }, [session, chatId, chats.isPending, threads, archivedThreads, queryClient, invalidateChats]);

  const runtime = useAgUiRuntime({
    agent: session.agent,
    // The model's reasoning stays on the server.
    showThinking: false,
    onCancel: () => {
      // The runtime also ends its stream when another chat is opened, and then the answer goes
      // on being written on the server. Only Stop, which leaves the same chat open, stops it.
      const stopped = session.chatId;
      setTimeout(() => {
        if (session.chatId !== stopped) return;
        void stopChatRun(stopped).then(invalidateChats, () => undefined);
      }, 0);
    },
    adapters: { threadList },
  });

  useEffect(() => {
    runtimeRef.current = runtime;
  }, [runtime]);

  return (
    <SessionContext.Provider value={session}>
      <AssistantRuntimeProvider runtime={runtime}>
        <ServerCopy chatId={chatId} session={session} />
        <ChatCards />
        {children}
      </AssistantRuntimeProvider>
    </SessionContext.Provider>
  );
}

/**
 * Keeps the open chat's messages the server's own. What was said can change without this page
 * having streamed it: an answer finished after the phone slept, a colleague asked in the same
 * chat, Tamga wrote in it. The stored copy is read again when the window is looked at again, and
 * every few seconds while Tamga answers there for someone else; it replaces what is on screen only
 * when it says something else.
 */
function ServerCopy({ chatId, session }: { chatId: string; session: Session }) {
  const aui = useAui();
  const queryClient = useQueryClient();
  const invalidateChats = useInvalidateChats();
  const answering = useAuiState((s) => s.thread.isRunning);
  const chat = useChats().data?.items.find((item) => item.id === chatId);
  const stored = useQuery({
    queryKey: chatMessagesKey(chatId),
    queryFn: () => getChatMessages(chatId),
    enabled: chat !== undefined && !answering,
    refetchInterval: chat?.running ? WHILE_ANSWERING : false,
  });

  // This page's own answer ended: the list has a new time, and the stored copy now holds it.
  // An answer that ended on screen is read, and is marked so before the list is read again, so
  // it never shows as new for the moment in between.
  const invalidateRecords = useInvalidatePlacements();
  const wasAnswering = useRef(false);
  useEffect(() => {
    if (wasAnswering.current && !answering) {
      session.settle();
      const seen = session.watched && document.visibilityState === "visible";
      void (seen ? markChatRead(chatId) : Promise.resolve())
        .catch(() => undefined)
        .then(() => {
          invalidateChats();
          void queryClient.invalidateQueries({ queryKey: chatMessagesKey(chatId) });
          // Its draft tools may have changed what the pages show: projects, their links, trucks
          // and checklists.
          invalidateRecords();
          void queryClient.invalidateQueries({ queryKey: ["shipments"] });
        });
    }
    wasAnswering.current = answering;
  }, [answering, chatId, session, invalidateChats, invalidateRecords, queryClient]);

  // The list says the chat changed on the server (a message was written, a run there ended):
  // the stored copy is read again. Without this a run that ends between two polls of the
  // messages would leave its answer unfetched. Another chat opened is no change: its copy is
  // read as it opens, and reading it here too would read it twice.
  const changed = `${chat?.last_message_at ?? ""}|${chat?.running ?? ""}`;
  const lastChanged = useRef({ chatId, changed });
  useEffect(() => {
    const before = lastChanged.current;
    lastChanged.current = { chatId, changed };
    if (before.chatId !== chatId || before.changed === changed) return;
    if (!answering) void queryClient.invalidateQueries({ queryKey: chatMessagesKey(chatId) });
  }, [changed, answering, chatId, queryClient]);

  const kept = stored.data;
  const keptAt = stored.dataUpdatedAt;
  useEffect(() => {
    if (answering || !kept || keptAt < session.currentSince) return;
    const theirs = shown(kept.messages);
    if (said(theirs) === said(aui.thread().getState().messages)) return;
    aui.thread().reset(theirs);
  }, [answering, kept, keptAt, aui, session]);

  return null;
}

/**
 * A chat's stored messages as the thread shows them. A card is drawn on the assistant message
 * before it; one with none (what Tamga wrote could not be used) gets an empty one to stand on.
 */
function shown(messages: readonly { id?: unknown; role?: unknown }[]): ThreadMessageLike[] {
  const owned: unknown[] = [];
  let before: unknown;
  for (const message of messages) {
    if (message.role === "activity" && before !== "assistant" && before !== "activity")
      owned.push({ id: `${String(message.id)}-owner`, role: "assistant", content: "" });
    owned.push(message);
    before = message.role;
  }
  return fromAgUiMessages(owned, { showThinking: false });
}

function asThreadMessage(message: ThreadMessageLike): ThreadMessage {
  return fromThreadMessageLike(message, generateId(), { type: "complete", reason: "unknown" });
}

/**
 * What a list of messages says, to tell whether two say the same: who wrote, the words and the
 * cards, with one writer's messages in a row read as one (a stream and the stored copy split an
 * answer with tool calls differently).
 */
function said(messages: readonly Pick<ThreadMessageLike, "role" | "content">[]): string {
  const turns: string[] = [];
  let writer = "";
  for (const message of messages) {
    const words =
      typeof message.content === "string"
        ? message.content
        : message.content
            .map((part) =>
              part.type === "text"
                ? part.text
                : part.type === "data"
                  ? `\u0002${part.name}\u0002${JSON.stringify(part.data)}`
                  : "",
            )
            .join("");
    if (!words) continue;
    if (message.role === writer) turns[turns.length - 1] += words;
    else turns.push(`${message.role}\u0000${words}`);
    writer = message.role;
  }
  return turns.join("\u0001");
}

/** Why a question could not be asked, in the reader's words. */
function failure(error: unknown, failures: Failures): string {
  if (!(error instanceof ApiError)) return failures.offline;
  if (error.code === "rate_limited") return failures.limit;
  if (error.code === "conflict") return failures.busy;
  if (error.code === "permission_denied") return failures.role;
  return failures.failed;
}

/** A UUID for a new chat. `crypto.randomUUID` is missing on a page served without TLS. */
function newChatId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
