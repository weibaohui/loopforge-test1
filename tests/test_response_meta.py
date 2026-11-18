"""Response metadata: length, digest and content type (AC-3, AC-10)."""

import hashlib
import unittest

from httpbinary import BinaryTestServer, deterministic_bytes, request_binary


class ResponseMetaTests(unittest.TestCase):
    """Empty and one byte responses must behave like any other response."""

    @classmethod
    def setUpClass(cls):
        cls.server = BinaryTestServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_empty_body(self):
        """AC-10: an empty body has length zero and does not raise."""
        response = request_binary(self.server.base_url + "/status/204", method="GET")
        self.assertEqual(response.status, 204)
        self.assertEqual(response.body, b"")
        self.assertEqual(response.length, 0)
        self.assertEqual(response.sha256, hashlib.sha256(b"").hexdigest())

    def test_single_byte_body(self):
        """AC-10: a one byte body has length one and does not raise."""
        response = request_binary(self.server.base_url + "/bytes/1", method="GET")
        self.assertEqual(response.length, 1)
        self.assertEqual(response.body, deterministic_bytes(1))
        self.assertEqual(response.sha256, hashlib.sha256(response.body).hexdigest())

    def test_content_type_and_headers(self):
        """AC-3: the response metadata is exposed and looked up case-insensitively."""
        response = request_binary(self.server.base_url + "/blob", method="GET")
        self.assertEqual(response.content_type, "application/octet-stream")
        self.assertEqual(response.headers.get("content-type"), "application/octet-stream")
        self.assertEqual(response.headers["CONTENT-TYPE"], "application/octet-stream")
        self.assertIsInstance(response.body, bytes)

    def test_header_echo_route(self):
        """AC-2: the request headers reach the server unchanged."""
        response = request_binary(
            self.server.base_url + "/headers",
            method="GET",
            headers={"X-Custom-Header": "custom-value"},
        )
        self.assertIn(b"custom-value", response.body)


if __name__ == "__main__":
    unittest.main()
