"""Byte fidelity of the request and the response path (AC-2, AC-3)."""

import hashlib
import unittest

from httpbinary import BinaryTestServer, deterministic_bytes, post_binary, request_binary


def binary_payload() -> bytes:
    """Build a payload of at least 10 KiB covering every byte value."""
    return b"".join(
        [
            bytes(range(256)) * 40,          # 10240 bytes, every byte value
            b"\xff\xfe\x00\x01",             # not valid UTF-8
            "中文 UTF-8 载荷".encode("utf-8"),  # multi byte UTF-8
            bytes(range(255, -1, -1)),
        ]
    )


class FidelityTests(unittest.TestCase):
    """The payload must survive the round trip unchanged."""

    @classmethod
    def setUpClass(cls):
        cls.server = BinaryTestServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_request_body_byte_fidelity(self):
        """AC-2: the request body arrives at the server unchanged."""
        payload = binary_payload()
        self.assertGreaterEqual(len(payload), 10 * 1024)
        response = post_binary(self.server.base_url + "/echo", payload)
        self.assertEqual(response.body, payload)
        self.assertEqual(response.sha256, hashlib.sha256(payload).hexdigest())

    def test_response_body_byte_fidelity(self):
        """AC-3: the response body is returned as the exact raw bytes."""
        response = request_binary(self.server.base_url + "/blob", method="GET")
        self.assertEqual(set(response.body), set(range(256)))
        self.assertIn(b"\xff\xfe\x00\x01", response.body)
        self.assertIn("中文 UTF-8 载荷".encode("utf-8"), response.body)
        # A payload that is not valid UTF-8 must not have been decoded.
        with self.assertRaises(UnicodeDecodeError):
            response.body.decode("utf-8")

    def test_response_bytes_are_deterministic(self):
        """AC-3: the same size always yields the same bytes."""
        first = request_binary(self.server.base_url + "/bytes/4096", method="GET")
        second = request_binary(self.server.base_url + "/bytes/4096", method="GET")
        self.assertEqual(first.body, second.body)
        self.assertEqual(first.body, deterministic_bytes(4096))
        self.assertEqual(first.sha256, hashlib.sha256(deterministic_bytes(4096)).hexdigest())

    def test_str_payload_is_rejected(self):
        """C-4: a str payload is refused instead of being encoded silently."""
        with self.assertRaises(TypeError):
            post_binary(self.server.base_url + "/echo", "not bytes")


if __name__ == "__main__":
    unittest.main()
