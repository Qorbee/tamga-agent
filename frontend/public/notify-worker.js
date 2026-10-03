// Tamga's notifications, shown through a service worker because Chrome on Android has no
// `new Notification`. It does nothing else: no caching, no fetch handler. A click focuses the
// app and asks it to open the chat the notification names, or opens the app at that chat when
// no tab of it is left (`features/shell/company-events.tsx`).

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const chat = event.notification.data && event.notification.data.chat;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const page = windows.find((client) => new URL(client.url).pathname.startsWith("/app"));
      if (!page) return self.clients.openWindow(chat ? "/app?chat=" + encodeURIComponent(chat) : "/app");
      return page.focus().then(() => page.postMessage({ openChat: chat }));
    }),
  );
});
