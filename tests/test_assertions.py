"""The assertion helpers and their byte level diagnostics (AC-4, AC-5)."""

import hashlib
import unittest

from httpbinary import (
    BinaryAssertionError,
    BinaryResponse,
    HTTPBinaryError,
    assert_body_equals,
    assert_body_length,
    assert_body_sha256,
    assert_header,
    assert_status,
)


def make_response(body: bytes, status: int = 200, headers=None) -> BinaryResponse:
    """Build a response object without touching the network."""
    return BinaryResponse(
        status=status,
        headers=headers or {"Content-Type": "application/octet-stream"},
        body=body,
    )


class AssertionTests(unittest.TestCase):
    """Every helper must pass on a matching value and fail loudly otherwise."""

    def test_all_assertion_functions_pass_path(self):
        """AC-4: the happy path of the five helpers must not raise."""
        body = bytes(range(256))
        response = make_response(body)
        assert_status(response, 200)
        assert_header(response, "Content-Type", "application/octet-stream")
        assert_body_equals(response, body)
        assert_body_length(response, len(body))
        assert_body_sha256(response, hashlib.sha256(body).hexdigest())

    def test_all_assertion_functions_fail_path(self):
        """AC-4: every helper must raise BinaryAssertionError when it fails."""
        response = make_response(b"payload")
        checks = [
            lambda: assert_status(response, 404),
            lambda: assert_header(response, "Content-Type", "text/plain"),
            lambda: assert_header(response, "X-Missing", "value"),
            lambda: assert_body_equals(response, b"other"),
            lambda: assert_body_length(response, 99),
            lambda: assert_body_sha256(response, "00" * 32),
        ]
        for check in checks:
            with self.assertRaises(BinaryAssertionError):
                check()

    def test_assertion_error_is_an_assertion_error(self):
        """AC-4: the failure must be counted as a failure by unittest."""
        self.assertTrue(issubclass(BinaryAssertionError, AssertionError))
        self.assertTrue(issubclass(BinaryAssertionError, HTTPBinaryError))

    def test_assert_header_is_case_insensitive(self):
        """AC-4: the header name is matched case-insensitively."""
        response = make_response(b"", headers={"content-type": "application/json"})
        assert_header(response, "Content-Type", "application/json")
        assert_header(response, "CONTENT-TYPE", "application/json")

    def test_assert_body_sha256_tolerates_upper_case(self):
        """AC-4: the expected digest may be given in upper case."""
        body = b"payload"
        digest = hashlib.sha256(body).hexdigest()
        assert_body_sha256(make_response(body), digest.upper())

    def test_assert_body_equals_reports_first_diff_offset(self):
        """AC-5: the failure message points at the first differing byte."""
        payload = bytes(range(256)) * 40 + b"\xff\xfe\x00\x01"
        expected = bytearray(payload)
        expected[1] = 0xAA
        expected = bytes(expected)
        actual = payload

        with self.assertRaises(BinaryAssertionError) as caught:
            assert_body_equals(make_response(actual), expected)
        message = str(caught.exception)

        self.assertIn("binary body mismatch at offset 1", message)
        self.assertIn("expected length: 10244, actual length: 10244", message)
        expected_hex = " ".join(f"{value:02x}" for value in expected[:17])
        actual_hex = " ".join(f"{value:02x}" for value in actual[:17])
        self.assertIn(expected_hex, message)
        self.assertIn(actual_hex, message)
        # The context window covers at least 16 bytes on both sides.
        self.assertGreaterEqual(len(expected_hex.split()), 16)
        self.assertGreaterEqual(len(actual_hex.split()), 16)
        self.assertIn("expected[0..16]", message)
        self.assertIn("actual  [0..16]", message)

    def test_assert_body_equals_reports_offset_zero(self):
        """AC-5: a difference in the very first byte is reported as offset 0."""
        with self.assertRaises(BinaryAssertionError) as caught:
            assert_body_equals(make_response(b"\x01" + b"x" * 40), b"\x00" + b"x" * 40)
        message = str(caught.exception)
        self.assertIn("binary body mismatch at offset 0", message)
        self.assertIn("expected length: 41, actual length: 41", message)

    def test_assert_body_equals_reports_length_difference(self):
        """AC-5: a truncated payload is reported at the end of the shorter one."""
        with self.assertRaises(BinaryAssertionError) as caught:
            assert_body_equals(make_response(b"abc"), b"abcd")
        message = str(caught.exception)
        self.assertIn("binary body mismatch at offset 3", message)
        self.assertIn("expected length: 4, actual length: 3", message)

    def test_assert_body_equals_short_payload(self):
        """AC-5: a short payload is shown completely."""
        with self.assertRaises(BinaryAssertionError) as caught:
            assert_body_equals(make_response(b"\x00\x01"), b"\x00\x02")
        message = str(caught.exception)
        self.assertIn("expected length: 2, actual length: 2", message)
        self.assertIn("00 01", message)
        self.assertIn("00 02", message)

    def test_assert_body_equals_rejects_a_str_expectation(self):
        """L-9: a str expectation is refused instead of being encoded."""
        with self.assertRaises(TypeError) as caught:
            assert_body_equals(make_response(b"abc"), "abc")
        self.assertIn("str", str(caught.exception))

    def test_assert_body_equals_rejects_a_non_bytes_expectation(self):
        """L-9: only bytes-like expectations are accepted."""
        with self.assertRaises(TypeError):
            assert_body_equals(make_response(b"abc"), 3)

    def test_empty_window_is_labelled_honestly(self):
        """L-10: an empty context window does not claim a byte."""
        with self.assertRaises(BinaryAssertionError) as caught:
            assert_body_equals(make_response(b"abc"), b"")
        message = str(caught.exception)
        self.assertIn("expected[0..-1]", message)
        self.assertNotIn("expected[0..0]", message)


if __name__ == "__main__":
    unittest.main()
