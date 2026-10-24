"""Offline, opt-in long montage performance harness safety/contract tests."""
from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from bench import long_mobile_montage as bench


class LongMontageHarnessTests(unittest.TestCase):
    def test_generated_synthetic_png_decodes_losslessly_with_stdlib(self):
        import struct
        import zlib
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.png"
            second = Path(tmp) / "second.png"
            bench.write_synthetic_png(first, 0, width=8, height=12)
            bench.write_synthetic_png(second, 2, width=8, height=12)
            self.assertNotEqual(first.read_bytes(), second.read_bytes())
            data = first.read_bytes()
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(struct.unpack(">II", data[16:24]), (8, 12))
            offset = 8
            chunks = {}
            while offset < len(data):
                length = struct.unpack(">I", data[offset:offset+4])[0]
                kind = data[offset+4:offset+8]
                payload = data[offset+8:offset+8+length]
                crc = struct.unpack(">I", data[offset+8+length:offset+12+length])[0]
                self.assertEqual(crc, zlib.crc32(kind + payload))
                chunks[kind] = payload
                offset += 12 + length
            self.assertEqual(len(zlib.decompress(chunks[b"IDAT"])), 12 * (1+8*3))
            self.assertIn(b"IEND", chunks)

    def test_invalid_benchmark_settings_refused_before_opening_media(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(bench, "make_fixtures") as fixture:
            for res, fps in ((1080, 12), (720, 50), (360, 0)):
                with self.subTest(res=res, fps=fps), self.assertRaises(ValueError):
                    bench.benchmark(Path(tmp), resolution=res, fps=fps)
            fixture.assert_not_called()

    def test_low_memory_never_runs_forced_parallel_stress(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(bench, "_scene_encoding_workers", return_value=1), \
             patch.object(bench, "make_fixtures") as fixture:
            with self.assertRaisesRegex(ValueError, "2 Gio libres"):
                bench.benchmark(Path(tmp), resolution=720, compare_workers=True)
            fixture.assert_not_called()

    def test_compare_records_real_worker_paths_and_keeps_source_bytes(self):
        def fixture(folder):
            folder.mkdir()
            for i in range(6):
                (folder / f"{i:02}.mp4").write_bytes(bytes([i]) * 19)
            import hashlib
            return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in folder.iterdir()}
        def fake_render(_topic, _script, output, **kwargs):
            output.write_bytes(b"synthetic-mp4-output")
            import free_studio
            workers = free_studio._scene_encoding_workers(720, 1280, 6)
            return {"segments": [{"text": "x"}] * 6,
                    "media": [{"source": "local"}] * 6,
                    "duration_seconds": 15,
                    "render_performance": {
                        "compositing_pipeline": "fused-xfade",
                        "scene_encoding_seconds": 1.2,
                        "final_encoding_seconds": 2.5,
                        "scene_encoding_workers": workers}}
        class QA:
            pass
        probe_result = {"streams": []}
        qa = {"ok": True, "checks": {"video_codec": "h264",
              "pixel_format": "yuv420p", "audio_codec": "aac"}}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(bench, "_available_render_memory_bytes",
                          return_value=3 * 1024**3), \
             patch.object(bench, "_scene_encoding_workers", return_value=2), \
             patch.object(bench, "make_fixtures", side_effect=fixture), \
             patch.object(bench, "render", side_effect=fake_render) as render, \
             patch.object(bench, "probe", return_value=probe_result), \
             patch.object(bench, "validate", return_value=qa):
            folder = Path(tmp)
            report = bench.benchmark(folder, resolution=720, compare_workers=True)
            self.assertEqual(render.call_count, 2)
            self.assertEqual([x["workers"] for x in report["rows"]], [1, 2])
            self.assertEqual([x["actual_scene_encoding_workers"] for x in report["rows"]],
                             [1, 2])
            self.assertEqual(report["selected_worker_count"], 2)
            self.assertEqual(report["memory_available_at_start_bytes"], 3 * 1024**3)
            self.assertEqual([x["QA"] for x in report["rows"]], ["passed", "passed"])
            self.assertEqual(json.loads((folder / "long-montage-report.json").read_text()), report)
            self.assertTrue(all(p.read_bytes() == bytes([int(p.stem)]) * 19
                                for p in (folder / "fixtures").iterdir()))

    def test_outdir_with_existing_user_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            original = folder / "private.mp4"
            original.write_bytes(b"do not delete")
            with self.assertRaises(SystemExit) as caught, \
                 patch.object(bench, "make_fixtures") as fixtures:
                bench.main(["--out-dir", str(folder)])
            self.assertEqual(caught.exception.code, 2)
            fixtures.assert_not_called()
            self.assertEqual(original.read_bytes(), b"do not delete")


if __name__ == "__main__":
    unittest.main()
