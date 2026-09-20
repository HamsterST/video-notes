import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from video_notes.media import download_and_transcribe, canonical_media_url, download_progress


class MediaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch('video_notes.cache.CACHE_DIR', Path(temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_tracking_removed_but_part_preserved(self):
        base = 'https://www.bilibili.com/video/BV123/'
        self.assertEqual(canonical_media_url(base+'?spm_id_from=x&vd_source=y'), base)
        self.assertNotEqual(canonical_media_url(base+'?p=1'), canonical_media_url(base+'?p=2'))
        other = 'https://example.org/v?vd_source=keep'
        self.assertEqual(canonical_media_url(other), other)

    def test_download_progress_no_url_or_cookie(self):
        reporter = Mock()
        hook = download_progress(reporter)
        hook({'status': 'finished', 'filename': 'private', 'url': 'secret'})
        self.assertNotIn('secret', str(reporter.call_args))
        reporter.assert_called_once()
    def fake_whisper(self):
        model = Mock()
        model.return_value.transcribe.return_value = (
            iter([SimpleNamespace(start=0, end=8, text=' hello ')]),
            SimpleNamespace(duration=8, language='en'))
        return SimpleNamespace(WhisperModel=model)

    def test_completed_download_reused(self):
        url = 'https://example.org/video'
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / hashlib.sha256(url.encode()).hexdigest()[:16]
            directory.mkdir()
            (directory / 'clip.mp4').write_bytes(b'fake-video')
            (directory / 'media.json').write_text(json.dumps({'file': 'clip.mp4', 'size': 10}))
            with patch.dict('sys.modules', {'faster_whisper': self.fake_whisper()}), patch('video_notes.media.yt_dlp.YoutubeDL') as downloader:
                result = download_and_transcribe(url, download_dir=temp, model_dir=temp, model='small')
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(result['segments'][0]['text'], 'hello')
                downloader.assert_not_called()

    def test_download_then_transcribe(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict('sys.modules', {'faster_whisper': self.fake_whisper()}), patch('video_notes.media.yt_dlp.YoutubeDL') as factory:
                ydl = factory.return_value.__enter__.return_value
                ydl.extract_info.return_value = {'id': 'clip'}
                target = Path(temp) / hashlib.sha256(b'https://example.org/video').hexdigest()[:16] / 'clip.mp4'
                ydl.prepare_filename.return_value = str(target)
                ydl.process_info.side_effect = lambda info: target.write_bytes(b'video')
                result = download_and_transcribe('https://example.org/video', download_dir=temp, model_dir=temp, model='small')
                self.assertEqual(result['status'], 'ok')
                ydl.process_info.assert_called_once()
                self.assertEqual(factory.call_args.args[0]['format'], 'bestaudio/best')
                self.assertNotIn('merge_output_format', factory.call_args.args[0])
                self.assertTrue((target.parent / 'media.json').exists())
