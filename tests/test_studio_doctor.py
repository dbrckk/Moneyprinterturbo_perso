"""Preflight guarantees: no user media read, actual H264/AAC & French voice."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import studio_doctor
import studio_server


class DoctorTests(unittest.TestCase):
    def test_missing_tools_return_safe_diagnostics_without_running_codecs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "exports"
            with patch.object(studio_doctor.shutil, "which", return_value=None), \
                 patch.object(studio_doctor.subprocess, "run") as run:
                report = studio_doctor.diagnose(folder)
            self.assertFalse(report["ok"])
            self.assertGreaterEqual(report["errors"], 3)
            self.assertFalse(any(c["name"] == "h264_aac_encode" for c in report["checks"]))
            self.assertTrue(folder.is_dir())
            self.assertEqual(list(folder.iterdir()), [])
            run.assert_not_called()
            self.assertNotIn(str(folder), json.dumps(report))

    def test_unwritable_destination_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_dir = Path(tmp) / "existing.txt"
            not_dir.write_text("private", encoding="utf-8")
            with patch.object(studio_doctor.shutil, "which", return_value=None):
                report = studio_doctor.diagnose(not_dir)
            self.assertFalse(report["ok"])
            self.assertIn("storage_write", [c["name"] for c in report["checks"]
                          if c["status"] == "error"])
            self.assertEqual(not_dir.read_text(encoding="utf-8"), "private")

    def test_cli_json_error_exit_without_opening_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = io.StringIO()
            with patch.object(studio_doctor.shutil, "which", return_value=None), \
                 patch.object(studio_server, "StudioServer") as server, \
                 redirect_stdout(report):
                result = studio_server.main(["--doctor-json", "--output-dir", tmp])
            self.assertEqual(result, 1)
            self.assertFalse(json.loads(report.getvalue())["ok"])
            server.assert_not_called()
            self.assertNotIn(tmp, report.getvalue())

    def test_low_disk_is_warning_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(studio_doctor.shutil, "which", return_value=None), \
                 patch.object(studio_doctor.shutil, "disk_usage",
                              return_value=shutil._ntuple_diskusage(2_000_000_000, 1_999_000_000, 1_000_000)):
                report = studio_doctor.diagnose(Path(tmp))
            self.assertIn("storage_space", [c["name"] for c in report["checks"]
                          if c["status"] == "warning"])

    def test_full_diagnostic_failure_reports_safe_error_and_preserves_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "private-outputs"
            folder.mkdir()
            existing = folder / "original-private-clip.mp4"
            existing.write_bytes(b"do-not-open-private-media")
            with patch.object(studio_doctor, "_verify_full_render",
                              side_effect=RuntimeError(str(existing))), \
                 redirect_stdout(io.StringIO()) as stdout:
                exit_code = studio_server.main([
                    "--doctor-full-json", "--output-dir", str(folder)])
            report = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 1)
            self.assertFalse(report["ok"])
            self.assertIn("full_render", [item["name"] for item in report["checks"]
                          if item["status"] == "error"])
            self.assertNotIn(tmp, stdout.getvalue())
            self.assertEqual([item.name for item in folder.iterdir()], [existing.name])
            self.assertEqual(existing.read_bytes(), b"do-not-open-private-media")

    def test_fast_doctor_does_not_launch_optional_full_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(studio_doctor, "_verify_full_render") as full:
                report = studio_doctor.diagnose(Path(tmp))
            self.assertNotIn("full_render", [c["name"] for c in report["checks"]])
            full.assert_not_called()

    @unittest.skipUnless(all(shutil.which(t) for t in ("ffmpeg", "ffprobe", "espeak")),
                         "Local tools required")
    def test_real_full_doctor_two_scene_video_audio_subtitles_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "private-outputs"
            folder.mkdir()
            original = folder / "private-recording.mp4"
            original.write_bytes(b"existing private media stays untouched")
            with redirect_stdout(io.StringIO()) as stdout:
                result = studio_server.main([
                    "--doctor-full-json", "--output-dir", str(folder)])
            report = json.loads(stdout.getvalue())
            self.assertEqual(result, 0, report)
            self.assertTrue(report["ok"])
            self.assertIn("full_render", [c["name"] for c in report["checks"]
                          if c["status"] == "ok"])
            self.assertEqual([x.name for x in folder.iterdir()], [original.name])
            self.assertEqual(original.read_bytes(), b"existing private media stays untouched")
            self.assertNotIn(tmp, stdout.getvalue())

    @unittest.skipUnless(all(shutil.which(t) for t in ("ffmpeg", "ffprobe", "espeak")),
                         "FFmpeg/ffprobe/eSpeak required")
    def test_actual_offline_encoding_and_voice_leave_no_media_or_tempfiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "private-outputs"
            sentinel = folder / "original.jpg"
            folder.mkdir()
            sentinel.write_bytes(b"original-personal-image")
            with redirect_stdout(io.StringIO()) as out:
                exit_code = studio_server.main(["--doctor", "--output-dir", str(folder)])
            self.assertEqual(exit_code, 0, out.getvalue())
            self.assertIn("h264_aac_encode", out.getvalue())
            self.assertIn("french_voice", out.getvalue())
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["original.jpg"])
            self.assertEqual(sentinel.read_bytes(), b"original-personal-image")


if __name__ == "__main__":
    unittest.main()
