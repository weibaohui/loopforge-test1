"""Loopback HTTP server used to exercise binary payloads end to end.

The server binds ``127.0.0.1`` only.  It exists so that the client can be
tested against a real socket without leaving the machine, and so that the
request side of a payload can be verified byte by byte.

Three details deserve attention:

* ``BaseHTTPRequestHandler`` does not decode chunked request bodies, so the
  handler implements the framing itself.
* A request body is checked against the limit *before* it is buffered, and an
  oversized body is drained instead of being left on the wire.
* The request threads are daemon threads, but the server keeps track of the
  requests that are still in flight and waits for them when it is stopped,
  so no thread or port survives the end of a test case.
"""

from __future__ import annotations

import errno
import hashlib
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .errors import MAX_BODY_BYTES
from .multipart import parse_multipart

__all__ = ["BinaryTestServer", "deterministic_bytes", "BLOB_PAYLOAD", "BYTES_SEED"]

#: Seed of the deterministic byte stream served by ``/bytes/<n>``.
BYTES_SEED = b"httpbinary/bytes/v1"

#: Maximum number of bytes that are drained from an oversized request body.
_MAX_DRAIN_BYTES = 64 * 1024 * 1024

#: Errno values that mean "the peer is gone"; they are not server errors.
_CONNECTION_ERRNOS = frozenset(
    value
    for value in (
        getattr(errno, "EPIPE", None),
        getattr(errno, "ECONNRESET", None),
        getattr(errno, "ECONNABORTED", None),
        getattr(errno, "ENOTCONN", None),
        getattr(errno, "EBADF", None),
    )
    if value is not None
)


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

    #: Socket timeout of a connection, in seconds.  It keeps a half open
    #: request from pinning a thread forever.
    timeout = 10

    #: Length of a request body that was rejected before it was read.
    _pending_length = None

    #: Size of a chunk that was rejected before it was read.
    _pending_chunk_size = None

    def log_message(self, format, *args) -> None:  # noqa: A002 - stdlib signature
        """Silence the default stderr access log."""
        return

    def log_error(self, format, *args) -> None:  # noqa: A002 - stdlib signature
        """Silence the default stderr error log."""
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
                # The answer goes out first, the rest of the request body is
                # drained afterwards: the peer can then finish writing and
                # still read the 413 instead of a broken pipe.
                self._respond(413, b"payload too large\n", "text/plain; charset=utf-8")
                self._drain_request_body()
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
            # The body is not buffered; it is drained after the answer has
            # been sent, so that the peer can finish writing and read the
            # answer instead of losing the connection to a broken pipe.
            self._pending_length = length
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
            if len(body) + size > MAX_BODY_BYTES:
                # The chunk is rejected *before* it is buffered, so a single
                # oversized chunk cannot exhaust the memory of the process.
                self._pending_chunk_size = size
                raise _PayloadTooLarge()
            body += self.rfile.read(size)
            self.rfile.read(2)  # the CRLF that follows the chunk data
        return bytes(body)

    def _drain_request_body(self) -> None:
        """Read and discard whatever is left of the request body."""
        encoding = self.headers.get("Transfer-Encoding", "")
        if "chunked" in encoding.lower():
            pending = self._pending_chunk_size
            self._pending_chunk_size = None
            if pending:
                self._discard(pending)
                self.rfile.read(2)  # the CRLF that follows the chunk data
            self._drain_chunked_body()
            return
        length = self._pending_length
        self._pending_length = None
        if length:
            self._discard(length)

    def _drain_chunked_body(self) -> None:
        """Read and discard the remaining chunks of a chunked request body."""
        while True:
            line = self.rfile.readline(65536)
            if not line:
                return
            size_field = line.split(b";", 1)[0].strip()
            if not size_field:
                continue
            try:
                size = int(size_field, 16)
            except ValueError:
                return
            if size == 0:
                while True:
                    trailer = self.rfile.readline(65536)
                    if trailer in (b"\r\n", b"\n", b""):
                        break
                return
            self._discard(size)
            self.rfile.read(2)  # the CRLF that follows the chunk data

    def _discard(self, count: int) -> None:
        """Read ``count`` bytes from the request body and throw them away.

        Only a bounded amount is drained; beyond that the connection is
        closed, which is acceptable for a loopback test server.
        """
        remaining = min(count, _MAX_DRAIN_BYTES)
        while remaining > 0:
            block = self.rfile.read(min(65536, remaining))
            if not block:
                return
            remaining -= len(block)

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


class _TrackingHTTPServer(ThreadingHTTPServer):
    """A threading HTTP server that keeps track of the requests in flight.

    :class:`socketserver.ThreadingMixIn` only registers *non daemon* request
    threads, so ``server_close()`` does not wait for them.  This subclass
    registers every request thread and connection, which lets
    :meth:`BinaryTestServer.stop` interrupt the connections that are still
    open and wait for their threads to finish.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs) -> None:
        """Create the server and the registries of the in-flight requests."""
        self._request_threads: set[threading.Thread] = set()
        self._open_requests: set[socket.socket] = set()
        self._registry_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def process_request_thread(self, request, client_address) -> None:
        """Serve a request while registering its thread and connection."""
        thread = threading.current_thread()
        with self._registry_lock:
            self._request_threads.add(thread)
            self._open_requests.add(request)
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._registry_lock:
                self._request_threads.discard(thread)
                self._open_requests.discard(request)

    def handle_error(self, request, client_address) -> None:
        """Ignore the errors of a connection that the peer aborted.

        A client that goes away while the server is still writing or draining
        is a normal event for a loopback test server, so the connection
        errors are swallowed.  Every other error is reported the way the
        standard library reports it, so a genuine bug stays visible.
        """
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        if isinstance(error, OSError) and error.errno in _CONNECTION_ERRNOS:
            return
        super().handle_error(request, client_address)

    def abort_open_requests(self) -> None:
        """Shut down the connections of the requests that are still running."""
        with self._registry_lock:
            connections = list(self._open_requests)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def wait_for_request_threads(self, timeout: float = 5.0) -> None:
        """Wait for the request threads that are still running."""
        with self._registry_lock:
            threads = list(self._request_threads)
        for thread in threads:
            thread.join(timeout=timeout)


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
        httpd = _TrackingHTTPServer((self._host, self._requested_port), _BinaryHandler)
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

        The call is idempotent and safe to use in a ``finally`` block.  The
        connections that are still in flight are interrupted and their
        threads are waited for, so that no request thread outlives the
        server.
        """
        httpd = self._httpd
        thread = self._thread
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.abort_open_requests()
            httpd.server_close()
            httpd.wait_for_request_threads()
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
