import os
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestLauncherConfig(unittest.TestCase):
    @patch("subprocess.call")
    @patch("launcher.start_proxy_thread")
    @patch("launcher.wait_for_proxy_health", return_value=True)
    @patch("launcher.link_sandbox_directories")
    def test_default_max_context_tokens_is_500k(self, mock_link, mock_health, mock_proxy, mock_call):
        import launcher
        import json

        # Ensure env var is not set
        with patch.dict(os.environ, {}, clear=True):
            m_open = unittest.mock.mock_open()
            with patch("pathlib.Path.mkdir"), patch("builtins.open", m_open):
                mock_call.return_value = 0
                with self.assertRaises(SystemExit):
                    launcher.run_session(
                        exe_path="dummy_claude",
                        model_override=None,
                        sandbox_dir="/tmp/test-sandbox",
                        extra_args=[],
                        quiet=True
                    )

                # Verify proc_env passed to subprocess.call
                self.assertTrue(mock_call.called)
                _, kwargs = mock_call.call_args
                proc_env = kwargs.get("env", {})
                self.assertEqual(proc_env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS"), "500000")

                # Verify settings.json env written to disk
                written_chunks = [call.args[0] for call in m_open().write.call_args_list if call.args]
                written_text = "".join(written_chunks)
                # settings.json was dumped via json.dump
                self.assertIn('"CLAUDE_CODE_MAX_CONTEXT_TOKENS": "500000"', written_text)

    @patch("subprocess.call")
    @patch("launcher.start_proxy_thread")
    @patch("launcher.wait_for_proxy_health", return_value=True)
    @patch("launcher.link_sandbox_directories")
    def test_respects_custom_max_context_tokens(self, mock_link, mock_health, mock_proxy, mock_call):
        import launcher

        with patch.dict(os.environ, {"CLAUDE_CODE_MAX_CONTEXT_TOKENS": "750000"}, clear=True):
            m_open = unittest.mock.mock_open()
            with patch("pathlib.Path.mkdir"), patch("builtins.open", m_open):
                mock_call.return_value = 0
                with self.assertRaises(SystemExit):
                    launcher.run_session(
                        exe_path="dummy_claude",
                        model_override=None,
                        sandbox_dir="/tmp/test-sandbox",
                        extra_args=[],
                        quiet=True
                    )

                self.assertTrue(mock_call.called)
                _, kwargs = mock_call.call_args
                proc_env = kwargs.get("env", {})
                self.assertEqual(proc_env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS"), "750000")

                written_chunks = [call.args[0] for call in m_open().write.call_args_list if call.args]
                written_text = "".join(written_chunks)
                self.assertIn('"CLAUDE_CODE_MAX_CONTEXT_TOKENS": "750000"', written_text)


if __name__ == "__main__":
    unittest.main()
