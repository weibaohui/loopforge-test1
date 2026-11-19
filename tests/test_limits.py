"""The body size ceiling is enforced before the body is buffered (AC-9)."""

import socket
import unittest

from httpbinary import (
    BinaryTestServer,
    BodyTooLargeError,
    HTTPBinaryError,
    request_binary,
)


class LimitTests(unittest.TestCase):
    """An oversized body must be rejected, not buffered."""

    @classmethod
    def setUpClass(cls):
        cls.server = BinaryTestServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_body_size_limit_enforced(self):
        """AC-9: the declared length is checked before any body byte is read."""
        with self.assertRaises(BodyTooLargeError) as caught:
            request_binary(
                self.server.base_url + "/bytes/100000",
                method="GET",
                max_body_bytes=1000,
            )
        error = caught.exception
        self.assertEqual(error.limit, 1000)
        # Nothing of the body was read or kept in memory: the pre-check path
        # reads the headers only.
        self.assertEqual(error.bytes_read, 0)
        self.assertEqual(error.bytes_retained, 0)
        self.assertIn("1000", str(error))

    def test_chunked_body_size_limit_enforced(self):
        """AC-9: a chunked body is counted while it is read."""
        with self.assertRaises(BodyTooLargeError) as caught:
            request_binary(
                self.server.base_url + "/chunked/100000",
                method="GET",
                max_body_bytes=1000,
            )
        error = caught.exception
        self.assertEqual(error.limit, 1000)
        # The bytes kept in memory never exceed the ceiling ...
        self.assertLessEqual(error.bytes_retained, 1000)
        # ... and the body was not read to its end: at most one read block
        # beyond the ceiling was pulled off the wire.
        self.assertLess(error.bytes_read, 100000)
        self.assertLessEqual(error.bytes_read, 1000 + 65536)

    def test_request_body_limit_is_checked_before_sending(self):
        """C-3: an oversized request body never reaches the wire."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            with self.assertRaises(BodyTooLargeError) as caught:
                request_binary(
                    f"http://127.0.0.1:{port}/echo",
                    b"x" * (2 * 1024 * 1024),
                    method="POST",
                    max_body_bytes=1024,
                    timeout=5.0,
                )
            self.assertEqual(caught.exception.limit, 1024)
            self.assertEqual(caught.exception.bytes_read, 0)
            # No connection was opened at all: the size is checked first.
            listener.settimeout(0.5)
            with self.assertRaises(TimeoutError):
                listener.accept()
        finally:
            listener.close()

    def test_oversized_request_gets_a_clean_413(self):
        """H-1: the server drains the body, so the client sees a 413."""
        response = request_binary(
            self.server.base_url + "/echo",
            b"x" * (10 * 1024 * 1024),
            method="POST",
            # The client accepts more than the server does, so the request
            # really goes out and the server has to reject it.
            max_body_bytes=16 * 1024 * 1024,
            timeout=30.0,
        )
        self.assertEqual(response.status, 413)
        self.assertEqual(response.body, b"payload too large\n")

    def test_transport_failure_is_a_library_error(self):
        """L-7: a transport failure is reported as an HTTPBinaryError."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        with self.assertRaises(HTTPBinaryError):
            request_binary(
                f"http://127.0.0.1:{port}/echo", b"x", method="POST", timeout=5.0
            )

    def test_body_within_the_limit_is_returned(self):
        """AC-9: a body below the ceiling is returned unchanged."""
        response = request_binary(
            self.server.base_url + "/bytes/500", method="GET", max_body_bytes=1000
        )
        self.assertEqual(response.length, 500)

    def test_default_limit_is_eight_mebibytes(self):
        """AC-9: the documented default ceiling is in effect."""
        from httpbinary import MAX_BODY_BYTES

        self.assertEqual(MAX_BODY_BYTES, 8 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
