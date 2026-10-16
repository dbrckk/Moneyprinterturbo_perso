"""Regression tests for scene transitions and subtitle-frame alignment."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import cinematic
import free_studio


class TimelineTests(unittest.TestCase):
    def test_frame_plan_preserves_storyboard_runtime(self):
        durations = [2.71, 3.62, 4.09, 2.51]
        for fps in (12, 24, 30):
            for transition in ('fade', 'wipeleft', 'cut'):
                with self.subTest(fps=fps, transition=transition):
                    plan = cinematic.transition_plan(durations, fps=fps, transition=transition)
                    self.assertAlmostEqual(plan.total_frames / fps, sum(durations), delta=1/fps)
                    self.assertEqual(len(plan.extra_frames), len(durations))
                    self.assertEqual(len(plan.offset_frames), len(durations)-1 if plan.overlap_frames else 0)
                    self.assertEqual(sum(round(d*fps) + bonus for d,bonus in zip(plan.durations,plan.extra_frames))
                                     - plan.overlap_frames * (len(durations)-1), plan.total_frames)
                    if plan.overlap_frames:
                        self.assertTrue(all(plan.offset_frames[i] < plan.offset_frames[i+1]
                                            for i in range(len(plan.offset_frames)-1)))

    def test_short_scenes_fall_back_to_cut_without_losing_frames(self):
        plan = cinematic.transition_plan([0.20,0.20],fps=12,transition='fade')
        self.assertEqual(plan.overlap_frames,0)
        for value in ('blend=all_mode', '', 'none'):
            with self.assertRaises(ValueError): cinematic.transition_plan([2,2],fps=12,transition=value)
        with self.assertRaises(ValueError): cinematic.transition_plan([2,float('nan')],fps=12,transition='fade')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_real_fades_and_wipe_preserve_duration_and_subtitles(self):
        with tempfile.TemporaryDirectory() as name:
            for transition in ('fade','wipeleft','cut'):
                with self.subTest(transition=transition):
                    target = Path(name) / (transition+'.mp4')
                    info = free_studio.render('Paysage', 'La forêt paisible. La montagne. Le soleil.', target,
                        resolution=360,fps=12,mute=True,transition=transition)
                    self.assertEqual(info['scene_transition'],transition)
                    self.assertEqual(info['render_performance']['compositing_pipeline'],
                                     'cut-concat' if transition == 'cut' else 'fused-xfade')
                    self.assertEqual(len(info['segments']),3)
                    self.assertAlmostEqual(free_studio.duration_seconds(target),
                        info['duration_seconds'],delta=0.15)
                    self.assertEqual(target.with_suffix('.srt').read_text().count(' --> '),3)
                    self.assertEqual(json.loads(target.with_suffix('.json').read_text())['scene_transition'],transition)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_real_media_to_graphic_crossfade_with_voice(self):
        from media_assets import MediaAsset
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as name:
            work=Path(name)
            img=work/'photo.png'
            free_studio.execute(['ffmpeg','-hide_banner','-loglevel','error','-y',
                '-f','lavfi','-i','testsrc2=s=360x640:r=1','-frames:v','1',str(img)])
            asset=MediaAsset(img,'image','local','user-provided','user','photo.png')
            target=work/'mixed.mp4'
            with patch.object(free_studio,'commons_scene_assets',return_value=({0:asset},[])):
                info=free_studio.render('Paysage','La forêt est belle. La mer est calme.',target,
                    lang='fr',resolution=360,fps=12,commons=True,transition='fade')
            self.assertEqual(info['visual_mode'],'mixed-media')
            self.assertGreater(target.stat().st_size,3000)
            self.assertEqual(len(info['media']),1)
            self.assertAlmostEqual(free_studio.duration_seconds(target),info['duration_seconds'],delta=0.15)

if __name__=='__main__':unittest.main()

class CompositionBenchmarkPathTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg needed")
    def test_internal_reference_pipeline_compares_with_optimized_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "baseline.mp4"
            new = Path(tmp) / "optimized.mp4"
            opts = dict(resolution=360, fps=12, mute=True, caption_style="classic", transition="fade")
            old_info = free_studio.render("Test", "La forêt est belle. La mer est calme.", old,
                                          use_fused_xfade=False, **opts)
            new_info = free_studio.render("Test", "La forêt est belle. La mer est calme.", new,
                                          use_fused_xfade=True, **opts)
            self.assertEqual(old_info["render_performance"]["compositing_pipeline"], "legacy-xfade")
            self.assertEqual(new_info["render_performance"]["compositing_pipeline"], "fused-xfade")
            self.assertEqual(old_info["size"], new_info["size"])
            self.assertEqual(old_info["segments"], new_info["segments"])
            self.assertAlmostEqual(free_studio.duration_seconds(old),
                                   free_studio.duration_seconds(new), delta=.15)
            self.assertEqual(old.with_suffix(".srt").read_text(),new.with_suffix(".srt").read_text())
