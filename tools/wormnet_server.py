#!/usr/bin/env python3
"""Custom WormNET HTTP CGI + IRC lobby server for Worms Armageddon v3.8.1."""

from __future__ import annotations

from dataclasses import dataclass, field
import http.server
import socket
import socketserver
import sys
import threading
import time
import urllib.parse


DEFAULT_CHANNELS: dict[str, dict[str, str]] = {
    "#AnythingGoes": {
        "topic": "pf,Be - Armageddon Recomp WormNET Lobby",
        "scheme": "pf,Be",
    },
    "#RoperHeaven": {
        "topic": "pf,Be - Rope & Shopper matches",
        "scheme": "pf,Be",
    },
    "#Intermediate": {
        "topic": "pf,Be - Standard Intermediate matches",
        "scheme": "pf,Be",
    },
}


@dataclass
class GameEntry:
    game_id: int
    name: str
    nick: str
    host_ip: str
    channel: str
    pwd: int = 0
    loc: int = 49
    created: float = field(default_factory=time.time)


class WormnetState:
    def __init__(self, irc_host: str = "10.0.0.1", irc_port: int = 6667, http_port: int = 17080):
        self.irc_host = irc_host
        self.irc_port = irc_port
        self.http_port = http_port
        self.lock = threading.Lock()
        self.next_game_id = 1
        self.games: dict[int, GameEntry] = {}
        self.channels: dict[str, dict[str, str]] = dict(DEFAULT_CHANNELS)

    def create_game(
        self,
        name: str,
        nick: str,
        host_ip: str,
        channel: str,
        pwd: int = 0,
        loc: int = 49,
    ) -> GameEntry:
        clean_name = (name or "Game").replace(" ", "_").replace("\r", "").replace("\n", "")
        clean_nick = (nick or "Host").replace(" ", "_").replace("\r", "").replace("\n", "")
        clean_ip = (host_ip or "10.0.0.2").strip()
        clean_chan = channel or "#AnythingGoes"
        if not clean_chan.startswith("#"):
            clean_chan = "#" + clean_chan
        with self.lock:
            gid = self.next_game_id
            self.next_game_id += 1
            for old_id, old in list(self.games.items()):
                if old.nick.lower() == clean_nick.lower() or old.host_ip == clean_ip:
                    self.games.pop(old_id, None)
            entry = GameEntry(
                game_id=gid,
                name=clean_name,
                nick=clean_nick,
                host_ip=clean_ip,
                channel=clean_chan,
                pwd=1 if pwd else 0,
                loc=loc,
            )
            self.games[gid] = entry
            return entry

    def close_game(self, game_id: int | None = None, nick: str = "", host_ip: str = "") -> None:
        with self.lock:
            if game_id and game_id in self.games:
                self.games.pop(game_id, None)
                return
            for gid, g in list(self.games.items()):
                if (nick and g.nick.lower() == nick.lower()) or (host_ip and g.host_ip == host_ip):
                    self.games.pop(gid, None)

    def list_games(self, channel: str = "") -> list[GameEntry]:
        norm = channel.lstrip("#").lower() if channel else ""
        now = time.time()
        with self.lock:
            for gid, g in list(self.games.items()):
                if now - g.created > 3600:
                    self.games.pop(gid, None)
            if not norm:
                return list(self.games.values())
            return [g for g in self.games.values() if g.channel.lstrip("#").lower() == norm]

    def scheme_for(self, channel: str) -> str:
        norm = channel.lstrip("#").lower()
        for k, v in self.channels.items():
            if k.lstrip("#").lower() == norm:
                return v.get("scheme", "pf,Be")
        return "pf,Be"


def _make_http_handler(state: WormnetState):
    class WormnetHTTPHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write(f"[wormnet-http] {self.address_string()} - {fmt % args}\n")

        def _send_text(self, body: str, status: int = 200) -> None:
            raw = body.encode("latin-1", errors="replace")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=iso-8859-1")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path_lower = parsed.path.lower()
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

            def q(key: str, default: str = "") -> str:
                for k, vals in qs.items():
                    if k.lower() == key.lower() and vals:
                        return vals[0]
                return default

            client_ip = (
                self.headers.get("UserServerIP")
                or q("IPAddress")
                or q("HostIP")
                or q("IP")
                or self.client_address[0]
            )
            host_hdr = (self.headers.get("Host") or "").split(":", 1)[0].strip()
            irc_host = state.irc_host
            if host_hdr and host_hdr.lower() not in (
                "wormnet1.team17.com",
                "wormnet.team17.com",
                "serverlist.worms2d.info",
                "wormnet.recomp",
                "10.0.0.1",
            ):
                irc_host = host_hdr

            if path_lower.endswith(("/serverlist.htm", "/communityserverlist.htm")):
                body = (
                    '<HTML><BODY>\r\n'
                    f'<SERVER name="Armageddon Recomp WormNET" '
                    f'address="http://{irc_host}:{state.http_port}/wormageddonweb/">\r\n'
                    f'<REDIRECT href="http://{irc_host}:{state.http_port}/wormageddonweb/Login.asp?UserName=&Password=&IPAddress=">\r\n'
                    '</BODY></HTML>\r\n'
                )
                self._send_text(body)
                return

            if path_lower.endswith(("/login.asp", "/welcomeloginform.asp")):
                body = (
                    f"<CONNECT {irc_host}>\r\n"
                    "<MOTD>\r\n"
                    "Welcome to Armageddon Recomp WormNET!\r\n"
                    "Select a channel (e.g. #AnythingGoes) to chat, join, or host a match.\r\n"
                    "</MOTD>\r\n"
                )
                self._send_text(body)
                return

            if path_lower.endswith("/requestchannelscheme.asp"):
                chan = q("Channel", "#AnythingGoes")
                scheme = state.scheme_for(chan)
                self._send_text(f"<SCHEME={scheme}>\r\n")
                return

            if path_lower.endswith("/gamelist.asp"):
                chan = q("Channel", "")
                games = state.list_games(chan)
                lines = ["<GAMELISTSTART>"]
                for g in games:
                    # Note: trailing space before '>' so WA.exe's integer parser fn_00475a60 sees 0x20 delimiter
                    lines.append(
                        f"<GAME {g.name} {g.nick} {g.host_ip} {g.loc} 1 {g.pwd} {g.game_id} >"
                    )
                lines.append("<GAMELISTEND>")
                self._send_text("\r\n".join(lines) + "\r\n")
                return

            if path_lower.endswith(("/game.asp", "/hostgame.asp", "/creategame.asp")):
                cmd = q("Cmd", "Create").lower()
                name = q("Name", q("GameName", "Worms_Match"))
                nick = q("Nick", q("Nickname", q("UserName", "Host")))
                chan = q("Channel", "#AnythingGoes")
                host_ip = q("HostIP", q("IPAddress", q("IP", client_ip)))
                pwd = 1 if q("Pwd", q("Password", "")) not in ("", "0") else 0
                try:
                    loc = int(q("Loc", q("Location", "49")))
                except ValueError:
                    loc = 49
                try:
                    gid = int(q("GameID", q("ID", "0")))
                except ValueError:
                    gid = 0

                if cmd in ("close", "remove", "delete", "end", "failed"):
                    state.close_game(game_id=gid or None, nick=nick, host_ip=host_ip)
                    self._send_text("OK\r\n")
                else:
                    entry = state.create_game(
                        name=name,
                        nick=nick,
                        host_ip=host_ip,
                        channel=chan,
                        pwd=pwd,
                        loc=loc,
                    )
                    self._send_text(f"SetGameId: {entry.game_id}\r\n<GAMEID {entry.game_id}>\r\nOK\r\n")
                return

            if path_lower.endswith(("/closegame.asp", "/removegame.asp")):
                try:
                    gid = int(q("GameID", q("ID", "0")))
                except ValueError:
                    gid = 0
                state.close_game(game_id=gid or None, nick=q("Nick", ""), host_ip=client_ip)
                self._send_text("OK\r\n")
                return

            if path_lower.endswith("/updateplayerlist.asp"):
                self._send_text("OK\r\n")
                return

            self._send_text(
                f"<CONNECT {irc_host}>\r\n<MOTD>Armageddon Recomp WormNET</MOTD>\r\n"
            )

    return WormnetHTTPHandler


@dataclass(eq=False)
class IrcClient:
    sock: socket.socket
    addr: tuple[str, int]
    nick: str = ""
    user: str = "worm"
    realname: str = "49 0 ?? 3.8.1"
    registered: bool = False
    channels: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def prefix(self) -> str:
        return f"{self.nick or '*'}!~{self.user}@recomp"

    def send_line(self, line: str) -> None:
        data = (line + "\r\n").encode("latin-1", errors="replace")
        with self.lock:
            try:
                self.sock.sendall(data)
            except OSError:
                pass


class WormnetIRCServer:
    def __init__(self, state: WormnetState, host: str = "0.0.0.0", port: int = 6667):
        self.state = state
        self.host = host
        self.port = port
        self.server_name = "wormnet.recomp"
        self.lock = threading.Lock()
        self.clients: dict[str, IrcClient] = {}
        self.channels: dict[str, set[IrcClient]] = {
            name: set() for name in DEFAULT_CHANNELS
        }
        self._sock: socket.socket | None = None
        self._running = False

    def start_in_thread(self) -> threading.Thread:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(64)
        self._sock = s
        self._running = True
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        return t

    def _accept_loop(self) -> None:
        assert self._sock is not None
        while self._running:
            try:
                conn, addr = self._sock.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                break
            c = IrcClient(sock=conn, addr=addr)
            threading.Thread(target=self._client_loop, args=(c,), daemon=True).start()

    def _client_loop(self, c: IrcClient) -> None:
        buf = b""
        try:
            while True:
                chunk = c.sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    line = raw_line.rstrip(b"\r").decode("latin-1", errors="replace").strip()
                    if line:
                        self._handle_line(c, line)
        except OSError:
            pass
        finally:
            self._disconnect(c, "Connection closed")

    def _find_channel_key(self, name: str) -> str:
        if not name.startswith("#"):
            name = "#" + name
        for k in self.channels:
            if k.lower() == name.lower():
                return k
        self.channels[name] = set()
        if name not in self.state.channels:
            self.state.channels[name] = {
                "topic": f"pf,Be - {name}",
                "scheme": "pf,Be",
            }
        return name

    def _register_if_ready(self, c: IrcClient) -> None:
        if c.registered or not c.nick:
            return
        c.registered = True
        s = self.server_name
        n = c.nick
        c.send_line(f":{s} 001 {n} :Welcome to the Armageddon Recomp WormNET IRC Network {n}")
        c.send_line(f":{s} 002 {n} :Your host is {s}, running WormnetRecomp 1.0")
        c.send_line(f":{s} 003 {n} :This server was created today")
        c.send_line(f":{s} 004 {n} {s} WormnetRecomp-1.0 o o")
        c.send_line(f":{s} 375 {n} :- {s} Message of the Day -")
        c.send_line(f":{s} 372 {n} :- Welcome to Armageddon Recomp WormNET!")
        c.send_line(f":{s} 376 {n} :End of /MOTD command.")

    def _handle_line(self, c: IrcClient, line: str) -> None:
        trailing = ""
        if " :" in line:
            head, trailing = line.split(" :", 1)
            parts = head.split()
        else:
            parts = line.split()
        if not parts:
            return
        cmd = parts[0].upper()
        args = parts[1:]
        s = self.server_name

        if cmd == "PING":
            token = trailing or (args[0] if args else s)
            c.send_line(f":{s} PONG {s} :{token}")
            return

        if cmd == "PONG":
            return

        if cmd == "PASS":
            return

        if cmd == "NICK":
            want = (args[0] if args else trailing).strip() or "Worm"
            want = want.replace(" ", "_")
            with self.lock:
                existing = self.clients.get(want.lower())
                if existing and existing is not c:
                    suffix = 2
                    while f"{want}{suffix}".lower() in self.clients:
                        suffix += 1
                    want = f"{want}{suffix}"
                if c.nick:
                    self.clients.pop(c.nick.lower(), None)
                c.nick = want
                self.clients[c.nick.lower()] = c
            self._register_if_ready(c)
            return

        if cmd == "USER":
            if args:
                c.user = args[0]
            if trailing:
                c.realname = trailing
            self._register_if_ready(c)
            return

        self._register_if_ready(c)
        n = c.nick or "*"

        if cmd == "LIST":
            c.send_line(f":{s} 321 {n} Channel :Users  Name")
            with self.lock:
                for ch_name, members in self.channels.items():
                    topic = self.state.channels.get(ch_name, {}).get("topic", "pf,Be - Armageddon Recomp")
                    count = max(1, len(members))
                    c.send_line(f":{s} 322 {n} {ch_name} {count} :{topic}")
            c.send_line(f":{s} 323 {n} :End of /LIST")
            return

        if cmd == "JOIN":
            raw_targets = (args[0] if args else trailing).split(",")
            for raw_t in raw_targets:
                raw_t = raw_t.strip()
                if not raw_t:
                    continue
                with self.lock:
                    ch = self._find_channel_key(raw_t)
                    members = self.channels[ch]
                    members.add(c)
                    c.channels.add(ch)
                    member_list = list(members)
                    topic = self.state.channels.get(ch, {}).get("topic", "pf,Be - Armageddon Recomp")
                for m in member_list:
                    m.send_line(f":{c.prefix} JOIN :{ch}")
                c.send_line(f":{s} 332 {n} {ch} :{topic}")
                names = " ".join(m.nick for m in member_list if m.nick)
                c.send_line(f":{s} 353 {n} = {ch} :{names}")
                c.send_line(f":{s} 366 {n} {ch} :End of /NAMES list.")
            return

        if cmd == "PART":
            raw_targets = (args[0] if args else "").split(",")
            reason = trailing or "Leaving"
            for raw_t in raw_targets:
                raw_t = raw_t.strip()
                if not raw_t:
                    continue
                with self.lock:
                    ch = self._find_channel_key(raw_t)
                    members = self.channels.get(ch, set())
                    member_list = list(members)
                    members.discard(c)
                    c.channels.discard(ch)
                for m in member_list:
                    m.send_line(f":{c.prefix} PART {ch} :{reason}")
            return

        if cmd in ("PRIVMSG", "NOTICE"):
            if not args:
                return
            target = args[0]
            msg = trailing if trailing else (" ".join(args[1:]) if len(args) > 1 else "")
            if target.startswith("#"):
                with self.lock:
                    ch = self._find_channel_key(target)
                    recipients = [m for m in self.channels.get(ch, set()) if m is not c]
                for m in recipients:
                    m.send_line(f":{c.prefix} {cmd} {ch} :{msg}")
            else:
                with self.lock:
                    dest = self.clients.get(target.lower())
                if dest:
                    dest.send_line(f":{c.prefix} {cmd} {dest.nick} :{msg}")
            return

        if cmd == "WHO":
            target = args[0] if args else "*"
            with self.lock:
                if target.startswith("#"):
                    ch = self._find_channel_key(target)
                    members = list(self.channels.get(ch, set()))
                else:
                    ch = "*"
                    members = [m for m in self.clients.values() if target in ("*", m.nick)]
            for m in members:
                c.send_line(f":{s} 352 {n} {ch} ~{m.user} recomp {s} {m.nick} H :0 {m.realname}")
            c.send_line(f":{s} 315 {n} {target} :End of /WHO list.")
            return

        if cmd == "MODE":
            target = args[0] if args else n
            if target.startswith("#"):
                ch = self._find_channel_key(target)
                c.send_line(f":{s} 324 {n} {ch} +nt")
            else:
                c.send_line(f":{s} 221 {n} +i")
            return

        if cmd == "TOPIC":
            target = args[0] if args else "#AnythingGoes"
            ch = self._find_channel_key(target)
            topic = self.state.channels.get(ch, {}).get("topic", "pf,Be - Armageddon Recomp")
            c.send_line(f":{s} 332 {n} {ch} :{topic}")
            return

        if cmd == "ISON":
            queried = [a.lower() for a in args]
            if trailing:
                queried.extend(x.lower() for x in trailing.split())
            with self.lock:
                online = [self.clients[q].nick for q in queried if q in self.clients]
            c.send_line(f":{s} 303 {n} :{' '.join(online)}")
            return

        if cmd == "USERHOST":
            replies = []
            with self.lock:
                for q in args:
                    m = self.clients.get(q.lower())
                    if m:
                        replies.append(f"{m.nick}=+~{m.user}@recomp")
            c.send_line(f":{s} 302 {n} :{' '.join(replies)}")
            return

        if cmd == "QUIT":
            self._disconnect(c, trailing or "Client Quit")
            try:
                c.sock.close()
            except OSError:
                pass
            return

    def _disconnect(self, c: IrcClient, reason: str = "Quit") -> None:
        peers: set[IrcClient] = set()
        with self.lock:
            if c.nick and self.clients.get(c.nick.lower()) is c:
                self.clients.pop(c.nick.lower(), None)
            for ch in list(c.channels):
                members = self.channels.get(ch, set())
                members.discard(c)
                peers.update(members)
            c.channels.clear()
        if c.nick:
            self.state.close_game(nick=c.nick)
            for p in peers:
                if p is not c:
                    p.send_line(f":{c.prefix} QUIT :{reason}")
        try:
            c.sock.close()
        except OSError:
            pass


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_wormnet_server(
    http_host: str = "0.0.0.0",
    http_port: int = 17080,
    irc_host: str = "0.0.0.0",
    irc_port: int = 6667,
    public_host: str = "10.0.0.1",
) -> WormnetState:
    state = WormnetState(irc_host=public_host, irc_port=irc_port, http_port=http_port)
    irc = WormnetIRCServer(state=state, host=irc_host, port=irc_port)
    irc.start_in_thread()

    handler = _make_http_handler(state)
    httpd = _ThreadedHTTPServer((http_host, http_port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"WormNET HTTP listening on http://{http_host}:{http_port}/ (IRC -> {public_host}:{irc_port})")
    return state
