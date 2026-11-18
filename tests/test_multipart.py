"""Multipart uploads keep the file part byte exact (AC-6)."""

import hashlib
import unittest

from httpbinary import (
    BinaryTestServer,
    build_multipart,
    parse_multipart,
    post_binary,
)


def file_payload() -> bytes:
    """Build a file payload covering every byte value."""
    return b"".join(
        [
            bytes(range(256)),
            b"\xff\xfe\x00\x01",
            "中文 UTF-8 载荷".encode("utf-8"),
        ]
    )


class MultipartTests(unittest.TestCase):
    """The bytes of a file part must arrive unchanged."""

    @classmethod
    def setUpClass(cls):
        cls.server = BinaryTestServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_multipart_binary_file_part_fidelity(self):
        """AC-6: the file part reaches the server byte for byte."""
        payload = file_payload()
        body, content_type = build_multipart(
            {"field": "value"},
            {"upload": ("payload.bin", payload, "application/octet-stream")},
        )
        response = post_binary(
            self.server.base_url + "/multipart",
            body,
            headers={"Content-Type": content_type},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, payload)
        self.assertEqual(response.headers.get("X-Part-Filename"), "payload.bin")
        self.assertEqual(
            response.headers.get("X-Part-Sha256"), hashlib.sha256(payload).hexdigest()
        )

    def test_build_and_parse_roundtrip(self):
        """AC-6: a locally built body can be parsed back into the same bytes."""
        payload = file_payload()
        body, content_type = build_multipart(
            {"field": "value", "other": "text"},
            {"upload": ("payload.bin", payload, "application/octet-stream")},
        )
        parts = parse_multipart(body, content_type)
        self.assertEqual(parts["field"][0].data, b"value")
        self.assertEqual(parts["other"][0].data, b"text")
        self.assertEqual(parts["upload"][0].data, payload)
        self.assertEqual(parts["upload"][0].filename, "payload.bin")
        self.assertEqual(parts["upload"][0].content_type, "application/octet-stream")

    def test_boundary_conflict_is_resolved(self):
        """AC-6: a boundary that occurs in the payload is replaced safely."""
        payload = b"prefix-CONFLICT-suffix"
        body, content_type = build_multipart(
            {}, {"upload": ("f.bin", payload, "application/octet-stream")},
            boundary=b"CONFLICT",
        )
        self.assertNotIn("boundary=CONFLICT", content_type)
        parts = parse_multipart(body, content_type)
        self.assertEqual(parts["upload"][0].data, payload)

    def test_multiple_parts_with_the_same_name(self):
        """AC-6: repeated field names keep their parts in order."""
        body, content_type = build_multipart(
            {},
            {
                "a": ("one.bin", b"\x00\x01", "application/octet-stream"),
                "b": ("two.bin", b"\x02\x03", "application/octet-stream"),
            },
        )
        parts = parse_multipart(body, content_type)
        self.assertEqual(parts["a"][0].data, b"\x00\x01")
        self.assertEqual(parts["b"][0].data, b"\x02\x03")

    def test_file_part_must_be_bytes(self):
        """C-4: a str file payload is refused instead of being encoded."""
        with self.assertRaises(TypeError):
            build_multipart({}, {"upload": ("f.bin", "text", "text/plain")})


if __name__ == "__main__":
    unittest.main()
