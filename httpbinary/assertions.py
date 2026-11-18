"""Assertion helpers with byte-level failure diagnostics.

Every helper is a plain module-level function so that it can be used from
``unittest``, ``pytest`` or any other runner without extra plumbing.  A
failed check always raises
:class:`~httpbinary.errors.BinaryAssertionError`.

The centrepiece is :func:`assert_body_equals`: when two payloads differ it
reports the first differing offset plus a hexadecimal window around it, which
turns "the bodies differ" into "the bodies differ starting at byte 4096".
"""

from __future__ import annotations

from .errors import BinaryAssertionError

__all__ = [
    "assert_status",
    "assert_header",
    "assert_body_equals",
    "assert_body_length",
    "assert_body_sha256",
]

#: Number of bytes shown on each side of the first differing offset.
_CONTEXT_SPAN = 8

#: Minimum number of bytes in a context window, when the payload allows it.
_MIN_WINDOW = 16


def assert_status(resp, expected: int) -> None:
    """Assert that the response status code equals ``expected``.

    Args:
        resp: The :class:`~httpbinary.client.BinaryResponse` to check.
        expected: The expected HTTP status code.

    Raises:
        BinaryAssertionError: If the status code differs.
    """
    if resp.status != expected:
        raise BinaryAssertionError(
            f"expected status {expected}, actual status {resp.status}"
        )


def assert_header(resp, name: str, expected: str) -> None:
    """Assert that a response header equals ``expected``.

    The header name is matched case-insensitively; the value is compared
    verbatim.

    Args:
        resp: The :class:`~httpbinary.client.BinaryResponse` to check.
        name: Header name, matched case-insensitively.
        expected: The expected header value.

    Raises:
        BinaryAssertionError: If the header is missing or has another value.
    """
    actual = resp.headers.get(name)
    if actual is None:
        raise BinaryAssertionError(
            f"expected header {name!r} to be {expected!r}, but the header is missing"
        )
    if actual != expected:
        raise BinaryAssertionError(
            f"expected header {name!r} to be {expected!r}, actual {actual!r}"
        )


def assert_body_equals(resp, expected: bytes) -> None:
    """Assert that the response body is byte-for-byte equal to ``expected``.

    On failure the message contains the first differing offset, both
    lengths and a hexadecimal context window around the difference:

    .. code-block:: text

        binary body mismatch at offset 42
          expected length: 10250, actual length: 10250
          expected[34..51]: 00 01 02 03 04 05 06 07 ...
          actual  [34..51]: 00 01 02 03 04 05 06 ff ...

    Args:
        resp: The :class:`~httpbinary.client.BinaryResponse` to check.
        expected: The expected response body bytes.

    Raises:
        BinaryAssertionError: If the bodies differ.
    """
    expected_bytes = bytes(expected)
    actual_bytes = resp.body
    if actual_bytes == expected_bytes:
        return
    offset = _first_difference(expected_bytes, actual_bytes)
    raise BinaryAssertionError(
        _format_body_mismatch(expected_bytes, actual_bytes, offset)
    )


def assert_body_length(resp, expected: int) -> None:
    """Assert that the response body has exactly ``expected`` bytes.

    Args:
        resp: The :class:`~httpbinary.client.BinaryResponse` to check.
        expected: The expected body length in bytes.

    Raises:
        BinaryAssertionError: If the length differs.
    """
    if resp.length != expected:
        raise BinaryAssertionError(
            f"expected body length {expected}, actual body length {resp.length}"
        )


def assert_body_sha256(resp, expected_hex: str) -> None:
    """Assert the SHA-256 digest of the response body.

    The expected digest is compared case-insensitively.

    Args:
        resp: The :class:`~httpbinary.client.BinaryResponse` to check.
        expected_hex: The expected hexadecimal SHA-256 digest.

    Raises:
        BinaryAssertionError: If the digest differs.
    """
    expected = str(expected_hex).strip().lower()
    actual = resp.sha256.lower()
    if expected != actual:
        raise BinaryAssertionError(
            f"expected body sha256 {expected}, actual {actual}"
        )


def _first_difference(expected: bytes, actual: bytes) -> int:
    """Return the 0-based offset of the first differing byte.

    When one payload is a prefix of the other the offset is the length of
    the shorter payload, which is where the two sequences start to differ.
    """
    shared = min(len(expected), len(actual))
    for index in range(shared):
        if expected[index] != actual[index]:
            return index
    return shared


def _window_bounds(length: int, offset: int, longest: int) -> tuple[int, int]:
    """Compute the ``(start, end)`` bounds of the context window.

    The window is centred on ``offset`` and spans ``_CONTEXT_SPAN`` bytes on
    each side.  When the payload is too short to show that much, the window
    shrinks towards the inside and keeps at least ``_MIN_WINDOW`` bytes when
    the payload is long enough.  ``start`` is shared by both sides of the
    comparison so that the two hexadecimal dumps stay aligned.
    """
    start = offset - _CONTEXT_SPAN
    if start < 0:
        start = 0
    if start > 0 and longest - start < _MIN_WINDOW and longest >= _MIN_WINDOW:
        start = longest - _MIN_WINDOW
    if start < 0:
        start = 0
    end = start + 2 * _CONTEXT_SPAN + 1
    if end > length:
        end = length
    if end - start < _MIN_WINDOW and length >= _MIN_WINDOW and start == 0:
        end = min(length, _MIN_WINDOW)
    if end < start:
        end = start
    return start, end


def _hex_window(data: bytes, start: int, end: int, truncated_left: bool,
                truncated_right: bool) -> str:
    """Render ``data[start:end]`` as space separated fixed-width hex bytes."""
    parts: list[str] = []
    if truncated_left:
        parts.append("...")
    parts.extend(f"{value:02x}" for value in data[start:end])
    if truncated_right:
        parts.append("...")
    return " ".join(parts)


def _format_body_mismatch(expected: bytes, actual: bytes, offset: int) -> str:
    """Build the failure message of :func:`assert_body_equals`."""
    longest = max(len(expected), len(actual))
    start, expected_end = _window_bounds(len(expected), offset, longest)
    _, actual_end = _window_bounds(len(actual), offset, longest)
    # Both sides must start at the same offset so that the dumps line up.
    actual_start = start
    expected_hex = _hex_window(
        expected, start, expected_end, start > 0, expected_end < len(expected)
    )
    actual_hex = _hex_window(
        actual, actual_start, actual_end, actual_start > 0, actual_end < len(actual)
    )
    return (
        f"binary body mismatch at offset {offset}\n"
        f"  expected length: {len(expected)}, actual length: {len(actual)}\n"
        f"  {'expected':<8}[{start}..{max(expected_end - 1, start)}]: {expected_hex}\n"
        f"  {'actual':<8}[{actual_start}..{max(actual_end - 1, actual_start)}]: {actual_hex}"
    )
