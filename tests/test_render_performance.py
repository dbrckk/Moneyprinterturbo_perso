"""Renderer and performance regressions; requires installed FFmpeg."""
from pathlib import Path
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

import free_studio


class PerformanceTests(unittest.TestCase):
    def test_single_scene_skips_remux(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            def fake_scene(_asset, _duration, index, folder_path, **kwargs):
                target = folder_path / f"scene_{index:02}.mp4"
                target.write_bytes(b"pretend encoded media")
                return target
            with patch.object(free_studio, "render_scene", side_effect=fake_scene), \
                 patch.object(free_studio, "execute", side_effect=AssertionError("unexpected FFmpeg concat")):
                out = free_studio.prepare_media_background([None], [3.0], work,
                    width=360, height=640, fps=12, transition="fade")
            self.assertEqual(out.read_bytes(), b"pretend encoded media")

    def test_720p_parallel_clip_preparation_preserves_order(self):
        import threading
        from unittest.mock import patch

        barrier = threading.Barrier(2, timeout=5)
        lock = threading.Lock()
        threads = {}
        def fake_scene(_asset, _duration, index, folder, **kwargs):
            with lock:
                threads[index] = threading.get_ident()
            if index < 2:
                barrier.wait()
            return folder / f"scene_{index:02}.mp4"

        with tempfile.TemporaryDirectory() as folder:
            with patch.object(free_studio.os, "cpu_count", return_value=4), \
                 patch.object(free_studio, "_available_render_memory_bytes",
                              return_value=4 * 1024 ** 3), \
                 patch.object(free_studio, "render_scene", side_effect=fake_scene):
                scenes = free_studio.encode_media_scenes(
                    [None] * 5, [1.2] * 5, Path(folder), width=720,
                    height=1280, fps=12, extra_frames=(0, 1, 0, 1, 0))
            self.assertEqual([path.name for path in scenes],
                             [f"scene_{i:02}.mp4" for i in range(5)])
            self.assertNotEqual(threads[0], threads[1],
                                "720p preparation should run two workers")

    def test_mobile_1080p_and_small_batches_remain_sequential(self):
        import threading
        observed = []

        def fake_scene(_asset, _duration, index, folder, **kwargs):
            observed.append(threading.get_ident())
            return folder / f"scene_{index:02}.mp4"

        with tempfile.TemporaryDirectory() as folder:
            with patch.object(free_studio.os, "cpu_count", return_value=8), \
                 patch.object(free_studio, "_available_render_memory_bytes",
                              return_value=4 * 1024 ** 3), \
                 patch.object(free_studio, "render_scene", side_effect=fake_scene):
                self.assertEqual(free_studio._scene_encoding_workers(1080, 1920, 6), 1)
                self.assertEqual(free_studio._scene_encoding_workers(360, 640, 8), 1)
                self.assertEqual(free_studio._scene_encoding_workers(720, 1280, 4), 1)
                self.assertEqual(free_studio._scene_encoding_workers(720, 1280, 6), 2)
                free_studio.encode_media_scenes(
                    [None] * 5, [1.2] * 5, Path(folder),
                    width=1080, height=1920, fps=12, extra_frames=(0,) * 5)
            self.assertEqual(set(observed), {threading.get_ident()})
        with self.assertRaises(ValueError):
            free_studio.encode_media_scenes([None] * 3, [1.0] * 2,
                Path("unused"), width=720, height=1280, fps=12,
                extra_frames=(0, 0))

    def test_parallel_scene_workers_respect_live_memory_headroom(self):
        minimum = free_studio.PARALLEL_SCENE_MIN_FREE_BYTES
        with patch.object(free_studio.os, "cpu_count", return_value=8):
            for memory, expected in ((None, 1), (0, 1),
                                     (minimum - 1, 1), (minimum, 2),
                                     (minimum + 1, 2)):
                with self.subTest(memory=memory), \
                     patch.object(free_studio, "_available_render_memory_bytes",
                                  return_value=memory):
                    self.assertEqual(free_studio._scene_encoding_workers(
                        720, 1280, 6), expected)
                    self.assertEqual(free_studio._scene_encoding_workers(
                        1080, 1920, 8), 1)
                    self.assertEqual(free_studio._scene_encoding_workers(
                        720, 1280, 4), 1)
        with patch.object(free_studio.os, "cpu_count", return_value=1), \
             patch.object(free_studio, "_available_render_memory_bytes",
                          side_effect=AssertionError("Do not measure memory with 1 CPU")):
            self.assertEqual(free_studio._scene_encoding_workers(720, 1280, 6), 1)

    def test_memory_headroom_includes_real_cgroup_limits(self):
        gib = 1024 ** 3
        def fake_reader(contents):
            def read_text(path, **kwargs):
                key = str(path)
                if key not in contents:
                    raise FileNotFoundError(key)
                return contents[key]
            return read_text

        global_memory = {"/proc/meminfo": f"MemTotal: 8000000 kB\nMemAvailable: {4*1024*1024} kB\n"}
        cases = [
            ("global", {}, 4 * gib),
            ("cgroup v2 constrained", {
                "/sys/fs/cgroup/memory.max": str(2 * gib),
                "/sys/fs/cgroup/memory.current": str(gib // 2),
            }, gib + gib // 2),
            ("cgroup v1 constrained", {
                "/sys/fs/cgroup/memory.max": "max",
                "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(3 * gib),
                "/sys/fs/cgroup/memory/memory.usage_in_bytes": str(gib * 2),
            }, gib),
            ("unlimited", {
                "/sys/fs/cgroup/memory.max": "max",
                "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(1 << 62),
            }, 4 * gib),
            ("memory fully consumed", {
                "/sys/fs/cgroup/memory.max": str(gib),
                "/sys/fs/cgroup/memory.current": str(gib + 500),
            }, 0),
            ("unknown usage fails closed", {
                "/sys/fs/cgroup/memory.max": str(2 * gib),
            }, 0),
        ]
        for name, records, expected in cases:
            with self.subTest(name=name), \
                 patch.object(Path, "read_text", fake_reader({**global_memory, **records})):
                self.assertEqual(free_studio._available_render_memory_bytes(), expected)
        for contents in ({}, {"/proc/meminfo": "MemAvailable: not-a-number kB\n"},
                         {"/proc/meminfo": "MemTotal: 100 kB\n"}):
            with patch.object(Path, "read_text", fake_reader(contents)):
                self.assertIsNone(free_studio._available_render_memory_bytes())

    def test_low_memory_forces_serial_scene_order_without_changing_encodes(self):
        import threading
        workers = []
        def fake_scene(_asset, _duration, index, folder, **kwargs):
            workers.append(threading.get_ident())
            return folder / f"scene_{index:02}.mp4"
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(free_studio.os, "cpu_count", return_value=8), \
                 patch.object(free_studio, "_available_render_memory_bytes",
                              return_value=512 * 1024 ** 2), \
                 patch.object(free_studio, "render_scene", side_effect=fake_scene):
                scenes = free_studio.encode_media_scenes(
                    [None] * 6, [3.2] * 6, Path(folder),
                    width=720, height=1280, fps=12, extra_frames=(0,) * 6)
        self.assertEqual([path.name for path in scenes],
                         [f"scene_{i:02}.mp4" for i in range(6)])
        self.assertEqual(set(workers), {threading.get_ident()})

    def test_parallel_ffmpeg_error_propagates_without_silent_partial_success(self):
        def fake_scene(_asset, _duration, index, folder, **kwargs):
            if index == 2:
                raise RuntimeError("Encoder failed")
            return folder / f"scene_{index:02}.mp4"
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(free_studio.os, "cpu_count", return_value=4), \
                 patch.object(free_studio, "_available_render_memory_bytes",
                              return_value=4 * 1024 ** 3), \
                 patch.object(free_studio, "render_scene", side_effect=fake_scene):
                with self.assertRaisesRegex(RuntimeError, "Encoder failed"):
                    free_studio.encode_media_scenes(
                        [None] * 6, [1.0] * 6, Path(folder),
                        width=720, height=1280, fps=12, extra_frames=(0,) * 6)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_real_five_scene_720p_fade_and_cut_remain_valid(self):
        import subprocess
        import video_qa

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sources = root / "source"
            sources.mkdir()
            colors = ("red", "green", "blue", "yellow", "cyan")
            for i, color in enumerate(colors):
                subprocess.run(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                     "-f", "lavfi", "-i",
                     f"color=c={color}:s=360x640:r=1",
                     "-frames:v", "1", "-update", "1",
                     str(sources / f"{i:02}.jpg")],
                    check=True, capture_output=True, timeout=30)
            originals = {p.name: p.read_bytes() for p in sources.iterdir()}
            script = ("Première scène. Deuxième scène. Troisième scène. "
                      "Quatrième scène. Cinquième scène.")
            for transition, expected in (("fade", "fused-xfade"),
                                         ("cut", "cut-concat")):
                with self.subTest(transition=transition):
                    output = root / f"{transition}.mp4"
                    info = free_studio.render(
                        "Montage", script, output, media_dir=sources,
                        mute=True, resolution=720, fps=12,
                        caption_style="classic", transition=transition)
                    self.assertEqual(info["render_performance"]["compositing_pipeline"],
                                     expected)
                    self.assertEqual(len(info["segments"]), 5)
                    result = video_qa.validate(
                        output, info, output.with_suffix(".srt"),
                        video_qa.probe(output))
                    self.assertTrue(result["ok"], result["errors"])
                    self.assertEqual(result["checks"]["pixel_format"], "yuv420p")
            self.assertEqual({p.name: p.read_bytes() for p in sources.iterdir()},
                             originals)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_real_render_reports_stage_times_and_maintains_quality(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "timed.mp4"
            info = free_studio.render("Performance", "Bonjour tout le monde.", target, lang="fr", fps=12,
                mute=True, resolution=360, caption_style="classic")
            perf = info["render_performance"]
            self.assertGreater(perf["total_seconds"], 0)
            for key in ("audio_seconds", "media_selection_seconds", "scene_encoding_seconds", "final_encoding_seconds"):
                self.assertGreaterEqual(perf[key], 0)
            self.assertGreater(target.stat().st_size, 3000)
            self.assertAlmostEqual(free_studio.duration_seconds(target), info["duration_seconds"], delta=0.2)
            self.assertEqual(json.loads(target.with_suffix(".json").read_text())["render_performance"], perf)


if __name__ == "__main__":
    unittest.main()
