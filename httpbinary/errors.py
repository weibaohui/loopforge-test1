"""Exception hierarchy and shared limits for the :mod:`httpbinary` package.

The whole package is built around one rule: a payload is always ``bytes`` and
never a ``str``.  Every failure mode that a caller may reasonably want to
handle therefore derives from :class:`HTTPBinaryError`, so a single
``except HTTPBinaryError`` clause is enough to catch anything this library
raises on purpose.
"""

__all__ = [
    "MAX_BODY_BYTES",
    "HTTPBinaryError",
    "BodyTooLargeError",
    "BinaryAssertionError",
]

#: Default in-memory ceiling (8 MiB) for request and response bodies.
#:
#: The limit is enforced *while* the body is read, never after the whole
#: payload has been buffered, so exceeding it cannot exhaust memory.
MAX_BODY_BYTES: int = 8 * 1024 * 1024


class HTTPBinaryError(Exception):
    """Base class for every error raised on purpose by this library."""


class BodyTooLargeError(HTTPBinaryError):
    """Raised when a body exceeds the configured in-memory ceiling.

    Instances carry two attributes that make the "the body was never fully
    buffered" guarantee observable:

    ``limit``
        The ceiling that was exceeded, in bytes.
    ``bytes_read``
        How many body bytes had been retained in memory when the limit was
        detected.  For the ``Content-Length`` pre-check path this is ``0``.
    """

    #: Ceiling that was exceeded, in bytes (``None`` when not set).
    limit = None
    #: Number of body bytes retained in memory before the failure.
    bytes_read = None


class BinaryAssertionError(AssertionError, HTTPBinaryError):
    """Raised by the assertion helpers when a check does not hold.

    It subclasses :class:`AssertionError` so that ``unittest`` reports a
    failure (rather than an error) for a failed assertion, and it subclasses
    :class:`HTTPBinaryError` so that callers may catch every library error
    with a single clause.
    """
