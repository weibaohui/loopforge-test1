"""The body size ceiling is enforced before the body is buffered (AC-9)."""

import unittest

from httpbinary import (
    BinaryTestServer,
    BodyTooLargeError,
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
        # Nothing of the body was kept in memory: the pre-check path reads
        # the headers only.
        self.assertEqual(error.bytes_read, 0)
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
        self.assertLessEqual(error.bytes_read, 1000)
        self.assertGreaterEqual(error.bytes_read, 0)

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
