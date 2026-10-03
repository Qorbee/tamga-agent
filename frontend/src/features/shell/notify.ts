/**
 * The browser's own notifications, shown through a service worker that does nothing else
 * (`public/notify-worker.js`): Chrome on Android has no `new Notification`. A browser shows them
 * only while a tab of the app is open; reaching a closed app needs web push, not built.
 */
const WORKER = "/notify-worker.js";

export const canNotify = () => "Notification" in window && "serviceWorker" in navigator;

/** Asked when the owner turns notifications on; the worker is readied with the permission. */
export async function allowNotifications(): Promise<NotificationPermission> {
  const permission = await Notification.requestPermission();
  if (permission === "granted") await navigator.serviceWorker.register(WORKER);
  return permission;
}

/** A notification about a chat; nothing when the owner has not allowed them. */
export async function notifyAbout(chat: string, title: string, body: string): Promise<void> {
  if (!canNotify() || Notification.permission !== "granted") return;
  await navigator.serviceWorker.register(WORKER);
  const worker = await navigator.serviceWorker.ready;
  await worker.showNotification(title, { body, tag: chat, data: { chat } });
}

/** Calls `open` with the chat a clicked notification names, as the worker passes it on. */
export function onNotificationClick(open: (chat: string) => void): () => void {
  if (!("serviceWorker" in navigator)) return () => undefined;
  const listener = (event: MessageEvent<{ openChat?: unknown } | null>) => {
    const chat = event.data?.openChat;
    if (typeof chat === "string") open(chat);
  };
  navigator.serviceWorker.addEventListener("message", listener);
  return () => navigator.serviceWorker.removeEventListener("message", listener);
}
