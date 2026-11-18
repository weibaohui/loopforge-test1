"""Byte-faithful HTTP testing primitives.

``httpbinary`` provides the small set of primitives that binary HTTP
interfaces usually need and that most test helpers get wrong: a client that
never lets a payload turn into text, a response object that exposes raw
bytes, assertions that point at the first differing byte, and a loopback
server that makes the round trip observable.

Example:
    >>> from httpbinary import BinaryTestServer, post_binary
    >>> with BinaryTestServer() as server:
    ...     response = post_binary(server.base_url + "/echo", b"\\x00\\xff")
    ...     assert response.body == b"\\x00\\xff"
"""

from .assertions import (
    assert_body_equals,
    assert_body_length,
    assert_body_sha256,
    assert_header,
    assert_status,
)
from .client import BinaryResponse, Headers, post_binary, request_binary
from .errors import (
    MAX_BODY_BYTES,
    BodyTooLargeError,
    BinaryAssertionError,
    HTTPBinaryError,
)
from .multipart import MultipartPart, build_multipart, parse_multipart
from .server import BLOB_PAYLOAD, BinaryTestServer, deterministic_bytes

__all__ = [
    # errors
    "HTTPBinaryError",
    "BodyTooLargeError",
    "BinaryAssertionError",
    "MAX_BODY_BYTES",
    # client
    "BinaryResponse",
    "Headers",
    "request_binary",
    "post_binary",
    # assertions
    "assert_status",
    "assert_header",
    "assert_body_equals",
    "assert_body_length",
    "assert_body_sha256",
    # multipart
    "MultipartPart",
    "build_multipart",
    "parse_multipart",
    # server
    "BinaryTestServer",
    "deterministic_bytes",
    "BLOB_PAYLOAD",
]

__version__ = "0.1.0"
