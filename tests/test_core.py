import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from video_notes.core import candidates, check_coverage, get_video_subtitles
from video_notes.parsers import parse_subtitles


class SubtitleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch('video_notes.cache.CACHE_DIR', Path(temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch('video_notes.core.load_cookies', side_effect=RuntimeError('secret cookie diagnostic'))
    @patch('video_notes.core.yt_dlp.YoutubeDL')
    def test_cookie_failure_is_separate(self, factory, loader):
        result = get_video_subtitles('https://example.org/video', cookies_from_browser='chrome')
        self.assertEqual(result['status'], 'cookie_load_failed')
        self.assertNotIn('secret', str(result))
        factory.return_value.__enter__.return_value.extract_info.assert_not_called()

    @patch('video_notes.core.load_cookies', return_value=[])
    @patch('video_notes.core.yt_dlp.YoutubeDL')
    def test_missing_site_cookies(self, factory, loader):
        self.assertEqual(get_video_subtitles('https://example.org/video', cookies_from_browser='chrome')['status'], 'site_cookies_missing')

    @patch('video_notes.core.load_cookies')
    @patch('video_notes.core.yt_dlp.YoutubeDL')
    def test_cookie_request_does_not_claim_login(self, factory, loader):
        cookie = MagicMock()
        cookie.domain = '.example.org'
        cookie.is_expired.return_value = False
        loader.return_value = [cookie]
        ydl = factory.return_value.__enter__.return_value
        ydl.extract_info.return_value = {'id': 'x', 'subtitles': {}}
        result = get_video_subtitles('https://example.org/video', cookies_from_browser='chrome', browser_profile='Profile 1')
        self.assertEqual(result['status'], 'no_subtitles')
        self.assertFalse(result['authentication']['login_verified'])
        self.assertIn('已携带', result['message'])
        self.assertIs(ydl.cookiejar, loader.return_value)
        loader.assert_called_once_with(None, ('chrome', 'Profile 1'), ydl)

    def test_bilibili(self):
        rows = parse_subtitles('{"body":[{"from":0,"to":2,"content":"你好"}]}', 'json')
        self.assertEqual(rows, [dict(start=0., end=2., text='你好')])

    def test_json3(self):
        rows = parse_subtitles('{"events":[{"tStartMs":1000,"dDurationMs":2000,"segs":[{"utf8":"Hi"},{"utf8":" there"}]}]}', 'json3')
        self.assertEqual(rows[0], dict(start=1., end=3., text='Hi there'))

    def test_vtt(self):
        rows = parse_subtitles('WEBVTT\n\n00:01.000 --> 00:03.000 align:start\n<c>Hello &amp; hi</c>\n', 'vtt')
        self.assertEqual(rows[0]['text'], 'Hello & hi')

    def test_srt(self):
        self.assertEqual(parse_subtitles('1\n00:01:02,500 --> 00:01:04,000\nhello', 'srt')[0]['start'], 62.5)

    def test_invalid(self):
        for text in ('<html>denied</html>', 'WEBVTT'):
            with self.assertRaises(ValueError):
                parse_subtitles(text, 'vtt')
        with self.assertRaises(ValueError):
            parse_subtitles('{"body":[{"from":2,"to":1,"content":"bad"}]}', 'json')

    def test_filter_danmaku(self):
        self.assertEqual(candidates({'subtitles': {'danmaku': [{'ext': 'xml'}]}}), [])

    def test_coverage(self):
        self.assertEqual(check_coverage(2400, [{'end': 1500}])['status'], 'possible_missing_tail')
        self.assertEqual(check_coverage(2400, [{'end': 2300}])['status'], 'no_large_tail_gap')
        self.assertEqual(check_coverage(2400, [{'end': 2200}])['status'], 'no_large_tail_gap')
        self.assertEqual(check_coverage(None, [{'end': 2}])['status'], 'unknown')

    @patch('video_notes.core.yt_dlp.YoutubeDL')
    def test_in_memory_fetch(self, factory):
        ydl = factory.return_value.__enter__.return_value
        ydl.extract_info.return_value = {'id': 'a', 'duration': 20, 'subtitles': {
            'en': [{'ext': 'vtt', 'url': 'https://example.org/captions'}]}}
        ydl.urlopen.return_value.__enter__.return_value.read.return_value = b'WEBVTT\n\n00:00.000 --> 00:20.000\nHi'
        result = get_video_subtitles('https://example.org/video')
        self.assertEqual(result['status'], 'ok')
        ydl.extract_info.assert_called_once_with('https://example.org/video', download=False)
        ydl.urlopen.assert_called_once()
        self.assertNotIn('https://example.org/captions', str(result))

    @patch('video_notes.core.yt_dlp.YoutubeDL')
    def test_missing_subtitles(self, factory):
        ydl = factory.return_value.__enter__.return_value
        ydl.extract_info.return_value = {'id': 'x', 'subtitles': {'danmaku': [{'ext': 'xml'}]}}
        self.assertEqual(get_video_subtitles('https://example.org/video')['status'], 'no_subtitles')
        ydl.urlopen.assert_not_called()

    @patch('video_notes.core.yt_dlp.YoutubeDL')
    def test_fetch_failure(self, factory):
        ydl = factory.return_value.__enter__.return_value
        ydl.extract_info.return_value = {'id': 'x', 'subtitles': {'en': [{'ext': 'srt', 'data': 'broken'}]}}
        self.assertEqual(get_video_subtitles('https://example.org/video')['status'], 'subtitle_fetch_failed')


if __name__ == '__main__':
    unittest.main()
