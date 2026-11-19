"""The loopback server releases its thread and its port (AC-11)."""

import socket
import threading
import time
import unittest

from httpbinary import BinaryTestServer, post_binary, request_binary


class ServerLifecycleTests(unittest.TestCase):
    """Starting and stopping the server must be deterministic."""

    def test_server_shutdown_releases_thread_and_port(self):
        """AC-11: no thread survives and the port can be bound again."""
        server = BinaryTestServer()
        server.start()
        port = server.port
        thread = server.server_thread
        self.assertIsNotNone(thread)
        self.assertTrue(thread.is_alive())
        self.assertEqual(request_binary(server.base_url + "/bytes/16", method="GET").length, 16)

        server.stop()
        self.assertFalse(thread.is_alive())

        # The very same port must be usable again right away.
        second = BinaryTestServer(port=port)
        second.start()
        try:
            self.assertEqual(second.port, port)
            self.assertEqual(
                request_binary(second.base_url + "/bytes/16", method="GET").length, 16
            )
        finally:
            second.stop()

    def test_context_manager_stops_the_server(self):
        """AC-11: the context manager releases the resources on exit."""
        with BinaryTestServer() as server:
            thread = server.server_thread
            self.assertTrue(thread.is_alive())
            base_url = server.base_url
        self.assertFalse(thread.is_alive())
        with self.assertRaises(RuntimeError):
            server.port
        self.assertTrue(base_url.startswith("http://127.0.0.1:"))

    def test_stop_is_idempotent(self):
        """C-7: stopping twice must not raise."""
        server = BinaryTestServer()
        server.start()
        server.stop()
        server.stop()
        self.assertFalse(server.is_running)

    def test_non_loopback_host_is_rejected(self):
        """C-1: the server refuses to bind anything but the loopback interface."""
        with self.assertRaises(ValueError):
            BinaryTestServer(host="0.0.0.0")

    def test_client_timeout_is_enforced(self):
        """C-5: a silent server makes the client give up."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        accepted = []

        def accept_and_hold():
            try:
                connection, _ = listener.accept()
                accepted.append(connection)
                threading.Event().wait(5)
            except OSError:
                pass

        worker = threading.Thread(target=accept_and_hold, daemon=True)
        worker.start()
        try:
            with self.assertRaises(TimeoutError):
                request_binary(
                    f"http://127.0.0.1:{port}/echo", b"x", method="POST", timeout=0.5
                )
        finally:
            listener.close()
            worker.join(timeout=5)
            for connection in accepted:
                connection.close()


class RepeatedRunTests(unittest.TestCase):
    """The server can be started and stopped repeatedly."""

    def test_repeated_start_stop(self):
        """AC-11: repeated cycles do not leak ports or threads."""
        before = {thread.ident for thread in threading.enumerate()}
        for _ in range(3):
            with BinaryTestServer() as server:
                post_binary(server.base_url + "/echo", b"payload")
                self.assertTrue(server.server_thread.is_alive())
            # The server thread must be gone once the context manager exits.
            self.assertFalse(server.server_thread.is_alive())
        after = {thread.ident for thread in threading.enumerate()}
        # A thread that appears during the cycles and is still alive at the
        # end is a leak.  The comparison has to look at the threads that were
        # *added*; ``before.issubset(after)`` would hold for any leak.
        self.assertEqual(after - before, set())

    def test_no_request_thread_outlives_stop(self):
        """C-7: an in-flight request thread does not outlive the server."""
        server = BinaryTestServer()
        server.start()
        connection = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        # A request that announces a body it never sends: the handler stays
        # blocked on the body until the server is stopped.
        connection.sendall(
            b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\nabc"
        )
        time.sleep(0.2)
        try:
            server.stop()
            alive = [
                thread.name
                for thread in threading.enumerate()
                if thread.is_alive() and "process_request_thread" in thread.name
            ]
            self.assertEqual(alive, [])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
