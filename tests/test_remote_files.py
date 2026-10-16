import re
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class RemoteStaticFilesTests(unittest.TestCase):
    def test_csp_token_never_persisted(self):
        html=(ROOT/'remote/index.html').read_text(encoding='utf-8')
        js=(ROOT/'remote/main.mjs').read_text(encoding='utf-8')
        self.assertIn("connect-src https://api.github.com",html)
        self.assertIn("script-src 'self'",html)
        self.assertIn('type="password"',html)
        self.assertEqual(js.count('localStorage.setItem('), 1) # centralized storage helper only
        self.assertIn('localStorage.setItem(key,',js)
        self.assertNotIn('token:',js)
        self.assertIn("$('token').value.trim()",js)
    def test_relative_static_paths(self):
        html=(ROOT/'remote/index.html').read_text(encoding='utf-8')
        for path in ('style.css','main.mjs','icon.svg','manifest.webmanifest'):
            self.assertIn('./'+path,html)
            self.assertTrue((ROOT/'remote'/path).is_file())
    def test_workflow_matches_form(self):
        yaml=(ROOT/'.github/workflows/free-video.yml').read_text(encoding='utf-8')
        self.assertIn('run-name:',yaml)
        self.assertIn('request_id:',yaml)
        self.assertIn('inputs.request_id',yaml)
        self.assertIn('script_mode:',yaml)
        self.assertIn('word_alignment:',yaml)
        self.assertIn('stable-ts==2.19.1',yaml)
        self.assertIn('ollama pull qwen2.5:1.5b',yaml)
        self.assertIn('ARGS+=(--ollama-model qwen2.5:1.5b',yaml)
        html=(ROOT/'remote/index.html').read_text(encoding='utf-8')
        self.assertIn('id="script_mode"',html)
        self.assertIn('id="voice"',html)
        self.assertIn('id="visual_style"',html)
        self.assertIn('id="transition"',html)
        self.assertIn('transition:',yaml)
        self.assertIn('--transition "$TRANSITION"',yaml)
        self.assertIn('visual_style:',yaml)
        self.assertIn('--visual-style "$VISUAL_STYLE"',yaml)
        self.assertIn('piper-tts==1.8.0',yaml)
        self.assertIn('sha256sum --check',yaml)
        self.assertIn('ARGS+=(--tts piper --piper-model',yaml)

if __name__=='__main__':unittest.main()
