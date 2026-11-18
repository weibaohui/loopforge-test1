# httpbinary

Byte-faithful HTTP testing primitives.

`httpbinary` gives binary HTTP interfaces the thing they usually miss: a way
to send and receive **bytes** without any implicit text conversion, and
assertions that tell you *which byte* went wrong.

* Zero third party dependencies — Python 3.11 standard library only.
* Everything runs on `127.0.0.1`; the bundled test server never leaves the
  machine.
* One command runs the whole self-verification suite.

## Quick start

```bash
make test     # run the self-verification suite
make check    # byte compilation, marker scan and docstring check
```

The package is used in place from the repository root, no installation
required:

```python
from httpbinary import BinaryTestServer, post_binary, assert_body_equals

with BinaryTestServer() as server:
    response = post_binary(server.base_url + "/echo", b"\x00\xff\xfe payload")
    assert_body_equals(response, b"\x00\xff\xfe payload")
```

## What it provides

### Client

| Symbol | Purpose |
| --- | --- |
| `request_binary(url, body, *, method, headers, timeout, raise_for_status, max_body_bytes, chunked)` | Send a request, get the raw response bytes back. |
| `post_binary(url, body, **kwargs)` | `POST` specialisation of `request_binary`. |
| `BinaryResponse` | `status`, `headers` (case-insensitive), `body` (always `bytes`), `length`, `sha256`, `content_type`. |

A `str` payload is refused with a `TypeError`: encoding it implicitly is
exactly how binary payloads get corrupted.

### Assertions

```python
from httpbinary import (
    assert_status, assert_header, assert_body_equals,
    assert_body_length, assert_body_sha256,
)
```

A failed check raises `BinaryAssertionError` (a subclass of both
`AssertionError` and `HTTPBinaryError`). `assert_body_equals` reports the
first differing byte:

```text
binary body mismatch at offset 1
  expected length: 10244, actual length: 10244
  expected[0..16]: 00 aa 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f 10 ...
  actual  [0..16]: 00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f 10 ...
```

### Multipart

```python
from httpbinary import build_multipart, parse_multipart

body, content_type = build_multipart(
    {"field": "value"},
    {"upload": ("payload.bin", raw_bytes, "application/octet-stream")},
)
parts = parse_multipart(body, content_type)
assert parts["upload"][0].data == raw_bytes
```

File parts are embedded verbatim — no base64, no text conversion — and a
boundary that would occur inside the payload is replaced automatically.

### Loopback test server

`BinaryTestServer` binds `127.0.0.1` only and serves:

| Route | Method | Behaviour |
| --- | --- | --- |
| `/echo` | POST | Echoes the request body byte for byte (chunked bodies included). |
| `/bytes/<n>` | GET | `n` deterministic bytes, also computable locally with `deterministic_bytes(n)`. |
| `/blob` | GET | A fixed payload covering every byte value, invalid UTF-8 and Chinese UTF-8. |
| `/chunked/<n>` | GET | The same bytes, sent with chunked transfer encoding. |
| `/status/<code>` | GET | Answers with the given status code. |
| `/headers` | GET/POST | Echoes the request headers as JSON. |
| `/multipart` | POST | Parses a multipart body and answers with the raw file part. |

The server shuts down deterministically: `stop()` is idempotent and the
context manager releases the thread and the port.

## Limits

* `MAX_BODY_BYTES` (8 MiB by default) caps the in-memory size of a body.
  A `Content-Length` is checked against the limit *before* the body is read;
  a chunked body is counted while it is read. Either way a
  `BodyTooLargeError` is raised and the oversized body is never buffered.
  The limit can be overridden per call with `max_body_bytes`.
* Every request has a timeout (10 s by default); a silent server makes the
  client raise `TimeoutError` instead of hanging.

## Layout

```text
httpbinary/
├── __init__.py     public API
├── errors.py       exception hierarchy and MAX_BODY_BYTES
├── client.py       BinaryResponse, request_binary, post_binary
├── assertions.py   the five assertion helpers and the byte diagnostics
├── multipart.py    build_multipart, parse_multipart
└── server.py       BinaryTestServer and the loopback routes
tests/              self-verification suite
```
