"""Regression tests for bounded one-pass JPEG/MP4/abstract composition."""
import json
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess
import tempfile
import unittest

import fast_media
import free_studio
import video_qa


def ffmpeg(*args):
    result=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",*args],
                          capture_output=True,text=True,timeout=100)
    if result.returncode:
        raise AssertionError(result.stderr[-1500:])


class MediaOnePassTests(unittest.TestCase):
    def test_safe_argv_and_rejection(self):
        a=SimpleNamespace(kind="image",file=Path("name;with special.jpg"))
        b=SimpleNamespace(kind="video",file=Path("my footage.mp4"))
        opts=dict(durations=[2.1,2.8],width=360,height=640,fps=12,
                  transition="fade",visual_filters=["format=yuv420p"],
                  audio=None,output=Path("out.mp4"),total=4.9)
        command=fast_media.build_onepass_media_command([a,b],**opts)
        self.assertEqual(command.count("-c:v"),1)
        self.assertIn("name;with special.jpg",command)
        self.assertIn("my footage.mp4",command)
        graph=command[command.index("-filter_complex")+1]
        self.assertIn("xfade=transition=fade",graph)
        self.assertIn("setpts=N/(12*TB),fps=12",graph)
        for name,value in (("width",361),("fps",3),("total",8)):
            with self.subTest(name=name),self.assertRaises(ValueError):
                fast_media.build_onepass_media_command([a,b],**(opts|{name:value}))
        with self.assertRaises(ValueError):
            fast_media.build_onepass_media_command([None,None],**opts)
        with self.assertRaises(ValueError):
            fast_media.build_onepass_media_command([SimpleNamespace(kind="other",file=Path("bad")),b],**opts)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"FFmpeg required")
    def test_real_jpeg_mp4_with_fade_cut_wipe_and_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            media=root/"source"
            media.mkdir()
            ffmpeg("-f","lavfi","-i","testsrc2=s=320x240:r=1",
                   "-frames:v","1",str(media/"01-photo.jpg"))
            ffmpeg("-f","lavfi","-i","testsrc2=s=240x426:r=18",
                   "-t","2","-c:v","libx264","-preset","ultrafast",
                   "-pix_fmt","yuv420p",str(media/"02-clip.mp4"))
            options=dict(resolution=360,fps=12,mute=True,media_dir=media,caption_style="classic")
            for transition in ("fade","cut","wipeleft"):
                for optimized in (True,False):
                    with self.subTest(transition=transition,optimized=optimized):
                        target=root/(f"{transition}-{optimized}.mp4")
                        info=free_studio.render("Médias","La nature est magnifique. Observons la montagne.",
                            target,transition=transition,use_media_onepass=optimized,**options)
                        expected=("direct-media-onepass" if optimized else
                                  "fused-xfade" if transition!="cut" else "cut-concat")
                        self.assertEqual(info["render_performance"]["compositing_pipeline"],expected)
                        report=video_qa.validate(target,info,target.with_suffix(".srt"),
                            video_qa.probe(target))
                        self.assertTrue(report["ok"],report["errors"])
                        self.assertEqual(report["checks"]["pixel_format"],"yuv420p")
                        self.assertEqual(len(info["media"]),2)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe") and shutil.which("espeak"),"Voice tools required")
    def test_photo_narration_with_direct_mp4(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            media=root/"source"
            media.mkdir()
            ffmpeg("-f","lavfi","-i","testsrc2=s=320x240:r=1",
                   "-frames:v","1",str(media/"01-photo.jpg"))
            target=root/"voice.mp4"
            info=free_studio.render("Paysage","Bonjour tout le monde.",target,
                media_dir=media,resolution=360,fps=12,
                transition="fade",caption_style="kinetic")
            self.assertEqual(info["render_performance"]["compositing_pipeline"],"direct-media-onepass")
            report=video_qa.validate(target,info,target.with_suffix(".srt"),
                                      video_qa.probe(target))
            self.assertTrue(report["ok"],report["errors"])
            self.assertEqual(report["checks"]["audio_codec"],"aac")
            self.assertTrue(target.with_suffix(".ass").exists())


if __name__=="__main__":
    unittest.main()
