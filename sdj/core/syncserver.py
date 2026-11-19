"""The small network service phones talk to on the shop Wi-Fi (background threads, no cloud).

* TCP 47810 – HTTP with encrypted JSON bodies: /sdj/hello, /sdj/pair, /sdj/sync
* UDP 47811 – "where is the PC?" broadcast from a phone; the PC answers with its name and port
"""
from __future__ import annotations

import json
import logging
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import phonesync

log = logging.getLogger(__name__)
MAX_BODY = 32 * 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    server_version = "SDJ"
    protocol_version = "HTTP/1.1"
    timeout = 90

    def log_message(self, fmt, *args):  # noqa: D401 - keep the console quiet
        log.debug("phone %s - " + fmt, self.client_address[0], *args)

    def _send(self, status: int, obj) -> None:
        body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") == "/sdj/hello":
            self._send(200, phonesync.hello())
        else:
            self._send(404, {"error": "Not found", "code": "not_found"})

    def do_POST(self):  # noqa: N802
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            self._send(413, {"error": "Request too large", "code": "too_large"})
            return
        try:
            env = json.loads(self.rfile.read(n).decode("utf-8"))
            ip = self.client_address[0]
            if self.path == "/sdj/pair":
                out = phonesync.handle_pair(env, ip)
            elif self.path == "/sdj/sync":
                out = phonesync.handle_sync(env, ip)
            else:
                self._send(404, {"error": "Not found", "code": "not_found"})
                return
            self._send(200, out)
        except phonesync.SyncError as exc:
            self._send(exc.status, {"error": str(exc), "code": exc.code})
        except (ValueError, KeyError) as exc:
            self._send(400, {"error": f"Bad request: {exc}", "code": "bad_request"})
        except Exception:
            log.exception("phone request failed")
            self._send(500, {"error": "The PC could not handle this request. Details are in the PC's log.",
                             "code": "server"})


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = not sys.platform.startswith("win")


class SyncServer:
    def __init__(self, port: int = phonesync.PORT, udp_port: int = phonesync.DISCOVERY_PORT, host: str = "0.0.0.0"):
        self.port, self.udp_port, self.host = port, udp_port, host
        self._http: _Server | None = None
        self._udp: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.error = ""

    @property
    def running(self) -> bool:
        return self._http is not None

    def start(self) -> bool:
        if self.running:
            return True
        self._stop.clear()
        try:
            self._http = _Server((self.host, self.port), _Handler)
            if self.port == 0:
                self.port = self._http.server_address[1]
        except OSError as exc:
            self._http = None
            self.error = (f"Phone sync could not start: port {self.port} is busy or blocked ({exc}). "
                          "Close other copies of SDJ Mobile Care and restart.")
            log.warning(self.error)
            return False
        t = threading.Thread(target=self._http.serve_forever, name="sdj-phone-http", daemon=True)
        t.start()
        self._threads = [t]
        try:
            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if not sys.platform.startswith("win"):
                self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._udp.bind((self.host, self.udp_port))
            self.udp_port = self._udp.getsockname()[1]
            self._udp.settimeout(1.0)
            u = threading.Thread(target=self._udp_loop, name="sdj-phone-udp", daemon=True)
            u.start()
            self._threads.append(u)
        except OSError as exc:            # discovery is a convenience; typing the PC address still works
            log.warning("phone discovery (UDP %s) not available: %s", self.udp_port, exc)
            self._udp = None
        self.error = ""
        log.info("phone sync listening on port %s", self.port)
        return True

    def _udp_loop(self) -> None:
        sock = self._udp
        while not self._stop.is_set() and sock is not None:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if data.startswith(b"SDJ?"):
                want = data[4:].decode("ascii", "ignore").strip()
                try:
                    info = phonesync.hello()
                    if want and want != info["pc"]:
                        continue
                    sock.sendto(b"SDJ!" + json.dumps(info).encode("utf-8"), addr)
                except Exception:  # never let a bad packet stop discovery
                    log.exception("discovery reply failed")

    def stop(self) -> None:
        self._stop.set()
        if self._http is not None:
            try:
                self._http.shutdown()
                self._http.server_close()
            except Exception:
                log.exception("stopping phone sync failed")
            self._http = None
        if self._udp is not None:
            try:
                self._udp.close()
            except OSError:
                pass
            self._udp = None
        for t in self._threads:
            t.join(timeout=3)
        self._threads = []


_service = SyncServer()


def service() -> SyncServer:
    return _service


def ensure_running() -> SyncServer:
    """Start the service when a phone is linked (or a link code is waiting); stop it otherwise."""
    try:
        wanted = phonesync.sync_wanted()
    except Exception:  # database not ready
        log.exception("phone sync check failed")
        wanted = False
    if wanted and not _service.running:
        _service.start()
    elif not wanted and _service.running:
        _service.stop()
    return _service
