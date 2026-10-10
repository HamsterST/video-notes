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
                self.assertEqual(result['provenance']['device'], 'cuda')
                self.assertEqual(result['provenance']['compute_type'], 'float16')
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

    def test_reused_media_info_skips_second_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict('sys.modules', {'faster_whisper': self.fake_whisper()}), patch('video_notes.media.yt_dlp.YoutubeDL') as factory:
                ydl = factory.return_value.__enter__.return_value
                target = Path(temp) / hashlib.sha256(b'https://example.org/video').hexdigest()[:16] / 'clip.mp4'
                ydl.prepare_filename.return_value = str(target)
                ydl.process_info.side_effect = lambda info: target.write_bytes(b'video')
                info = {'id': 'clip', 'url': 'https://cdn.example/temporary-token'}
                result = download_and_transcribe('https://example.org/video', download_dir=temp,
                                                 model_dir=temp, model='small', media_info=info)
                self.assertEqual(result['status'], 'ok')
                ydl.extract_info.assert_not_called()
                ydl.process_info.assert_called_once_with(info)

    def test_douyin_download_uses_feed_info_without_yt_dlp(self):
        url = 'https://www.douyin.com/video/7638051474626383155'
        with tempfile.TemporaryDirectory() as temp:
            def save_media(info, directory, progress):
                path = directory / (info['id'] + '.mp4')
                path.write_bytes(b'fake-video')
                return path
            with (patch.dict('sys.modules', {'faster_whisper': self.fake_whisper()}),
                  patch('video_notes.media.yt_dlp.YoutubeDL', side_effect=AssertionError('yt-dlp extractor')),
                  patch('video_notes.media.fetch_video_info', side_effect=AssertionError('second feed request')),
                  patch('video_notes.media.download_video', side_effect=save_media) as downloader):
                info = {'id': '7638051474626383155', '_douyin_media_urls': ['https://v1.douyinvod.com/video']}
                result = download_and_transcribe(url, download_dir=temp, model_dir=temp, model='small', media_info=info)
                self.assertEqual(result['status'], 'ok')
                downloader.assert_called_once()
                self.assertEqual(downloader.call_args.args[0], info)
