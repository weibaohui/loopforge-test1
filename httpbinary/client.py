"""Byte-faithful HTTP client and response object.

The client is a thin, explicit wrapper around :mod:`http.client`.  It exists
for three reasons:

* it refuses ``str`` payloads instead of silently encoding them (a ``str``
  round-trip is exactly how binary payloads get corrupted);
* it enforces a body-size ceiling *before* the body is buffered;
* it exposes the raw response bytes through :class:`BinaryResponse`.
"""

from __future__ import annotations

import hashlib
import http.client
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .errors import BodyTooLargeError, HTTPBinaryError, MAX_BODY_BYTES

__all__ = ["BinaryResponse", "Headers", "request_binary", "post_binary"]

#: Size of the blocks used when a response has no usable ``Content-Length``.
_READ_BLOCK = 65536


class Headers(Mapping):
    """Read-only, case-insensitive view over HTTP header pairs.

    Lookup is case-insensitive (``headers["content-type"]`` and
    ``headers["Content-Type"]`` return the same value) while iteration yields
    the header names in their original spelling.  Repeated headers are joined
    with ``", "`` which matches the semantics used by
    :class:`email.message.Message`.
    """

    __slots__ = ("_pairs", "_index")

    def __init__(self, pairs=()):
        """Build a header mapping from an iterable of ``(name, value)`` pairs."""
        self._pairs = [(str(name), str(value)) for name, value in pairs]
        index: dict[str, str] = {}
        for name, value in self._pairs:
            key = name.lower()
            index[key] = index[key] + ", " + value if key in index else value
        self._index = index

    def __getitem__(self, name):
        return self._index[str(name).lower()]

    def __iter__(self):
        seen = set()
        for name, _ in self._pairs:
            key = name.lower()
            if key not in seen:
                seen.add(key)
                yield name

    def __len__(self):
        return len(self._index)

    def __eq__(self, other):
        if isinstance(other, Headers):
            return self._index == other._index
        if isinstance(other, Mapping):
            return self._index == {str(k).lower(): v for k, v in other.items()}
        return NotImplemented

    def __hash__(self):
        return hash(tuple(sorted(self._index.items())))

    def __repr__(self):
        return f"Headers({self._index!r})"


@dataclass(frozen=True)
class BinaryResponse:
    """An HTTP response whose body is guaranteed to be raw ``bytes``.

    Attributes:
        status: HTTP status code.
        headers: Case-insensitive mapping of the response headers.
        body: The response body, always ``bytes`` and never ``str``.
    """

    status: int
    headers: Mapping[str, str]
    body: bytes
    _sha256: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Normalise the body to ``bytes`` and cache the SHA-256 digest."""
        body = self.body
        if not isinstance(body, (bytes, bytearray, memoryview)):
            raise TypeError(
                "BinaryResponse.body must be bytes-like, got "
                f"{type(body).__name__}; a str body would hide an implicit "
                "text encoding"
            )
        headers = self.headers
        if not isinstance(headers, Headers):
            pairs = headers.items() if hasattr(headers, "items") else headers
            headers = Headers(pairs)
        object.__setattr__(self, "headers", headers)
        object.__setattr__(self, "body", bytes(body))
        object.__setattr__(self, "_sha256", hashlib.sha256(self.body).hexdigest())

    @property
    def length(self) -> int:
        """Length of the response body in bytes."""
        return len(self.body)

    @property
    def sha256(self) -> str:
        """Hexadecimal SHA-256 digest of the response body."""
        return self._sha256

    @property
    def content_type(self) -> str | None:
        """The ``Content-Type`` header value, or ``None`` when absent."""
        value = self.headers.get("Content-Type")
        return None if value is None else str(value)


def _coerce_body(body) -> bytes:
    """Return ``body`` as ``bytes``, refusing ``str`` payloads outright.

    ``str`` is rejected on purpose: encoding it implicitly would let a caller
    believe that a binary payload survived a round-trip when it did not.
    """
    if isinstance(body, str):
        raise TypeError(
            "body must be bytes-like, not str; encode the payload explicitly "
            "so that no implicit text conversion happens on the wire"
        )
    if isinstance(body, (bytes, bytearray, memoryview)):
        return bytes(body)
    raise TypeError(f"body must be bytes-like, got {type(body).__name__}")


def _encode_chunked(payload: bytes) -> bytes:
    """Encode ``payload`` as an HTTP/1.1 chunked transfer body.

    ``http.client`` does not encode request bodies as chunks, so the library
    performs the framing itself: ``<hex size>\\r\\n<data>\\r\\n`` per chunk,
    terminated by the zero-length chunk.
    """
    chunks = bytearray()
    for start in range(0, len(payload), _READ_BLOCK):
        part = payload[start:start + _READ_BLOCK]
        chunks += b"%x\r\n" % len(part)
        chunks += part
        chunks += b"\r\n"
    chunks += b"0\r\n\r\n"
    return bytes(chunks)


def _read_body(response, limit: int) -> bytes:
    """Read a response body without ever buffering more than ``limit`` bytes.

    Two paths are used, matching the two ways a server can frame a body:

    * ``Content-Length`` present: the declared size is compared against the
      limit *before* a single body byte is read, so an oversized response is
      rejected without touching the payload at all.
    * chunked (or no length at all): the body is read block by block and the
      running total is checked against the limit after every block.
    """
    declared = response.getheader("Content-Length")
    if declared is not None:
        try:
            size = int(declared)
        except (TypeError, ValueError):
            size = None
        if size is not None:
            if size > limit:
                raise _too_large(limit, 0)
            return response.read(size)

    blocks: list[bytes] = []
    total = 0
    while True:
        remaining = limit - total
        block = response.read(min(_READ_BLOCK, remaining + 1))
        if not block:
            break
        if total + len(block) > limit:
            # The offending block is dropped, so the number of bytes held in
            # memory never exceeds the configured limit.
            raise _too_large(limit, total)
        blocks.append(block)
        total += len(block)
    return b"".join(blocks)


def _too_large(limit: int, bytes_read: int) -> BodyTooLargeError:
    """Build a :class:`BodyTooLargeError` describing a rejected body."""
    error = BodyTooLargeError(
        f"body exceeds the limit of {limit} bytes "
        f"({bytes_read} bytes were read into memory); "
        "the body was not fully read"
    )
    error.limit = limit
    error.bytes_read = bytes_read
    return error


def request_binary(
    url: str,
    body: bytes = b"",
    *,
    method: str = "POST",
    headers: Mapping[str, str] | None = None,
    timeout: float = 10.0,
    raise_for_status: bool = False,
    max_body_bytes: int = MAX_BODY_BYTES,
    chunked: bool = False,
) -> BinaryResponse:
    """Send an HTTP request and return the raw response bytes.

    Args:
        url: Absolute ``http://`` or ``https://`` URL.
        body: Request body.  Must be bytes-like; ``str`` is rejected.
        method: HTTP method, ``POST`` by default.
        headers: Extra request headers.
        timeout: Socket timeout in seconds, 10 seconds by default.
        raise_for_status: When true, a 4xx/5xx response raises
            :class:`~httpbinary.errors.HTTPBinaryError` instead of being
            returned.  By default error responses are returned normally so
            that the assertion helpers can report them.
        max_body_bytes: In-memory ceiling for the response body.
        chunked: When true the request body is sent using chunked transfer
            encoding.  The framing is performed by this library because
            :mod:`http.client` does not encode request bodies.

    Returns:
        A :class:`BinaryResponse` whose ``body`` is the raw response bytes.
    """
    payload = _coerce_body(body)
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise HTTPBinaryError(f"unsupported URL scheme: {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise HTTPBinaryError(f"URL has no host: {url!r}")
    port = parts.port or (443 if scheme == "https" else 80)
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"

    connection_class = (
        http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_class(host, port, timeout=timeout)
    try:
        connection.putrequest(method.upper(), target, skip_accept_encoding=True)
        for name, value in (headers or {}).items():
            # The framing headers are managed by the library, otherwise the
            # request could carry two conflicting values for them.
            if str(name).lower() in ("content-length", "transfer-encoding"):
                continue
            connection.putheader(name, value)
        if chunked:
            connection.putheader("Transfer-Encoding", "chunked")
            connection.endheaders(_encode_chunked(payload))
        else:
            connection.putheader("Content-Length", str(len(payload)))
            connection.endheaders(payload)
        raw = connection.getresponse()
        try:
            response_headers = Headers(raw.getheaders())
            status = raw.status
            response_body = _read_body(raw, max_body_bytes)
        finally:
            raw.close()
    finally:
        connection.close()

    response = BinaryResponse(status=status, headers=response_headers, body=response_body)
    if raise_for_status and status >= 400:
        preview = response_body[:64]
        error = HTTPBinaryError(
            f"HTTP {status} response from {url!r}; body preview: {preview!r}"
        )
        error.status = status
        error.body_preview = preview
        raise error
    return response


def post_binary(url: str, body: bytes, **kwargs) -> BinaryResponse:
    """POST ``body`` to ``url`` and return the raw response bytes.

    A thin specialisation of :func:`request_binary`; every keyword argument
    of that function is accepted here as well.
    """
    kwargs.setdefault("method", "POST")
    return request_binary(url, body, **kwargs)
