import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from video_notes.workflow import run_workflow


def failed(status='no_subtitles'):
    return {'status': status, 'message': '未获取到字幕', 'warnings': [], 'segments': []}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch('video_notes.cache.CACHE_DIR', Path(temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch('video_notes.workflow.get_video_subtitles', return_value={'status': 'ok'})
    def test_success_does_not_download(self, query):
        convert = Mock()
        result = run_workflow('https://example.org/v', transcriber=convert)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(query.call_args.kwargs['cookies_from_browser'], 'chrome')
        convert.assert_not_called()

    @patch('video_notes.workflow.get_video_subtitles')
    def test_retry_then_transcribe(self, query):
        query.side_effect = [failed(), failed()]
        convert = Mock(return_value={'status': 'ok'})
        run_workflow('https://example.org/v', choose=lambda: 'retry', transcriber=convert)
        self.assertEqual(query.call_count, 2)
        convert.assert_called_once()

    @patch('video_notes.workflow.get_video_subtitles')
    def test_retry_success(self, query):
        query.side_effect = [failed(), {'status': 'ok'}]
        convert = Mock()
        run_workflow('https://example.org/v', choose=lambda: 'retry', transcriber=convert)
        convert.assert_not_called()

    @patch('video_notes.workflow.get_video_subtitles', return_value=failed('cookie_load_failed'))
    def test_cookie_failure_downloads_anonymously(self, query):
        convert = Mock(return_value={'status': 'ok'})
        run_workflow('https://example.org/v', choose=lambda: 'transcribe', transcriber=convert)
        self.assertIsNone(convert.call_args.kwargs['cookies_from_browser'])
        query.assert_called_once()

    @patch('video_notes.workflow.get_video_subtitles')
    def test_no_input_no_download(self, query):
        query.return_value = failed()
        convert = Mock()
        result = run_workflow('https://example.org/v', transcriber=convert)
        self.assertEqual(result['next_action'], 'retry_or_transcribe')
        convert.assert_not_called()

    @patch('video_notes.workflow.get_video_subtitles')
    def test_cancel_and_stop(self, query):
        convert = Mock()
        query.return_value = failed()
        self.assertEqual(run_workflow('https://example.org/v', choose=lambda: None, transcriber=convert)['status'], 'cancelled')
        run_workflow('https://example.org/v', fallback='stop', transcriber=convert)
        convert.assert_not_called()

    @patch('video_notes.workflow.get_video_subtitles', return_value={'status': 'unsupported_input'})
    def test_playlist_never_downloaded(self, query):
        convert = Mock()
        run_workflow('https://example.org/v', fallback='transcribe', transcriber=convert)
        convert.assert_not_called()
