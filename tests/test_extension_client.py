import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from video_notes import extension_client as client


class ExtensionClientTests(unittest.TestCase):
    def setUp(self):
        self.url = 'https://www.youtube.com/watch?v=test'
        self.response = Mock()
        self.response.__enter__ = Mock(return_value=self.response)
        self.response.__exit__ = Mock(return_value=False)
        self.response.read.return_value = json.dumps({'type': 'cookies_response', 'cookies': []}).encode()
        self.opener = Mock()
        self.opener.open.return_value = self.response
        for name, value in [('_token', Mock(return_value='private-token')),
                            ('build_opener', Mock(return_value=self.opener)),
                            ('registration_missing', Mock(return_value=False)),
                            ('ensure_registered', Mock()), ('make_jar', Mock(return_value=(self.url, 'jar')))]:
            p = patch.object(client, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(client.time, 'sleep')
        p.start()
        self.addCleanup(p.stop)

    def test_live_connection_never_reads_or_writes_registry(self):
        client.registration_missing.side_effect = PermissionError('denied')
        client.ensure_registered.side_effect = PermissionError('denied')
        self.assertEqual(client.extension_cookies(self.url), 'jar')
        client.registration_missing.assert_not_called()
        client.ensure_registered.assert_not_called()

    def test_registered_but_starting_only_retries_connection(self):
        self.opener.open.side_effect = [URLError(ConnectionRefusedError()), self.response]
        self.assertEqual(client.extension_cookies(self.url), 'jar')
        client.registration_missing.assert_called_once()
        client.ensure_registered.assert_not_called()

    def test_missing_registration_is_created_once(self):
        self.opener.open.side_effect = [URLError(ConnectionRefusedError()), self.response]
        client.registration_missing.return_value = True
        self.assertEqual(client.extension_cookies(self.url), 'jar')
        client.ensure_registered.assert_called_once()

    def test_first_install_without_token(self):
        client._token.side_effect = [FileNotFoundError(), 'new-token']
        client.registration_missing.return_value = True
        self.assertEqual(client.extension_cookies(self.url), 'jar')
        client.ensure_registered.assert_called_once()

    def test_registration_write_denied_has_specific_error(self):
        self.opener.open.side_effect = URLError(ConnectionRefusedError())
        client.registration_missing.return_value = True
        client.ensure_registered.side_effect = PermissionError('private-details')
        with self.assertRaisesRegex(ValueError, '注册失败.*权限不足') as error:
            client.extension_cookies(self.url)
        self.assertNotIn('private-details', str(error.exception))

    def test_registry_read_denial_does_not_trigger_write(self):
        self.opener.open.side_effect = URLError(ConnectionRefusedError())
        client.registration_missing.side_effect = PermissionError()
        with self.assertRaisesRegex(ValueError, '无权限读取'):
            client.extension_cookies(self.url)
        client.ensure_registered.assert_not_called()

    def test_registered_but_offline_reports_connection_error(self):
        self.opener.open.side_effect = URLError(ConnectionRefusedError())
        with patch.object(client.time, 'monotonic', side_effect=[0, 41]):
            with self.assertRaisesRegex(ValueError, '已注册.*连接被拒绝'):
                client.extension_cookies(self.url)
        client.ensure_registered.assert_not_called()

    def test_http_auth_failure_does_not_reregister_or_leak_body(self):
        self.opener.open.side_effect = HTTPError('http://localhost', 403, 'private-token', {}, io.BytesIO(b'private-body'))
        with self.assertRaisesRegex(ValueError, '认证失败') as error:
            client.extension_cookies(self.url)
        self.assertNotIn('private', str(error.exception))
        client.registration_missing.assert_not_called()
        client.ensure_registered.assert_not_called()

    def test_invalid_response_is_not_reported_as_login_failure(self):
        self.response.read.return_value = b'private-invalid-json'
        with self.assertRaisesRegex(ValueError, '数据格式无效') as error:
            client.extension_cookies(self.url)
        self.assertNotIn('private', str(error.exception))
        client.ensure_registered.assert_not_called()
