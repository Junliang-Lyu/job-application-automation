"""Launcher restarts a hidden workbench server that runs older code."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import launch_workbench as L  # noqa: E402
from manual_workbench import code_version  # noqa: E402


class LauncherTests(unittest.TestCase):
    def run_main(self, running):
        with mock.patch.object(L, 'running_info', return_value=running), \
             mock.patch.object(L, 'stop_old_server') as stop, \
             mock.patch.object(L.subprocess, 'Popen') as popen, \
             mock.patch.object(L, 'healthy', return_value=True), \
             mock.patch.object(L, 'open_jobsearch_workbench') as opened:
            L.main()
        return stop, popen, opened

    def test_same_code_reuses_server(self):
        stop, popen, opened = self.run_main({'jobs': [], 'ready_count': 0, 'code_version': code_version(L.ROOT)})
        stop.assert_not_called(); popen.assert_not_called(); opened.assert_called_once()

    def test_old_code_is_restarted(self):
        stop, popen, opened = self.run_main({'jobs': [], 'ready_count': 0})   # server from before code_version existed
        stop.assert_called_once(); popen.assert_called_once(); opened.assert_called_once()

    def test_no_server_starts_one(self):
        stop, popen, _ = self.run_main(None)
        stop.assert_not_called(); popen.assert_called_once()


if __name__ == '__main__':
    unittest.main()
