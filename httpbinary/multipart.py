"""Construction and parsing of ``multipart/form-data`` payloads.

The multipart body is assembled and dissected by hand, on raw bytes, so that
a file part is embedded verbatim: no base64, no text conversion, no
re-encoding.  The boundary is validated against the payload, because a
boundary that also occurs inside the data would split a part in two.
"""

from __future__ import annotations

import email.message
import email.utils
import uuid
from dataclasses import dataclass
from typing import Mapping

__all__ = ["MultipartPart", "build_multipart", "parse_multipart"]

#: Prefix of the generated boundaries.
_BOUNDARY_PREFIX = b"----httpbinary"

#: How many boundaries are tried before giving up on finding a safe one.
_MAX_BOUNDARY_ATTEMPTS = 100


@dataclass(frozen=True)
class MultipartPart:
    """A single part of a multipart body.

    Attributes:
        name: The form field name.
        filename: The file name of the part, ``None`` for plain fields.
        content_type: The declared content type of the part, if any.
        data: The raw bytes of the part.
    """

    name: str
    filename: str | None
    content_type: str | None
    data: bytes


def _random_boundary() -> bytes:
    """Return a fresh, highly unlikely to collide boundary."""
    return _BOUNDARY_PREFIX + uuid.uuid4().hex.encode("ascii")


def _conflicts(boundary: bytes, payloads: list[bytes]) -> bool:
    """Return whether ``boundary`` occurs inside any of ``payloads``."""
    return any(boundary in payload for payload in payloads)


def build_multipart(
    fields: Mapping[str, str],
    files: Mapping[str, tuple[str, bytes, str]],
    *,
    boundary: bytes | None = None,
) -> tuple[bytes, str]:
    """Build a ``multipart/form-data`` body with byte-exact file parts.

    Args:
        fields: Plain text form fields.
        files: File parts, each a ``(filename, data, content_type)`` triple.
            The ``data`` bytes are embedded verbatim.
        boundary: Boundary to use.  When it is not given a random one is
            generated.  A boundary that also occurs inside the payload would
            corrupt the body, so it is replaced by a fresh one; the boundary
            that was actually used is always reported in the returned
            content type.

    Returns:
        A ``(body, content_type)`` tuple, where ``content_type`` is the
        ``Content-Type`` header value including the boundary parameter.

    Raises:
        TypeError: If a file part does not carry bytes.
    """
    field_items = []
    for name, value in (fields or {}).items():
        if isinstance(value, str):
            encoded = value.encode("utf-8")
        elif isinstance(value, (bytes, bytearray, memoryview)):
            encoded = bytes(value)
        else:
            raise TypeError(
                f"field {name!r} must be str or bytes-like, got {type(value).__name__}"
            )
        field_items.append((str(name), encoded))

    file_items = []
    for name, spec in (files or {}).items():
        try:
            filename, data, content_type = spec
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"file {name!r} must be a (filename, data, content_type) triple"
            ) from exc
        if isinstance(data, str):
            raise TypeError(
                f"file {name!r} must carry bytes, not str; a str payload would "
                "be silently encoded and lose its binary content"
            )
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"file {name!r} must carry bytes-like data, got {type(data).__name__}"
            )
        content_type_text = str(content_type)
        if "\r" in content_type_text or "\n" in content_type_text:
            raise ValueError(
                f"file {name!r} has a content type with a line break; it could "
                "inject an extra part header"
            )
        file_items.append((str(name), str(filename), bytes(data), content_type_text))

    payloads = [data for _, data in field_items]
    payloads.extend(data for _, _, data, _ in file_items)

    if boundary is None:
        candidate = _random_boundary()
        attempts = 0
        while _conflicts(candidate, payloads) and attempts < _MAX_BOUNDARY_ATTEMPTS:
            candidate = _random_boundary()
            attempts += 1
        if _conflicts(candidate, payloads):
            raise ValueError("unable to generate a boundary that avoids the payload")
    else:
        if not isinstance(boundary, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"boundary must be bytes-like, got {type(boundary).__name__}"
            )
        candidate = bytes(boundary)
        if _conflicts(candidate, payloads):
            candidate = _random_boundary()
            attempts = 0
            while _conflicts(candidate, payloads) and attempts < _MAX_BOUNDARY_ATTEMPTS:
                candidate = _random_boundary()
                attempts += 1
            if _conflicts(candidate, payloads):
                raise ValueError("unable to generate a boundary that avoids the payload")

    body = bytearray()
    for name, data in field_items:
        body += b"--" + candidate + b"\r\n"
        body += b'Content-Disposition: form-data; name="' + _escape(name).encode("utf-8") + b'"\r\n'
        body += b"\r\n"
        body += data
        body += b"\r\n"
    for name, filename, data, content_type in file_items:
        body += b"--" + candidate + b"\r\n"
        body += (
            b'Content-Disposition: form-data; name="'
            + _escape(name).encode("utf-8")
            + b'"; filename="'
            + _escape(filename).encode("utf-8")
            + b'"\r\n'
        )
        body += b"Content-Type: " + content_type.encode("utf-8") + b"\r\n"
        body += b"\r\n"
        body += data
        body += b"\r\n"
    body += b"--" + candidate + b"--\r\n"

    try:
        boundary_text = candidate.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("boundary must be ASCII so that it fits in a header") from exc
    content_type = f"multipart/form-data; boundary={boundary_text}"
    return bytes(body), content_type


def _escape(value: str) -> str:
    """Escape the characters that may not appear inside a quoted parameter."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")


def _boundary_from_content_type(content_type: str) -> bytes:
    """Extract the boundary parameter of a multipart content type."""
    message = email.message.Message()
    message["Content-Type"] = content_type
    boundary = message.get_param("boundary")
    if boundary is None:
        raise ValueError(f"content type has no boundary parameter: {content_type!r}")
    if isinstance(boundary, tuple):
        boundary = email.utils.collapse_rfc2231_value(boundary)
    return str(boundary).encode("utf-8")


def _decode_header_bytes(raw: bytes) -> str:
    """Decode a header line, preferring UTF-8 over the latin-1 fallback.

    The builder encodes the parameters as UTF-8, so decoding them the same
    way keeps a non ASCII file name readable; latin-1 is only used when the
    bytes are not valid UTF-8.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _parse_part_headers(raw: bytes) -> email.message.Message:
    """Parse the header block of a single multipart part."""
    message = email.message.Message()
    for line in raw.split(b"\r\n"):
        if not line.strip():
            continue
        name, _, value = _decode_header_bytes(line).partition(":")
        message[name.strip()] = value.strip()
    return message


def _header_parameter(message: email.message.Message, parameter: str) -> str | None:
    """Read a parameter of the ``Content-Disposition`` header."""
    value = message.get_param(parameter, header="content-disposition")
    if value is None:
        return None
    if isinstance(value, tuple):
        value = email.utils.collapse_rfc2231_value(value)
    return str(value)


def parse_multipart(body: bytes, content_type: str) -> dict[str, list[MultipartPart]]:
    """Split a multipart body into its parts.

    The body is split on the raw boundary bytes, so a part that contains
    every byte value from ``0x00`` to ``0xFF`` is returned unchanged.

    Args:
        body: The raw multipart body.
        content_type: The ``Content-Type`` header value carrying the boundary.

    Returns:
        A mapping of field name to the list of parts with that name.

    Raises:
        ValueError: If the content type carries no boundary.
    """
    boundary = _boundary_from_content_type(content_type)
    delimiter = b"--" + boundary
    result: dict[str, list[MultipartPart]] = {}
    for segment in body.split(delimiter)[1:]:
        if segment.startswith(b"--"):
            # Closing delimiter, everything after it is the epilogue.
            break
        if segment.startswith(b"\r\n"):
            segment = segment[2:]
        elif segment.startswith(b"\n"):
            segment = segment[1:]
        else:
            continue
        separator = segment.find(b"\r\n\r\n")
        header_length = 4
        if separator < 0:
            separator = segment.find(b"\n\n")
            header_length = 2
            if separator < 0:
                continue
        header_block = segment[:separator]
        data = segment[separator + header_length:]
        # The CRLF in front of the next delimiter belongs to the framing.
        if data.endswith(b"\r\n"):
            data = data[:-2]
        elif data.endswith(b"\n"):
            data = data[:-1]
        headers = _parse_part_headers(header_block)
        disposition = headers.get("Content-Disposition", "")
        part_message = email.message.Message()
        part_message["Content-Disposition"] = disposition
        name = _header_parameter(part_message, "name") or ""
        filename = _header_parameter(part_message, "filename")
        part = MultipartPart(
            name=name,
            filename=filename,
            content_type=headers.get("Content-Type"),
            data=data,
        )
        result.setdefault(name, []).append(part)
    return result
