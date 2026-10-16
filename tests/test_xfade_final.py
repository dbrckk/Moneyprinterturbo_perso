"""One-encode final crossfade regression tests using real FFmpeg."""
from dataclasses import dataclass
from pathlib import Path
import json, shutil, subprocess, tempfile, unittest
from xfade_final import final_xfade_command

@dataclass(frozen=True)
class FramePlan:
    total_frames: int
    overlap_frames: int
    offset_frames: tuple[int,...]

class XfadeFinalTests(unittest.TestCase):
    def test_graph_has_one_final_encode_and_safe_audio_map(self):
        command = final_xfade_command([Path("one.mp4"),Path("two.mp4")],
            FramePlan(48,4,(22,)), fps=24, transition="fade",
            overlays=["format=yuv420p","drawbox=x=0:y=0:w=20:h=10:c=white:t=fill"],
            audio=Path("voice.m4a"), duration=2,output=Path("render.mp4"))
        graph=command[command.index("-filter_complex")+1]
        self.assertIn("xfade=transition=fade:duration=0.166667:offset=0.916667",graph)
        self.assertIn("[xf1]format=yuv420p,drawbox",graph)
        self.assertIn("2:a:0",command)

    def test_invalid_transitions_are_rejected(self):
        base=dict(scenes=[Path("a"),Path("b")],plan=FramePlan(48,4,(22,)),fps=24,
                  overlays=["format=yuv420p"],duration=2,output=Path("out"))
        for bad in ("cut","", "eval", "fade:enable=evil"):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                final_xfade_command(**base,transition=bad)
        with self.assertRaises(ValueError):
            final_xfade_command(**{**base,"plan":FramePlan(48,3,(22,))},transition="fade")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg needed")
    def test_real_clip_two_scenes_audio_and_graphics(self):
        with tempfile.TemporaryDirectory() as folder:
            work=Path(folder)
            clips=[]
            for index,color in enumerate(("blue","green")):
                path=work/f"scene_{index}.mp4"
                p=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                    "-f","lavfi","-i",f"color=c={color}:s=360x640:r=12",
                    "-frames:v","24","-c:v","libx264","-preset","ultrafast",
                    "-pix_fmt","yuv420p",str(path)],capture_output=True,text=True,timeout=60)
                self.assertEqual(p.returncode,0,p.stderr)
                clips.append(path)
            track=work/"voice.m4a"
            p=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                "-f","lavfi","-i","sine=frequency=240:duration=3.833",
                "-c:a","aac",str(track)],capture_output=True,text=True,timeout=30)
            self.assertEqual(p.returncode,0,p.stderr)
            target=work/"out.mp4"
            command=final_xfade_command(clips,FramePlan(46,2,(23,)),
                fps=12,transition="fade",overlays=["format=yuv420p",
                    "drawbox=x=0:y=0:w=30:h=10:c=white:t=fill"],
                audio=track,duration=46/12,output=target)
            p=subprocess.run(command,capture_output=True,text=True,timeout=120)
            self.assertEqual(p.returncode,0,p.stderr[-1500:])
            probe=subprocess.run(["ffprobe","-v","error","-show_format",
                "-show_streams","-of","json",str(target)],
                capture_output=True,text=True,timeout=30,check=True)
            d=json.loads(probe.stdout)
            video=next(s for s in d["streams"] if s["codec_type"]=="video")
            sound=next(s for s in d["streams"] if s["codec_type"]=="audio")
            self.assertEqual(video["codec_name"],"h264")
            self.assertEqual(sound["codec_name"],"aac")
            self.assertEqual(video["pix_fmt"],"yuv420p")
            self.assertEqual(int(video["nb_frames"]),46)
            self.assertAlmostEqual(float(d["format"]["duration"]),46/12,delta=0.15)

if __name__=="__main__":
    unittest.main()
