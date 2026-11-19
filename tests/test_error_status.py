"""Error responses are returned, not raised, unless asked for (AC-8)."""

import unittest

from httpbinary import (
    BinaryAssertionError,
    BinaryTestServer,
    HTTPBinaryError,
    assert_status,
    post_binary,
    request_binary,
)


class ErrorStatusTests(unittest.TestCase):
    """A 4xx or 5xx response is a normal response by default."""

    @classmethod
    def setUpClass(cls):
        cls.server = BinaryTestServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_error_status_does_not_raise_by_default(self):
        """AC-8: a 500 response is returned and reported by the assertion."""
        response = request_binary(self.server.base_url + "/status/500", method="GET")
        self.assertEqual(response.status, 500)
        self.assertEqual(response.body, b"")
        with self.assertRaises(BinaryAssertionError):
            assert_status(response, 200)

    def test_client_error_is_not_raised_by_default(self):
        """AC-8: a 404 response is returned as well."""
        response = request_binary(self.server.base_url + "/status/404", method="GET")
        self.assertEqual(response.status, 404)

    def test_raise_for_status_raises(self):
        """AC-8: the caller can opt into raising on error responses."""
        with self.assertRaises(HTTPBinaryError) as caught:
            request_binary(
                self.server.base_url + "/status/500", method="GET", raise_for_status=True
            )
        self.assertEqual(caught.exception.status, 500)

    def test_raise_for_status_does_not_raise_on_success(self):
        """AC-8: successful responses are still returned."""
        response = post_binary(
            self.server.base_url + "/echo", b"ok", raise_for_status=True
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"ok")

    def test_assert_status_passes_for_matching_status(self):
        """AC-8: the assertion helper reports a matching status."""
        response = request_binary(self.server.base_url + "/status/503", method="GET")
        assert_status(response, 503)


if __name__ == "__main__":
    unittest.main()
