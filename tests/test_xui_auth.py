import json
import time
import unittest
from unittest.mock import patch

import requests
import xui_api as api


def response(status=200, body=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body if body is not None else {'success': True}).encode()
    result.url = 'https://panel.example/panel/api/inbounds/addClient'
    return result


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch.object(api, 'session'),
            patch.object(api, '_session_authenticated', True),
            patch.object(api, '_last_login_time', time.time()),
        ]
        self.session = self.patches[0].start()
        for item in self.patches[1:]:
            item.start()
        self.addCleanup(patch.stopall)

    def test_expired_session_logs_in_again(self):
        api._last_login_time = 0
        self.session.post.return_value = response(body={'success': False})
        self.assertFalse(api.ensure_authenticated())

    def test_login_rejects_unsuccessful_json(self):
        self.session.post.return_value = response(body={'success': False})
        self.assertFalse(api.login_to_xui(force=True))
        self.assertFalse(api._session_authenticated)

    def test_login_rejects_html(self):
        result = response()
        result._content = b'<html>Login</html>'
        self.session.post.return_value = result
        self.assertFalse(api.login_to_xui(force=True))

    def test_create_recovers_from_unauthenticated_responses(self):
        for status in (401, 404):
            with self.subTest(status=status):
                self.session.post.side_effect = [response(status), response(), response()]
                client_id, error = api.create_client('test@example.com', 1024, 1900000000000)
                self.assertIsNone(error)
                self.assertIsNotNone(client_id)

    def test_real_404_is_retried_only_once(self):
        self.session.post.side_effect = [response(404), response(), response(404)]
        client_id, error = api.create_client('test@example.com', 1024, 1900000000000)
        self.assertIsNone(client_id)
        self.assertIn('404', error)
        self.assertEqual(self.session.post.call_count, 3)

    def test_failed_relogin_invalidates_session(self):
        self.session.post.side_effect = [response(404), response(body={'success': False})]
        client_id, error = api.create_client('test@example.com', 1024, 1900000000000)
        self.assertIsNone(client_id)
        self.assertIsNotNone(error)
        self.assertFalse(api._session_authenticated)

    def test_list_recovers_from_404(self):
        self.session.get.side_effect = [response(404), response(body={'success': True, 'obj': []})]
        self.session.post.return_value = response()
        self.assertEqual(api.get_all_clients(), [])

    def test_status_recovers_from_404(self):
        self.session.get.side_effect = [response(404), response(body={
            'success': True, 'obj': {'total': 1073741824, 'expiryTime': 1900000000000}
        })]
        self.session.post.return_value = response()
        self.assertEqual(api.get_client_status('test@example.com')['total_gb'], 1)

    def test_extension_recovers_from_404(self):
        self.session.get.return_value = response(body={
            'success': True, 'obj': {'total': 1073741824, 'expiryTime': 1900000000000}
        })
        self.session.post.side_effect = [response(404), response(), response()]
        self.assertEqual(api.extend_client('test@example.com', 'test-id', 1), (True, None))

    def test_create_does_not_retry_timeout(self):
        self.session.post.side_effect = requests.Timeout('request timed out')
        client_id, error = api.create_client('test@example.com', 1024, 1900000000000)
        self.assertIsNone(client_id)
        self.assertIn('timed out', error)
        self.assertEqual(self.session.post.call_count, 1)

    def test_fresh_session_is_reused(self):
        self.assertTrue(api.ensure_authenticated())
        self.session.post.assert_not_called()

    def test_delete_recovers_from_404(self):
        self.session.post.side_effect = [response(404), response(), response()]
        self.assertEqual(api.delete_client('test-id'), (True, None))


if __name__ == '__main__':
    unittest.main()
