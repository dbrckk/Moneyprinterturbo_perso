"""Test real FFmpeg pixels, framing and sync with offline extreme landscape art."""
from pathlib import Path
from types import SimpleNamespace
import json
import shutil
import subprocess
import tempfile
import unittest

import fast_media
import free_studio
import scene_framing


def run(cmd, timeout=100):
    p=subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise AssertionError(p.stderr[-1800:])
    return p


def rgb_pixel(video: Path, x: int, y: int, width: int = 360):
    result=subprocess.run([
        "ffmpeg","-hide_banner","-loglevel","error","-i",str(video),
        "-frames:v","1","-f","rawvideo","-pix_fmt","rgb24","pipe:1"],
        capture_output=True, timeout=60, check=True)
    offset=(y*width+x)*3
    return tuple(result.stdout[offset:offset+3])


class FramingLogicTests(unittest.TestCase):
    def test_threshold_framing_only_for_extreme_credible_image_crop(self):
        a=SimpleNamespace(kind="image", crop_retention_estimate=0.12)
        self.assertTrue(scene_framing.needs_soft_matte(a))
        a.crop_retention_estimate=0.50
        self.assertFalse(scene_framing.needs_soft_matte(a))
        a.crop_retention_estimate=None
        self.assertFalse(scene_framing.needs_soft_matte(a))
        a.crop_retention_estimate=float("nan")
        self.assertFalse(scene_framing.needs_soft_matte(a))
        a.crop_retention_estimate=.12
        a.kind="video"
        self.assertFalse(scene_framing.needs_soft_matte(a))

    def test_photo_movement_spans_entire_scene_and_rejects_invalid_limits(self):
        movement = scene_framing.photo_zoom_filter(
            width=360, height=640, fps=12, frames=180)
        self.assertIn("on/180", movement)
        self.assertIn("min(1.08000,1+0.08000", movement)
        self.assertNotIn("zoom+0.0008", movement)
        self.assertIn("z='1'", scene_framing.photo_zoom_filter(
            width=360, height=640, fps=12, frames=1))
        for overrides in ({"frames": 0}, {"frames": True},
                          {"frames": 216001}, {"width": 361},
                          {"fps": 7}, {"zoom_percent": -0.1},
                          {"zoom_percent": float("nan")},
                          {"zoom_percent": 0.12}):
            options=dict(width=360,height=640,fps=12,frames=180)
            options.update(overrides)
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                scene_framing.photo_zoom_filter(**options)

    def test_bounded_filtergraph_input_and_output(self):
        graph=scene_framing.soft_matte_graph(0,width=360,height=640,fps=12,frames=30,
                                             prefix="matte0",output_name="final")
        self.assertEqual(len(graph),3)
        self.assertIn("split=2",graph[0])
        self.assertIn("gblur=sigma=5",graph[1])
        self.assertTrue(graph[2].endswith("[final]"))
        for bad in ("a;b","a[0]","a b"):
            with self.assertRaises(ValueError):
                scene_framing.soft_matte_graph(0,width=360,height=640,fps=12,frames=30,
                                               prefix=bad,output_name="safe")
        with self.assertRaises(ValueError):
            scene_framing.soft_matte_graph(0,width=361,height=640,fps=12,frames=30,
                                           prefix="matte",output_name="safe")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg needed")
class RealMatteVideoTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.work=Path(self.dir.name)
        self.source=self.work/"extreme-wide.jpg"
        run(["ffmpeg","-hide_banner","-loglevel","error","-y",
             "-f","lavfi","-i","color=c=red:s=1600x300:r=1",
             "-vf","drawbox=x=0:y=0:w=520:h=300:color=blue:t=fill,"
                    "drawbox=x=1080:y=0:w=520:h=300:color=green:t=fill",
             "-frames:v","1",str(self.source)])
        self.asset=SimpleNamespace(kind="image",file=self.source,
                                   crop_retention_estimate=.105,visual_medium="photograph-likely")

    def verify_full_width_colored_subject_visible(self, video):
        blue=rgb_pixel(video,65,320)
        green=rgb_pixel(video,300,320)
        middle=rgb_pixel(video,180,320)
        self.assertGreater(blue[2],150,blue)
        self.assertGreater(green[1],70,green)
        self.assertGreater(middle[0],150,middle)

    def test_one_pass_graph_keeps_extreme_landscape_full_width(self):
        out=self.work/"onepass.mp4"
        cmd=fast_media.build_onepass_media_command(
            [self.asset],[2.5],width=360,height=640,fps=12,
            transition="cut",visual_filters=["format=yuv420p"],
            audio=None,output=out,total=2.5)
        self.assertIn("gblur=sigma=5",cmd[cmd.index("-filter_complex")+1])
        run(cmd)
        self.verify_full_width_colored_subject_visible(out)
        info=json.loads(run(["ffprobe","-v","error","-show_streams","-of","json",str(out)]).stdout)
        self.assertEqual(int(info["streams"][0]["nb_frames"]),30)

    @staticmethod
    def decoded_mini_frame(video: Path, at: float) -> bytes:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-ss", str(at), "-i", str(video), "-frames:v", "1",
             "-vf", "scale=36:64,format=gray", "-f", "rawvideo", "pipe:1"],
            capture_output=True, timeout=40, check=True)
        if len(result.stdout) != 36 * 64:
            raise AssertionError("Incomplete decoded frame")
        return result.stdout

    def test_real_fifteen_second_photos_keep_moving_through_last_seconds(self):
        """Validate source, onepass and legacy decoded pixels, not only a graph."""
        pattern = self.work / "detail.jpg"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=s=720x1280:r=1",
             "-frames:v", "1", "-update", "1", str(pattern)])
        wide = self.work / "pattern-wide.jpg"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=s=1600x320:r=1",
             "-frames:v", "1", "-update", "1", str(wide)])
        for picture, ratio, min_movement in (
            (pattern, 0.65, 2.0), (wide, 0.12, 0.8)):
            with self.subTest(source=picture.name):
                source_bytes = picture.read_bytes()
                asset = SimpleNamespace(kind="image", file=picture,
                    crop_retention_estimate=ratio,
                    visual_medium="photograph-likely")
                clips = [self.work / f"{picture.stem}-direct.mp4",
                         self.work / f"{picture.stem}-legacy.mp4"]
                command = fast_media.build_onepass_media_command(
                    [asset], [15.0], width=360, height=640, fps=12,
                    transition="cut", visual_filters=["format=yuv420p"],
                    audio=None, output=clips[0], total=15.0)
                graph = command[command.index("-filter_complex")+1]
                self.assertIn("on/180", graph)
                run(command)
                rendered = free_studio.render_scene(
                    asset, 15., 0, self.work, width=360, height=640, fps=12)
                for video in (clips[0], rendered):
                    profile = json.loads(run(["ffprobe", "-v", "error",
                        "-show_streams", "-of", "json", str(video)]).stdout)
                    self.assertEqual(int(profile["streams"][0]["nb_frames"]), 180)
                    late_a = self.decoded_mini_frame(video, 11.0)
                    late_b = self.decoded_mini_frame(video, 14.0)
                    late_delta = sum(abs(a-b) for a,b in zip(late_a, late_b)) / len(late_a)
                    self.assertGreater(late_delta, min_movement,
                        "Camera movement stopped before the narration ended")
                self.assertEqual(picture.read_bytes(), source_bytes)
                clips[1] = rendered
                # Both compositors should emit essentially the same camera move.
                a = self.decoded_mini_frame(clips[0], 13.0)
                b = self.decoded_mini_frame(clips[1], 13.0)
                discrepancy = sum(abs(x-y) for x,y in zip(a,b))/len(a)
                self.assertLess(discrepancy, 2.0)

    def test_legacy_intermediate_scene_keeps_full_width(self):
        output=free_studio.render_scene(self.asset,2.5,0,self.work,
                                         width=360,height=640,fps=12)
        self.verify_full_width_colored_subject_visible(output)
        self.assertGreater(output.stat().st_size,2000)


if __name__=="__main__":
    unittest.main()
