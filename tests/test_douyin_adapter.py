import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from yt_dlp.extractor.tiktok import DouyinIE

from video_notes.cli import main
from video_notes.native_host import make_jar
from video_notes.urls import canonical_media_url, normalize_douyin_url
from video_notes.douyin import DouyinLookupError, fetch_video_info, download_video


VIDEO = 'https://www.douyin.com/video/7686057747742636526'
MODAL = 'https://www.douyin.com/user/self?from_tab_name=main&modal_id=7686057747742636526&showTab=favorite_collection'


class DouyinAdapterTests(unittest.TestCase):
    def test_modal_url_becomes_video_url_and_cache_identity(self):
        self.assertEqual(normalize_douyin_url(MODAL), VIDEO)
        self.assertTrue(DouyinIE.suitable(normalize_douyin_url(MODAL)))
        self.assertEqual(canonical_media_url(MODAL), canonical_media_url(VIDEO + '?from=share'))
        self.assertEqual(normalize_douyin_url('https://v.douyin.com/abc'), 'https://v.douyin.com/abc')
        self.assertEqual(normalize_douyin_url('https://evil.example/user/self?modal_id=7686057747742636526'),
                         'https://evil.example/user/self?modal_id=7686057747742636526')

    def test_native_host_accepts_video_and_only_douyin_cookies(self):
        url, jar = make_jar({'url': VIDEO, 'cookies': [
            {'domain': '.douyin.com', 'name': 'sessionid', 'value': 'one', 'path': '/'},
            {'domain': '.other.example', 'name': 'sessionid', 'value': 'two', 'path': '/'},
        ]})
        self.assertEqual(url, VIDEO)
        self.assertEqual([(c.domain, c.name, c.value) for c in jar], [('.douyin.com', 'sessionid', 'one')])
        self.assertEqual(make_jar({'url': MODAL, 'cookies': []})[0], VIDEO)
        for bad in ('https://fake-douyin.com/video/7686057747742636526',
                    'http://www.douyin.com/video/7686057747742636526'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                make_jar({'url': bad, 'cookies': []})

    @patch('video_notes.extension_client.extension_cookies', return_value=Mock())
    @patch('video_notes.cli.run_workflow', return_value={'status': 'ok', 'message': 'ok'})
    def test_cli_uses_extension_with_canonical_url(self, workflow, cookies):
        with patch.object(sys, 'argv', ['video-notes', MODAL, '--fallback', 'stop']), patch('builtins.print'):
            self.assertEqual(main(), 0)
        self.assertEqual(workflow.call_args.args[0], VIDEO)
        self.assertIs(workflow.call_args.kwargs['cookie_loader'], cookies)

    @patch('video_notes.douyin.urlopen')
    def test_feed_matches_id_and_exposes_real_caption_track(self, request):
        data = {'aweme_list': [
            {'aweme_id': 'other', 'video': {}},
            {'aweme_id': '7686057747742636526', 'desc': 'sample', 'video': {
                'duration': 10000,
                'play_addr': {'url_list': ['https://v1.douyinvod.com/video'], 'data_size': 36},
                'cla_info': {'caption_infos': [{'language_code': 'zh', 'url': 'https://cdn.example/caption.vtt',
                                                'format': 'webvtt', 'is_auto_generated': True}]}}}]}
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(data).encode()
        request.return_value = response
        info = fetch_video_info(VIDEO)
        self.assertEqual(info['id'], '7686057747742636526')
        self.assertEqual(info['duration'], 10)
        self.assertEqual(info['automatic_captions']['zh'][0]['ext'], 'vtt')
        self.assertEqual(info['_douyin_media_size'], 36)
        with self.assertRaises(DouyinLookupError):
            fetch_video_info('https://www.douyin.com/video/1111111111111111111')

    @patch('video_notes.douyin.urlopen')
    def test_download_validates_mp4_and_size(self, request):
        head = b'\x00\x00\x00\x20ftyp' + b'0' * 24
        response = Mock(status=200, url='https://v1.douyinvod.com/video')
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = [head, b'body', b'']
        request.return_value = response
        with tempfile.TemporaryDirectory() as temp:
            target = download_video({'id': '123', '_douyin_media_urls': [response.url],
                                     '_douyin_media_size': 36}, Path(temp), Mock())
            self.assertEqual(target.read_bytes(), head + b'body')
            self.assertFalse(target.with_suffix('.mp4.part').exists())

    @patch('video_notes.douyin.time.sleep')
    @patch('video_notes.douyin.urlopen')
    def test_transient_empty_feed_is_retried_once(self, request, sleep):
        empty = Mock(status=200)
        empty.__enter__ = Mock(return_value=empty)
        empty.__exit__ = Mock(return_value=False)
        empty.read.return_value = b'{"aweme_list": []}'
        found = Mock(status=200)
        found.__enter__ = Mock(return_value=found)
        found.__exit__ = Mock(return_value=False)
        found.read.return_value = json.dumps({'aweme_list': [{'aweme_id': '7686057747742636526',
            'video': {'play_addr': {'url_list': ['https://v1.douyinvod.com/video']}}}]}).encode()
        request.side_effect = [empty, found]
        self.assertEqual(fetch_video_info(VIDEO)['id'], '7686057747742636526')
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once()


if __name__ == '__main__':
    unittest.main()
