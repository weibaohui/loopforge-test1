"""Loopback HTTP server used to exercise binary payloads end to end.

The server binds ``127.0.0.1`` only.  It exists so that the client can be
tested against a real socket without leaving the machine, and so that the
request side of a payload can be verified byte by byte.

Two details deserve attention:

* ``BaseHTTPRequestHandler`` does not decode chunked request bodies, so the
  handler implements the framing itself.
* The request threads are daemon threads and the server is shut down
  explicitly, so no thread or port survives the end of a test case.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .errors import MAX_BODY_BYTES
from .multipart import parse_multipart

__all__ = ["BinaryTestServer", "deterministic_bytes", "BLOB_PAYLOAD", "BYTES_SEED"]

#: Seed of the deterministic byte stream served by ``/bytes/<n>``.
BYTES_SEED = b"httpbinary/bytes/v1"


def deterministic_bytes(n: int, seed: bytes = BYTES_SEED) -> bytes:
    """Return ``n`` deterministic bytes.

    The stream is a SHA-256 based keystream: ``sha256(seed || counter)``
    concatenated.  Unlike :mod:`random` it is stable across Python versions
    and platforms, so a test can compute the expected payload locally
    without requesting it first.

    Args:
        n: Number of bytes to produce.
        seed: Seed of the keystream.

    Returns:
        ``n`` bytes of pseudo-random looking but fully deterministic data.
    """
    if n < 0:
        raise ValueError("n must not be negative")
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:n])


def _build_blob() -> bytes:
    """Build the fixed payload served by ``/blob``."""
    return b"".join(
        [
            bytes(range(256)),
            b"\xff\xfe\x00\x01",
            "中文 UTF-8 载荷".encode("utf-8"),
            bytes(range(255, -1, -1)),
        ]
    )


#: Fixed payload served by ``/blob``: every byte value, an invalid UTF-8
#: sequence and a Chinese UTF-8 sequence.
BLOB_PAYLOAD = _build_blob()


class _PayloadTooLarge(Exception):
    """Raised internally when a request body exceeds the in-memory limit."""


class _BinaryHandler(BaseHTTPRequestHandler):
    """Request handler implementing the loopback routes."""

    protocol_version = "HTTP/1.1"
    server_version = "httpbinary/0.1"
    sys_version = ""

    def log_message(self, format, *args) -> None:  # noqa: A002 - stdlib signature
        """Silence the default stderr access log."""
        return

    # -- routing ---------------------------------------------------------

    def do_GET(self) -> None:
        """Handle the read-only routes."""
        path = urlsplit(self.path).path
        try:
            if path == "/blob":
                self._respond(200, BLOB_PAYLOAD, "application/octet-stream")
                return
            if path.startswith("/bytes/"):
                size = self._path_int(path, "/bytes/")
                if size is None:
                    self._respond(400, b"invalid size\n", "text/plain; charset=utf-8")
                    return
                if size > MAX_BODY_BYTES:
                    self._respond(413, b"payload too large\n", "text/plain; charset=utf-8")
                    return
                self._respond(200, deterministic_bytes(size), "application/octet-stream")
                return
            if path.startswith("/chunked/"):
                size = self._path_int(path, "/chunked/")
                if size is None:
                    self._respond(400, b"invalid size\n", "text/plain; charset=utf-8")
                    return
                if size > MAX_BODY_BYTES:
                    self._respond(413, b"payload too large\n", "text/plain; charset=utf-8")
                    return
                self._respond_chunked(deterministic_bytes(size))
                return
            if path.startswith("/status/"):
                code = self._path_int(path, "/status/")
                if code is None or not 100 <= code <= 599:
                    self._respond(400, b"invalid status\n", "text/plain; charset=utf-8")
                    return
                self._respond(code, b"", "text/plain; charset=utf-8")
                return
            if path == "/headers":
                payload = json.dumps(
                    {name: value for name, value in self.headers.items()},
                    sort_keys=True,
                ).encode("utf-8")
                self._respond(200, payload, "application/json")
                return
            self._respond(404, b"not found\n", "text/plain; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_POST(self) -> None:
        """Handle the echo and multipart routes."""
        path = urlsplit(self.path).path
        try:
            try:
                body = self._read_request_body()
            except _PayloadTooLarge:
                self._respond(413, b"payload too large\n", "text/plain; charset=utf-8")
                return
            if path == "/echo":
                self._respond(200, body, "application/octet-stream")
                return
            if path == "/headers":
                payload = json.dumps(
                    {name: value for name, value in self.headers.items()},
                    sort_keys=True,
                ).encode("utf-8")
                self._respond(200, payload, "application/json")
                return
            if path == "/multipart":
                self._respond_multipart(body)
                return
            self._respond(404, b"not found\n", "text/plain; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _path_int(path: str, prefix: str) -> int | None:
        """Parse the integer that follows ``prefix`` in ``path``."""
        tail = path[len(prefix):]
        try:
            return int(tail)
        except ValueError:
            return None

    def _read_request_body(self) -> bytes:
        """Read the request body, decoding chunked framing when present."""
        encoding = self.headers.get("Transfer-Encoding", "")
        if "chunked" in encoding.lower():
            return self._read_chunked_body()
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(raw_length)
        except ValueError:
            return b""
        if length > MAX_BODY_BYTES:
            raise _PayloadTooLarge()
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _read_chunked_body(self) -> bytes:
        """Decode a chunked request body by hand.

        ``BaseHTTPRequestHandler`` does not decode chunked request bodies,
        so the size line, the data and the trailing CRLF are consumed here.
        """
        body = bytearray()
        while True:
            line = self.rfile.readline(65536)
            if not line:
                break
            size_field = line.split(b";", 1)[0].strip()
            if not size_field:
                continue
            try:
                size = int(size_field, 16)
            except ValueError:
                break
            if size == 0:
                # Consume the trailer section.
                while True:
                    trailer = self.rfile.readline(65536)
                    if trailer in (b"\r\n", b"\n", b""):
                        break
                break
            body += self.rfile.read(size)
            self.rfile.read(2)  # the CRLF that follows the chunk data
            if len(body) > MAX_BODY_BYTES:
                raise _PayloadTooLarge()
        return bytes(body)

    def _respond(self, code: int, body: bytes, content_type: str,
                 extra_headers=()) -> None:
        """Send a response with an explicit content length."""
        self.close_connection = True
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            for name, value in extra_headers:
                self.send_header(name, value)
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The peer aborted (for example because it rejected an oversized
            # body); that is not an error of the server.
            self.close_connection = True

    def _respond_chunked(self, body: bytes) -> None:
        """Send a response using chunked transfer encoding."""
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            for start in range(0, len(body), 65536):
                part = body[start:start + 65536]
                self.wfile.write(b"%x\r\n" % len(part) + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _respond_multipart(self, body: bytes) -> None:
        """Parse a multipart request and answer with the raw file part."""
        content_type = self.headers.get("Content-Type", "")
        try:
            parts = parse_multipart(body, content_type)
        except ValueError:
            self._respond(400, b"invalid multipart body\n", "text/plain; charset=utf-8")
            return
        file_parts = [part for parts_list in parts.values() for part in parts_list
                      if part.filename is not None]
        if not file_parts:
            self._respond(404, b"no file part\n", "text/plain; charset=utf-8")
            return
        part = file_parts[0]
        self._respond(
            200,
            part.data,
            part.content_type or "application/octet-stream",
            extra_headers=(
                ("X-Part-Filename", part.filename),
                ("X-Part-Sha256", hashlib.sha256(part.data).hexdigest()),
                ("X-Part-Length", str(len(part.data))),
            ),
        )


class BinaryTestServer:
    """A loopback HTTP server for binary payload testing.

    The server binds ``127.0.0.1`` only; any other host is rejected with a
    :class:`ValueError`.  It is normally used as a context manager so that
    the thread and the port are released deterministically.

    Example:
        >>> with BinaryTestServer() as server:  # doctest: +SKIP
        ...     response = server.base_url + "/echo"
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        """Create a server bound to the loopback interface.

        Args:
            host: Must be ``127.0.0.1`` or ``localhost``.
            port: Port to bind, ``0`` picks a free one.
        """
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError(
                f"BinaryTestServer only binds the loopback interface, got {host!r}"
            )
        self._host = "127.0.0.1"
        self._requested_port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Start serving in a background thread.

        Starting an already running server is a no-op.
        """
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer((self._host, self._requested_port), _BinaryHandler)
        httpd.daemon_threads = True
        httpd.allow_reuse_address = True
        thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="httpbinary-server",
            daemon=True,
        )
        self._httpd = httpd
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Stop the server and release its thread and port.

        The call is idempotent and safe to use in a ``finally`` block.
        """
        httpd = self._httpd
        thread = self._thread
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=5.0)

    @property
    def port(self) -> int:
        """The port the server listens on."""
        if self._httpd is None:
            raise RuntimeError("the server is not running")
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        """The base URL of the running server, without a trailing slash."""
        return f"http://{self._host}:{self.port}"

    @property
    def server_thread(self) -> threading.Thread | None:
        """The thread that runs the server, or ``None`` before the first start."""
        return self._thread

    @property
    def is_running(self) -> bool:
        """Whether the server is currently serving."""
        return self._httpd is not None

    def __enter__(self) -> "BinaryTestServer":
        """Start the server and return it."""
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        """Stop the server when leaving the ``with`` block."""
        self.stop()
