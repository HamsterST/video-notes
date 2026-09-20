import contextlib
import io
import json
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_notes import cache, settings
from video_notes.core import get_video_subtitles
from video_notes.media import download_and_transcribe
from video_notes.workflow import run_workflow
from video_notes.cli import main


class CacheSettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in [('video_notes.cache.CACHE_DIR', self.root / 'cache'),
                            ('video_notes.settings.SETTINGS_FILE', self.root / 'settings.json'),
                            ('video_notes.settings.MODELS_DIR', self.root / 'models')]:
            p = patch(name, value)
            p.start()
            self.addCleanup(p.stop)
        self.video = self.root / 'clip.m4a'
        self.video.write_bytes(b'audio')
        self.url = 'https://example.org/video'

    def whisper(self, failure=None):
        def transcribe(*args, **kwargs):
            def rows():
                yield SimpleNamespace(start=0, end=8, text='hello')
                if failure:
                    raise failure
            return rows(), SimpleNamespace(duration=8, language='en')
        model = Mock()
        model.return_value.transcribe.side_effect = transcribe
        return SimpleNamespace(WhisperModel=model)

    def run_media(self, **kwargs):
        return download_and_transcribe(self.url, local_video=str(self.video),
                                      model_dir=str(self.root / 'models'), **kwargs)

    def test_first_setup_asks_once_and_persists_choice(self):
        with patch('builtins.input', return_value='2') as question, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(settings.configure_model(True), 'large-v3-turbo')
            self.assertEqual(settings.configure_model(True), 'large-v3-turbo')
            question.assert_called_once()
        with patch('builtins.input', side_effect=AssertionError('must not ask')):
            self.assertEqual(settings.configure_model(False), 'large-v3-turbo')

    def test_noninteractive_defaults_without_question_or_model_download(self):
        with patch('builtins.input', side_effect=AssertionError('must not ask')):
            self.assertIsNone(settings.configure_model(False))
        self.assertFalse(settings.SETTINGS_FILE.exists())
        self.assertFalse((self.root / 'models').exists())

    def test_cli_switch_without_url_or_network(self):
        with patch('sys.argv', ['video_notes', '--set-model', 'large-v3']), contextlib.redirect_stderr(io.StringIO()), patch('video_notes.cli.run_workflow') as run:
            self.assertEqual(main(), 0)
            run.assert_not_called()
        self.assertEqual(settings.saved_model(), 'large-v3')

    def test_cache_skips_engine_and_recalculates_output_language(self):
        engine = self.whisper()
        with patch.dict('sys.modules', {'faster_whisper': engine}):
            first = self.run_media(model='small')
            second = self.run_media(model='small', output_language='ja')
        self.assertFalse(first['cache']['reused'])
        self.assertTrue(second['cache']['reused'])
        self.assertEqual(second['output_language'], 'ja')
        self.assertEqual(first['provenance'], second['provenance'])
        engine.WhisperModel.assert_called_once()

    def test_model_beam_and_audio_change_invalidate_cache(self):
        engine = self.whisper()
        with patch.dict('sys.modules', {'faster_whisper': engine}):
            for model, beam in [('small', 1), ('large-v3', 1), ('large-v3', 5)]:
                result = self.run_media(model=model, beam_size=beam)
                self.assertFalse(result['cache']['reused'])
            self.video.write_bytes(b'other')  # Same size; content fingerprint must change.
            self.assertFalse(self.run_media(model='large-v3', beam_size=5)['cache']['reused'])
        self.assertEqual(engine.WhisperModel.call_count, 4)

    def test_interrupt_and_failed_generator_never_commit_complete_cache(self):
        for exc in (KeyboardInterrupt(), RuntimeError('failed after one segment')):
            with patch.dict('sys.modules', {'faster_whisper': self.whisper(exc)}):
                if isinstance(exc, KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        self.run_media(model='small')
                else:
                    self.assertEqual(self.run_media(model='small')['status'], 'transcription_failed')
            self.assertEqual(list((self.root / 'cache').rglob('*.json')), [])

    def test_corrupt_result_recomputed_and_refresh_keeps_local_media(self):
        engine = self.whisper()
        with patch.dict('sys.modules', {'faster_whisper': engine}), patch('video_notes.media.yt_dlp.YoutubeDL') as network:
            self.run_media(model='small')
            p = next((self.root / 'cache').rglob('*.json'))
            envelope = json.loads(p.read_text(encoding='utf-8'))
            envelope['result']['segments'][0]['text'] = 'tampered'
            p.write_text(json.dumps(envelope), encoding='utf-8')
            self.assertFalse(self.run_media(model='small')['cache']['reused'])
            self.assertFalse(self.run_media(model='small', force=True)['cache']['reused'])
            network.assert_not_called()
        self.assertEqual(engine.WhisperModel.call_count, 3)

    def test_subtitle_cache_bypasses_login_and_network_and_preserves_source(self):
        with patch('video_notes.core.yt_dlp.YoutubeDL') as factory:
            ydl = factory.return_value.__enter__.return_value
            ydl.extract_info.return_value = {'id': 'a', 'duration': 8, 'automatic_captions': {
                'en': [{'ext': 'srt', 'data': '1\n00:00:00,000 --> 00:00:08,000\nhello'}]}}
            first = get_video_subtitles(self.url)
        loader = Mock(side_effect=AssertionError('login must be skipped'))
        with patch('video_notes.workflow.get_video_subtitles', side_effect=AssertionError('network must be skipped')):
            second = run_workflow(self.url, cookie_loader=loader, output_language='ja')
        self.assertTrue(second['cache']['reused'])
        self.assertEqual(second['provenance']['source'], 'automatic')
        self.assertEqual(second['provenance'], first['provenance'])
        self.assertNotIn('authentication', second)
        self.assertNotIn('diagnostics', second)
        loader.assert_not_called()

    def test_cached_transcript_workflow_needs_neither_login_nor_network(self):
        with patch.dict('sys.modules', {'faster_whisper': self.whisper()}):
            self.run_media(model='small')
        lookup = lambda url, **kwargs: self.run_media(model='small', cache_only=True, **kwargs)
        with patch('video_notes.workflow.get_video_subtitles', side_effect=AssertionError('network')):
            result = run_workflow(self.url, fallback='transcribe', transcript_loader=lookup,
                                  cookie_loader=Mock(side_effect=AssertionError('login')))
        self.assertTrue(result['cache']['reused'])

    def test_subtitle_language_and_refresh_do_not_use_wrong_cache(self):
        with patch('video_notes.core._get_video_subtitles') as query:
            query.return_value = {'status': 'ok', 'segments': [{'start': 0, 'end': 8, 'text': 'hi'}],
                                  'video': {'duration': 8}, 'selected_subtitle': {'source': 'platform', 'language': 'en', 'format': 'srt'}}
            get_video_subtitles(self.url, subtitle_language='en')
            get_video_subtitles(self.url, subtitle_language='ja')
            get_video_subtitles(self.url, subtitle_language='en', force=True)
            self.assertEqual(query.call_count, 3)

    def test_existing_subtitle_file_skips_all_network(self):
        path = self.root / 'saved.srt'
        path.write_text('1\n00:00:00,000 --> 00:00:08,000\nhello', encoding='utf-8')
        with patch('video_notes.workflow.get_video_subtitles', side_effect=AssertionError('network')):
            result = run_workflow(self.url, subtitle_file=path,
                                  cookie_loader=Mock(side_effect=AssertionError('login')))
        self.assertEqual(result['segments'][0]['text'], 'hello')
        self.assertEqual(result['provenance']['source'], 'local_file')
        self.assertNotIn('model', result['provenance'])

    def test_model_switch_reuses_registered_download(self):
        media_dir = self.root / 'downloads'
        folder = media_dir / hashlib.sha256(self.url.encode()).hexdigest()[:16]
        folder.mkdir(parents=True)
        (folder / 'clip.m4a').write_bytes(b'audio')
        (folder / 'media.json').write_text(json.dumps({'file': 'clip.m4a', 'size': 5}), encoding='utf-8')
        engine = self.whisper()
        with patch.dict('sys.modules', {'faster_whisper': engine}), patch('video_notes.media.yt_dlp.YoutubeDL', side_effect=AssertionError('download')):
            for model in ('small', 'large-v3'):
                result = download_and_transcribe(self.url, download_dir=media_dir, model=model, model_dir=self.root / 'models')
                self.assertEqual(result['status'], 'ok')
        self.assertEqual(engine.WhisperModel.call_count, 2)

    def test_native_host_returns_cached_subtitle_and_keeps_framing(self):
        from video_notes import native_host
        from video_notes.core import subtitle_cache_key
        url = 'https://www.youtube.com/watch?v=example'
        result = {'status': 'ok', 'segments': [{'start': 0, 'end': 8, 'text': 'hi'}],
                  'video': {'duration': 8}, 'warnings': [], 'message': 'cached',
                  'provenance': {'source': 'automatic'}}
        cache.write('subtitles', subtitle_cache_key(url), result)
        request = json.dumps({'version': 1, 'url': url, 'cookies': [], 'fallback': 'stop'}).encode()
        source = SimpleNamespace(buffer=io.BytesIO(struct.pack('=I', len(request)) + request))
        target = SimpleNamespace(buffer=io.BytesIO())
        with patch('video_notes.native_host.sys.stdin', source), patch('video_notes.native_host.sys.stdout', target), \
             patch('video_notes.native_host.os.name', 'posix'), \
             patch('video_notes.native_host.DATA_DIR', self.root), \
             patch('video_notes.native_host.RESULTS_DIR', self.root / 'results'), \
             patch('video_notes.workflow.get_video_subtitles', side_effect=AssertionError('network')):
            self.assertEqual(native_host.main(), 0)
        stream = io.BytesIO(target.buffer.getvalue())
        frames = []
        while header := stream.read(4):
            frames.append(json.loads(stream.read(struct.unpack('=I', header)[0])))
        self.assertEqual(frames[-1]['status'], 'ok')
        saved = json.loads(next((self.root / 'results').glob('*.json')).read_text(encoding='utf-8'))
        self.assertTrue(saved['cache']['reused'])
