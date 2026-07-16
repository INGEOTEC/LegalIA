import os
import unittest
from unittest.mock import MagicMock, patch

from dof2md.mineru_server import ENV_VAR, MineruServer, _wait_until_healthy


def _fake_response(status_code):
    response = MagicMock()
    response.status_code = status_code
    return response


class TestWaitUntilHealthy(unittest.TestCase):
    @patch("dof2md.mineru_server.requests.get", return_value=_fake_response(200))
    def test_returns_once_healthy(self, mock_get):
        _wait_until_healthy("http://127.0.0.1:1234", timeout=5)
        mock_get.assert_called_once_with("http://127.0.0.1:1234/health", timeout=2)

    @patch("dof2md.mineru_server.time.sleep")
    @patch("dof2md.mineru_server.requests.get", return_value=_fake_response(503))
    def test_raises_after_timeout_if_never_healthy(self, mock_get, mock_sleep):
        with self.assertRaises(RuntimeError):
            _wait_until_healthy("http://127.0.0.1:1234", timeout=0)


class TestMineruServer(unittest.TestCase):
    def setUp(self):
        self._original_env = os.environ.pop(ENV_VAR, None)

    def tearDown(self):
        if self._original_env is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = self._original_env

    @patch("dof2md.mineru_server._wait_until_healthy")
    @patch("dof2md.mineru_server.subprocess.Popen")
    def test_start_sets_env_var_and_stop_clears_it(self, mock_popen, mock_wait):
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        server = MineruServer(port=9123)
        server.start()

        self.assertEqual(os.environ[ENV_VAR], "http://127.0.0.1:9123")
        cmd = mock_popen.call_args[0][0]
        self.assertEqual(cmd, ["mineru-api", "--host", "127.0.0.1", "--port", "9123"])

        server.stop()
        self.assertNotIn(ENV_VAR, os.environ)
        mock_process.terminate.assert_called_once()

    @patch("dof2md.mineru_server._wait_until_healthy")
    @patch("dof2md.mineru_server.subprocess.Popen")
    def test_stop_restores_previous_env_var(self, mock_popen, mock_wait):
        os.environ[ENV_VAR] = "http://preexisting:8000"
        mock_popen.return_value = MagicMock()

        server = MineruServer(port=9124)
        server.start()
        self.assertEqual(os.environ[ENV_VAR], "http://127.0.0.1:9124")

        server.stop()
        self.assertEqual(os.environ[ENV_VAR], "http://preexisting:8000")

    @patch("dof2md.mineru_server._wait_until_healthy", side_effect=RuntimeError("never healthy"))
    @patch("dof2md.mineru_server.subprocess.Popen")
    def test_start_stops_process_if_health_check_fails(self, mock_popen, mock_wait):
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        server = MineruServer(port=9125)
        with self.assertRaises(RuntimeError):
            server.start()

        mock_process.terminate.assert_called_once()
        self.assertNotIn(ENV_VAR, os.environ)

    @patch("dof2md.mineru_server._wait_until_healthy")
    @patch("dof2md.mineru_server.subprocess.Popen")
    def test_used_as_context_manager(self, mock_popen, mock_wait):
        mock_popen.return_value = MagicMock()

        with MineruServer(port=9126) as server:
            self.assertEqual(os.environ[ENV_VAR], server.base_url)

        self.assertNotIn(ENV_VAR, os.environ)


if __name__ == "__main__":
    unittest.main()
