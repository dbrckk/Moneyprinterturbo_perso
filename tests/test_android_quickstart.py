"""Offline tests for safe Android Termux onboarding and public website wording."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "scripts" / "termux-start.sh"


class AndroidQuickstartTests(unittest.TestCase):
    def test_bash_syntax_and_public_usage_without_phone(self):
        check = subprocess.run(["bash", "-n", str(LAUNCHER)],
                               capture_output=True, text=True, timeout=10)
        self.assertEqual(check.returncode, 0, check.stderr)
        help_result = subprocess.run(["bash", str(LAUNCHER), "--help"],
                                     capture_output=True, text=True, timeout=10)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("127.0.0.1:8765", help_result.stdout)
        self.assertIn("--doctor-full", help_result.stdout)
        self.assertNotIn("https://raw.githubusercontent", LAUNCHER.read_text())

    def test_fail_closed_outside_official_termux_or_on_unknown_flags(self):
        env = {**os.environ, "PREFIX": "/usr"}
        for flags in ([], ["--doctor"], ["--doctor-full"], ["--unknown"]):
            response = subprocess.run(["bash", str(LAUNCHER), *flags],
                                      env=env, capture_output=True,
                                      text=True, timeout=10)
            self.assertNotEqual(response.returncode, 0)
        self.assertIn("Termux", subprocess.run(
            ["bash", str(LAUNCHER), "--doctor"], env=env,
            capture_output=True, text=True, timeout=10).stderr)

    def test_mocked_termux_only_launches_loopback_after_doctor(self):
        # Simulate the Termux command environment WITHOUT running pkg, FFmpeg,
        # GitHub, or the studio itself on GitHub Actions. Python executions are
        # intercepted by an in-memory temp directory shim.
        with tempfile.TemporaryDirectory(prefix="termux-launch-test-") as tmp:
            folder = Path(tmp)
            log = folder / "calls.txt"
            stub = (
                "#!/bin/sh\n"
                "printf '%s\\n' \"$0 $*\" >> \"$MPT_MOCK_LOG\"\n"
                "exit 0\n"
            )
            for name in ("python3", "ffmpeg", "ffprobe", "espeak", "pkg"):
                file = folder / name
                file.write_text(stub, encoding="utf-8")
                file.chmod(0o700)
            env = {**os.environ, "PREFIX": "/data/data/com.termux/files/usr",
                   "PATH": str(folder) + os.pathsep + os.environ.get("PATH", ""),
                   "MPT_MOCK_LOG": str(log)}
            command = ["bash", str(LAUNCHER)]
            res = subprocess.run([*command, "--doctor"], env=env,
                                 text=True, capture_output=True, timeout=10)
            self.assertEqual(res.returncode, 0, res.stderr)
            calls = log.read_text(encoding="utf-8")
            self.assertIn("studio_server.py --doctor", calls)
            self.assertNotIn("studio_server.py --host", calls)
            self.assertNotIn("/pkg ", calls)
            log.write_text("", encoding="utf-8")
            start = subprocess.run(command, env=env, text=True,
                                   capture_output=True, timeout=10)
            self.assertEqual(start.returncode, 0, start.stderr)
            lines = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn("studio_server.py --doctor", lines[0])
            self.assertIn("studio_server.py --host 127.0.0.1 --port 8765 --token ", lines[1])
            self.assertNotIn("0.0.0.0", lines[1])
            self.assertNotIn("/pkg ", "\n".join(lines))
            log.write_text("", encoding="utf-8")
            full = subprocess.run([*command, "--doctor-full"], env=env,
                                  text=True, capture_output=True, timeout=10)
            self.assertEqual(full.returncode, 0)
            self.assertIn("studio_server.py --doctor-full", log.read_text())
            self.assertNotIn("studio_server.py --host", log.read_text())

    def test_mobile_remote_shows_private_gallery_help_and_copy_fallback(self):
        html = (ROOT / "remote" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "remote" / "main.mjs").read_text(encoding="utf-8")
        css = (ROOT / "remote" / "style.css").read_text(encoding="utf-8")
        self.assertIn('id="termux-command"', html)
        self.assertIn('id="copy-termux-command"', html)
        self.assertIn('id="termux-copy-status"', html)
        self.assertIn("f-droid.org/packages/com.termux", html)
        self.assertIn("scripts/termux-start.sh", html)
        self.assertIn("Ce studio distant utilise des images libres", html)
        self.assertIn("navigator.clipboard", js)
        self.assertIn("$('termux-command').textContent.trim()", js)
        self.assertIn("appui long", js)
        self.assertIn(".local-import-panel .termux-code", css)

    def test_remote_render_workflow_install_is_bounded_and_consistent(self):
        free = (ROOT / ".github" / "workflows" / "free-video.yml").read_text(
            encoding="utf-8")
        self.assertIn("runs-on: ubuntu-24.04", free)
        self.assertIn("timeout -k 10s 180s apt-get", free)
        self.assertIn("timeout -k 10s 480s apt-get", free)
        self.assertIn("install -y --no-install-recommends", free)
        self.assertIn("missing[@]", free)


if __name__ == "__main__":
    unittest.main()
