"""Chunked transfer encoding, in both directions (AC-7)."""

import json
import unittest

from httpbinary import (
    BinaryTestServer,
    deterministic_bytes,
    post_binary,
    request_binary,
)


class ChunkedTests(unittest.TestCase):
    """Chunked bodies must be framed and decoded by the library itself."""

    @classmethod
    def setUpClass(cls):
        cls.server = BinaryTestServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_chunked_request_body_roundtrip(self):
        """AC-7: a chunked request body arrives at the server unchanged."""
        payload = bytes(range(256)) * 3 + b"\xff\xfe\x00\x01"
        response = post_binary(
            self.server.base_url + "/echo", payload, chunked=True, timeout=5.0
        )
        self.assertEqual(response.body, payload)

    def test_chunked_request_body_is_larger_than_one_chunk(self):
        """AC-7: a payload spanning several chunks is decoded correctly."""
        payload = deterministic_bytes(200000)
        response = post_binary(
            self.server.base_url + "/echo", payload, chunked=True, timeout=10.0
        )
        self.assertEqual(response.body, payload)

    def test_chunked_response_is_decoded(self):
        """AC-3: a chunked response is reassembled into the exact bytes."""
        response = request_binary(self.server.base_url + "/chunked/8192", method="GET")
        self.assertEqual(response.body, deterministic_bytes(8192))

    def test_chunked_request_does_not_hang(self):
        """AC-7: the request completes inside the timeout budget."""
        response = post_binary(
            self.server.base_url + "/echo", b"x" * 10, chunked=True, timeout=5.0
        )
        self.assertEqual(response.body, b"x" * 10)

    def test_request_body_is_really_sent_as_chunks(self):
        """AC-7: the framing is done by the library, not by the standard library."""
        response = post_binary(
            self.server.base_url + "/headers", b"payload", chunked=True, timeout=5.0
        )
        headers = json.loads(response.body.decode("utf-8"))
        self.assertEqual(headers.get("Transfer-Encoding"), "chunked")
        self.assertNotIn("Content-Length", headers)

    def test_plain_request_uses_content_length(self):
        """AC-7: a normal request is framed with a content length."""
        response = post_binary(
            self.server.base_url + "/headers", b"payload", timeout=5.0
        )
        headers = json.loads(response.body.decode("utf-8"))
        self.assertEqual(headers.get("Content-Length"), "7")
        self.assertNotIn("Transfer-Encoding", headers)


if __name__ == "__main__":
    unittest.main()
