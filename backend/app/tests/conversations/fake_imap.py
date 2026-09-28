"""A tiny IMAP server for tests: enough of RFC 3501 for the reader, and a record of every command.

It answers LOGIN, CAPABILITY, LIST, EXAMINE, UID SEARCH, UID FETCH and LOGOUT, and says BAD to
anything else, so a test can assert both what the reader asked and that it asked nothing more.
"""

import re
import socketserver
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType

type Send = Callable[[str | bytes], None]


@dataclass
class Folder:
    uidvalidity: int
    messages: dict[int, bytes] = field(default_factory=dict)
    flags: str = "\\HasNoChildren"


@dataclass
class FakeImap:
    folders: dict[str, Folder]
    commands: list[str] = field(default_factory=list)
    port: int = 0
    _server: socketserver.ThreadingTCPServer | None = None

    def __enter__(self) -> FakeImap:
        fake = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                fake._serve(self)

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def verbs(self) -> set[str]:
        """Each command's name, with UID's subcommand: {"LOGIN", "UID FETCH", ...}."""
        names = set()
        for line in self.commands:
            words = line.split()
            names.add(
                " ".join(words[:2]).upper() if words[0].upper() == "UID" else words[0].upper()
            )
        return names

    def _serve(self, conn: socketserver.StreamRequestHandler) -> None:
        def send(text: str | bytes) -> None:
            conn.wfile.write(text.encode() if isinstance(text, str) else text)

        selected: Folder | None = None
        send("* OK fake IMAP ready\r\n")
        while line := conn.rfile.readline():
            tag, _, rest = line.decode().rstrip("\r\n").partition(" ")
            self.commands.append(rest)
            verb, _, args = rest.partition(" ")
            verb = verb.upper()
            if verb == "CAPABILITY":
                send(f"* CAPABILITY IMAP4rev1\r\n{tag} OK done\r\n")
            elif verb == "LOGIN":
                send(f"{tag} OK logged in\r\n")
            elif verb == "LIST":
                for name, folder in self.folders.items():
                    send(f'* LIST ({folder.flags}) "/" "{name}"\r\n')
                send(f"{tag} OK done\r\n")
            elif verb == "EXAMINE":
                selected = self.folders[args.strip('"')]
                uidnext = max(selected.messages, default=0) + 1
                send(
                    f"* {len(selected.messages)} EXISTS\r\n"
                    f"* OK [UIDVALIDITY {selected.uidvalidity}] ok\r\n"
                    f"* OK [UIDNEXT {uidnext}] ok\r\n"
                    f"{tag} OK [READ-ONLY] examined\r\n"
                )
            elif verb == "UID" and selected is not None:
                self._uid(send, tag, args, selected)
            elif verb == "LOGOUT":
                send(f"* BYE\r\n{tag} OK bye\r\n")
                return
            else:
                send(f"{tag} BAD not in the fake\r\n")

    def _uid(self, send: Send, tag: str, args: str, folder: Folder) -> None:
        sub, _, rest = args.partition(" ")
        uids = sorted(folder.messages)
        if sub.upper() == "SEARCH":
            if match := re.match(r"UID (\d+):\*", rest):
                found = [uid for uid in uids if uid >= int(match.group(1))] or uids[-1:]
            else:  # SINCE <date>: every message counts as recent in the fake
                found = uids
            send(f"* SEARCH {' '.join(map(str, found))}\r\n{tag} OK done\r\n")
        elif sub.upper() == "FETCH":
            uid = int(rest.split()[0])
            raw = folder.messages[uid]
            received = '"28-Sep-2026 09:00:00 +0300"'
            head = (
                f"* {uids.index(uid) + 1} FETCH (UID {uid} INTERNALDATE {received}"
                f" BODY[] {{{len(raw)}}}\r\n"
            )
            send(head.encode() + raw + f")\r\n{tag} OK done\r\n".encode())
        else:
            send(f"{tag} BAD not in the fake\r\n")
