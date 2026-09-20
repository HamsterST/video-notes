import json
import unittest
from unittest.mock import MagicMock, patch

from video_notes.diagnostics import check_login
from video_notes.core import get_video_subtitles


class DiagnosticTests(unittest.TestCase):
    def check(self, payload):
        ydl = MagicMock()
        ydl.urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return check_login(ydl, 'https://www.bilibili.com/video/x')

    def test_logged_in_no_account_leak(self):
        result = self.check({'code': 0, 'data': {'isLogin': True, 'uname': 'private-user', 'mid': 123}})
        self.assertEqual(result['status'], 'logged_in')
        self.assertNotIn('private-user', str(result))
        self.assertNotIn('123', str(result))

    def test_logged_out(self):
        self.assertEqual(self.check({'code': -101})['status'], 'not_logged_in')
        self.assertEqual(self.check({'code': 0, 'data': {'isLogin': False}})['status'], 'not_logged_in')

    def test_error_is_not_logged_out(self):
        self.assertEqual(self.check({'code': -412})['status'], 'unknown')

    def test_domain_boundary(self):
        ydl = MagicMock()
        self.assertEqual(check_login(ydl, 'https://bilibili.com.example.org/v')['status'], 'unsupported')
        ydl.urlopen.assert_not_called()

    @patch('video_notes.core.check_login')
    @patch('video_notes.core.load_cookies')
    @patch('video_notes.core.yt_dlp.YoutubeDL')
    def test_disabled_does_not_request_login(self, factory, loader, check):
        cookie = MagicMock()
        cookie.domain = '.bilibili.com'
        cookie.is_expired.return_value = False
        loader.return_value = [cookie]
        factory.return_value.__enter__.return_value.extract_info.return_value = {'id': 'x'}
        result = get_video_subtitles('https://www.bilibili.com/video/x', cookies_from_browser='chrome', diagnostics=False)
        check.assert_not_called()
        self.assertNotIn('diagnostics', result)
        self.assertIsNone(result['authentication']['login_verified'])
