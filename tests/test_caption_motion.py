import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from caption_motion import _timestamp, _group_words, _apportion, generate_karaoke_ass


class CaptionMotionTests(unittest.TestCase):
    def test_time_and_distribution(self):
        self.assertEqual(_timestamp(366123), "1:01:01.23")
        self.assertEqual(_apportion(101, [1, 2, 3]), [17, 34, 50])
        self.assertEqual(sum(_apportion(913, [1] * 23)), 913)

    def test_groups_enforce_readability(self):
        groups = _group_words(" ".join(["bonjour"] * 30), 360, 640)
        self.assertGreater(len(groups), 3)
        self.assertTrue(all(len(" ".join(group)) <= 34 or len(group) == 1 for group in groups))

    def test_all_phrase_cues_cover_scene(self):
        with tempfile.TemporaryDirectory() as folder:
            scene = [{"start": 0, "end": 3.75,
                      "text": "Bonjour le monde. Ceci est une démonstration en français."},
                     {"start": 3.75, "end": 6.03,
                      "text": "Les paysages naturels sont magnifiques."}]
            info = generate_karaoke_ass(scene, Path(folder), width=360, height=640)
            phrases = info["phrases"]
            self.assertTrue(info["estimated_word_timing"])
            self.assertEqual(phrases[0]["start"], 0)
            self.assertEqual(phrases[-1]["end"], 6.03)
            self.assertTrue(all(phrases[i]["end"] == phrases[i+1]["start"]
                                for i in range(len(phrases)-1)))
            ass = (Path(folder)/"captions.ass").read_text()
            self.assertIn(r"{\kf", ass)
            self.assertIn("DejaVu Sans", ass)
            self.assertEqual(ass.count("Dialogue:"), len(phrases))

    def test_injection_is_escaped(self):
        with tempfile.TemporaryDirectory() as folder:
            generate_karaoke_ass([{"start": 0, "end": 3,
                "text": r"Bonjour {\pos(0,0)} \N maintenant."}], Path(folder), width=360,height=640)
            raw=(Path(folder)/"captions.ass").read_text()
            self.assertNotIn(r"{\pos(0,0)}", raw)
            self.assertNotIn(r"\N", raw)
            self.assertIn("（", raw)

    def test_reject_invalid_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                generate_karaoke_ass([{"start": 0, "end": 0.001, "text": "Très long texte"}], Path(folder), width=360,height=640)
            with self.assertRaises(ValueError):
                generate_karaoke_ass([{"start": 1, "end": 2, "text": "Première."},
                                      {"start": 1.5, "end": 3, "text": "Deuxième."}], Path(folder), width=360,height=640)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg needed")
    def test_real_libass_burn_in(self):
        with tempfile.TemporaryDirectory() as folder:
            work=Path(folder)
            generate_karaoke_ass([{"start": 0, "end": 2.5, "text": "Bonjour le monde"},
                                  {"start": 2.5, "end": 4.8, "text": "Bienvenue sur cette vidéo."}],
                                 work, width=360, height=640)
            target=work/"captions.mp4"
            cp=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                "-f","lavfi","-i","color=c=0x14263a:s=360x640:r=12:d=4.8",
                "-vf","ass=captions.ass","-c:v","libx264","-preset","ultrafast",
                "-pix_fmt","yuv420p",str(target)], cwd=work,capture_output=True,text=True,timeout=50)
            self.assertEqual(cp.returncode,0,cp.stderr[-1500:])
            self.assertGreater(target.stat().st_size,2000)

if __name__=="__main__":
    unittest.main()
