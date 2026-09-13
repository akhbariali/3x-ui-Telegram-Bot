from datetime import timedelta
import unittest
from unittest.mock import patch
import xui_api as api
from test_xui_auth import response


class V3Tests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(api, 'XUI_API_TOKEN', ''))
        self.enterContext(patch.object(api, '_csrf_token', None))
        self.session = self.enterContext(patch.object(api, 'session'))
        self.enterContext(patch.object(api, 'ensure_authenticated', return_value=True))
        self.session.post.return_value = response()
        self.session.get.return_value = response(body={'success': True, 'obj': 'csrf'})

    def test_create_unlimited_with_ip_limit(self):
        for limit in (1, 2, 3):
            client_id, error = api.create_client('alice', 0, timedelta(days=31), limit_ip=limit)
            self.assertIsNone(error)
            args, kwargs = self.session.post.call_args
            self.assertTrue(args[0].endswith('/panel/api/clients/add'))
            self.assertEqual(kwargs['json']['inboundIds'], [api.INBOUND_ID])
            client = kwargs['json']['client']
            self.assertEqual((client['totalGB'], client['limitIp'], client['tgId']), (0, limit, 0))
            self.assertEqual(client['id'], client_id)

    def test_renewal_preserves_identity_and_sets_unlimited(self):
        client = {'id': 17, 'uuid': 'existing-uuid', 'email': 'alice', 'subId': 'original-sub',
                  'totalGB': 10737418240, 'expiryTime': 1900000000000, 'limitIp': 1,
                  'flow': 'xtls-rprx-vision', 'enable': True, 'tgId': 42,
                  'reverse': None, 'allowedIPs': '', 'comment': 'keep me', 'limitHwid': 4}
        self.session.get.side_effect = lambda url, **kw: response(body={'success': True, 'obj':
            'csrf' if url.endswith('/csrf-token') else {'client': client, 'inboundIds': [api.INBOUND_ID]}})
        self.assertEqual(api.extend_client('alice', 'existing-uuid', 0, timedelta(days=31),
                                          limit_ip=3, unlimited=True), (True, None))
        args, kwargs = self.session.post.call_args
        self.assertTrue(args[0].endswith('/panel/api/clients/update/alice'))
        body = kwargs['json']
        self.assertEqual((body['id'], body['subId'], body['flow']),
                         ('existing-uuid', 'original-sub', 'xtls-rprx-vision'))
        self.assertEqual((body['totalGB'], body['limitIp'], body['limitHwid']), (0, 3, 4))
        self.assertEqual(body['expiryTime'], 1900000000000 + 31 * 86400000)

    def test_list_translates_numeric_id_to_uuid(self):
        self.session.get.return_value = response(body={'success': True, 'obj': [
            {'id': 17, 'uuid': 'uuid', 'email': 'alice', 'totalGB': 0, 'expiryTime': 1900000000000,
             'enable': True, 'inboundIds': [api.INBOUND_ID], 'traffic': {'up': 100, 'down': 20}},
            {'id': 18, 'uuid': 'other', 'email': 'bob', 'inboundIds': [api.INBOUND_ID + 1]}
        ]})
        clients = api.get_all_clients()
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0]['id'], 'uuid')
        self.assertEqual(clients[0]['total_gb'], 0)

    def test_delete_resolves_uuid_to_email(self):
        self.session.get.side_effect = lambda url, **kw: response(body={'success': True, 'obj':
            'csrf' if url.endswith('/csrf-token') else [
                {'id': 17, 'uuid': 'uuid', 'email': 'alice', 'inboundIds': [api.INBOUND_ID]}]})
        self.assertEqual(api.delete_client('uuid'), (True, None))
        self.assertTrue(self.session.post.call_args.args[0].endswith('/panel/api/clients/del/alice'))
