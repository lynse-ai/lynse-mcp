import asyncio
import base64
from dataclasses import replace
import hashlib
import logging
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from lynse_account import AccountBackend, AccountError, AccountLynseAPI
from lynse import LynseAPIError
from lynse_remote import API_PATH, Settings, create_app
from lynse_oauth_store import fingerprint
from lynse_test_client import TestOAuthClient

RESOURCE = 'https://mcp.lynse.cn/mcp'
WEB = 'https://www.lynse.cn'
logging.disable(logging.INFO)


class FakeBackend:
    def __init__(self):
        self.calls = []

    def sms(self, phone):
        self.calls.append(('sms', phone))
        return {'cooldown': 60}

    def phone_login(self, phone, code):
        self.calls.append(('login', phone, code))
        # A distinct token per login mirrors the real backend; count grows per call.
        count = sum(1 for call in self.calls if call[0] == 'login')
        return {'upstream_token': f'private-upstream-{phone}-{count}', 'subject': phone,
                'name': 'User ' + phone[-4:], 'lifetime': 86400}

    def current_user(self, token):
        user_id = token.removeprefix('private-upstream-').rsplit('-', 1)[0]
        return {'id': user_id, 'nickname': 'User ' + user_id[-4:]}

    def wechat_url(self):
        return 'https://open.weixin.qq.com/connect/oauth2/authorize?appid=test&state=wechat-state&redirect_uri=https%3A%2F%2Fold.test%2Fcallback&scope=snsapi_userinfo'

    def wechat_login(self, code, state):
        return self.phone_login('13900000001', code)


class AccountOAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'oauth.sqlite3'
        self.key = Fernet.generate_key().decode()
        self.settings = Settings(RESOURCE, WEB, 'https://api.lynse.cn', self.key, str(self.db),
                                 WEB + '/platform/oauth/wechat', test_ui=True)
        self.backend = FakeBackend()
        self.app = create_app(self.settings, self.backend)
        self.client = TestClient(self.app, base_url='https://mcp.lynse.cn')
        self.client.__enter__()
        self.web = TestClient(self.app, base_url=WEB)

    def tearDown(self):
        self.web.close()
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def register(self, scopes='lynse:read', redirect='http://127.0.0.1:9876/callback'):
        r = self.client.post('/register', json={
            'client_name': 'Test Client', 'redirect_uris': [redirect], 'scope': scopes,
            'token_endpoint_auth_method': 'none', 'grant_types': ['authorization_code', 'refresh_token'],
        })
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def start(self, client=None, scopes='lynse:read', web=None):
        client = client or self.register(scopes)
        verifier = 'a' * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        params = {'response_type': 'code', 'client_id': client['client_id'],
                  'redirect_uri': client['redirect_uris'][0], 'code_challenge': challenge,
                  'code_challenge_method': 'S256', 'scope': scopes, 'state': 'client-state', 'resource': RESOURCE}
        response = self.client.get('/authorize', params=params, follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.text)
        ticket = parse_qs(urlsplit(response.headers['location']).query)['ticket'][0]
        browser = web or self.web
        context = browser.get(API_PATH + '/context', params={'ticket': ticket})
        self.assertEqual(context.status_code, 200, context.text)
        return {'client': client, 'ticket': ticket, 'csrf': context.json()['csrf'], 'verifier': verifier,
                'redirect': client['redirect_uris'][0], 'web': browser}

    def action(self, flow, action, **body):
        return flow['web'].post(API_PATH + '/' + action,
            headers={'Origin': WEB, 'X-MCP-CSRF': flow['csrf']},
            json={'ticket': flow['ticket'], **body})

    def grant(self, phone='13900000001', scopes='lynse:read', web=None, client=None):
        flow = self.start(client, scopes, web)
        self.assertEqual(self.action(flow, 'phone-login', phone=phone, code='123456', user_id='attacker').status_code, 200)
        r = self.action(flow, 'consent', decision='allow')
        self.assertEqual(r.status_code, 200, r.text)
        query = parse_qs(urlsplit(r.json()['redirect_uri']).query)
        self.assertEqual(query['state'], ['client-state'])
        self.assertEqual(query['iss'], [self.app.state.oauth_provider.issuer])
        flow['code'] = query['code'][0]
        return flow

    def exchange(self, flow, **changes):
        form = {'client_id': flow['client']['client_id'], 'grant_type': 'authorization_code',
                'code': flow['code'], 'code_verifier': flow['verifier'],
                'redirect_uri': flow['redirect'], 'resource': RESOURCE}
        return self.client.post('/token', data=form | changes)

    def rpc(self, token, method, params=None, session=None, request_id=1):
        headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json, text/event-stream'}
        if session:
            headers['Mcp-Session-Id'] = session
            headers['MCP-Protocol-Version'] = '2025-11-25'
        return self.client.post('/mcp', headers=headers,
            json={'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params or {}})

    def initialize(self, token):
        response = self.rpc(token, 'initialize', {'protocolVersion': '2025-11-25',
            'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.headers['Mcp-Session-Id']

    def test_discovery_and_missing_or_foreign_token_fail_closed(self):
        discovery = self.client.get('/.well-known/oauth-authorization-server').json()
        self.assertIn('none', discovery['token_endpoint_auth_methods_supported'])
        self.assertTrue(discovery['authorization_response_iss_parameter_supported'])
        resource = self.client.get('/.well-known/oauth-protected-resource/mcp').json()
        self.assertEqual(discovery['issuer'], self.app.state.oauth_provider.issuer)
        self.assertEqual(resource['authorization_servers'], [discovery['issuer']])
        self.assertEqual(self.client.options('/.well-known/oauth-authorization-server',
            headers={'Origin': 'https://client.test', 'Access-Control-Request-Method': 'GET'}).status_code, 200)
        r = self.client.post('/mcp', json={})
        self.assertEqual(r.status_code, 401)
        self.assertIn('/.well-known/oauth-protected-resource/mcp', r.headers['www-authenticate'])
        self.assertEqual(self.rpc('a-lynse-login-token', 'tools/list').status_code, 401)

    def test_remote_profile_filters_secrets_and_write_grant_can_return_meetings_to_unfiled(self):
        flow = self.grant(scopes='lynse:read lynse:write')
        token = self.exchange(flow).json()['access_token']
        session = self.initialize(token)
        profile = {'code': 200, 'data': {'id': 'verified-account', 'nickname': 'Test account',
                   'password': 'private-password-hash', 'apiKey': 'private-api-key'}}
        with patch.object(AccountLynseAPI, 'get_current_customer', return_value=profile):
            response = self.rpc(token, 'tools/call', {'name': 'get_current_user', 'arguments': {}}, session)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('private-', response.text)
        result = response.json()['result']
        self.assertFalse(result.get('isError', False))
        self.assertEqual(result['structuredContent']['data'], {'id': 'verified-account', 'nickname': 'Test account'})
        with patch.object(AccountLynseAPI, 'change_folder', return_value={'code': 200}) as move:
            response = self.rpc(token, 'tools/call', {'name': 'move_meetings', 'arguments': {
                'file_ids': ['meeting-1'], 'new_folder_id': '', 'old_folder_id': 'folder-1', 'confirm': True}}, session)
        self.assertFalse(response.json()['result'].get('isError', False))
        move.assert_called_once_with({'oldFolderId': 'folder-1', 'newFolderId': '', 'fileIds': ['meeting-1']})

    def test_pkce_resource_redirect_and_code_replay(self):
        flow = self.grant()
        self.assertEqual(self.exchange(flow, code_verifier='b' * 64).json()['error'], 'invalid_grant')
        self.assertEqual(self.exchange(flow, resource='https://other.test/mcp').json()['error'], 'invalid_target')
        self.assertEqual(self.exchange(flow, redirect_uri='https://attacker.test').json()['error'], 'invalid_request')
        r = self.exchange(flow)
        self.assertEqual(r.status_code, 200, r.text)
        first = r.json()
        token = first['access_token']
        # A duplicate callback for the same code replays the identical token
        # bundle: no new token is minted, and the client keeps its credentials.
        replay = self.exchange(flow)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json(), first)
        access = asyncio.run(self.app.state.oauth_provider.verify_token(token))
        self.assertEqual(access.subject, '13900000001')
        self.assertNotIn('private-upstream', r.text)
        self.assertNotIn(b'private-upstream', self.db.read_bytes())
        self.assertNotIn(token.encode(), self.db.read_bytes())

    def test_code_replay_is_client_scoped_and_revocation_still_fails_closed(self):
        flow = self.grant()
        first = self.exchange(flow)
        self.assertEqual(first.status_code, 200, first.text)
        token, refresh = first.json()['access_token'], first.json()['refresh_token']

        # Idempotency never crosses clients: a different registered client cannot
        # replay a code that belongs to this one.
        foreign = self.register(redirect='http://127.0.0.1:9877/callback')
        bad = self.exchange(flow, client_id=foreign['client_id'])
        self.assertEqual(bad.json()['error'], 'invalid_grant', bad.text)

        # Revoking the grant kills the cached replay and the live token.
        revoke = self.client.post('/revoke', data={'client_id': flow['client']['client_id'],
                                                   'token': refresh})
        self.assertEqual(revoke.status_code, 200, revoke.text)
        denied = self.exchange(flow)
        self.assertEqual(denied.json()['error'], 'invalid_grant', denied.text)
        self.assertIsNone(asyncio.run(self.app.state.oauth_provider.verify_token(token)))

    def test_browser_binding_csrf_and_denial(self):
        flow = self.start()
        attacker = TestClient(self.app, base_url=WEB)
        self.assertEqual(attacker.get(API_PATH + '/context', params={'ticket': flow['ticket']}).status_code, 400)
        attacker.close()
        r = self.web.post(API_PATH + '/phone-login', json={'ticket': flow['ticket'], 'phone': '13900000001', 'code': '123456'})
        self.assertEqual(r.status_code, 403)
        r = self.action(flow, 'consent', decision='deny')
        self.assertEqual(parse_qs(urlsplit(r.json()['redirect_uri']).query)['error'], ['access_denied'])
        self.assertEqual(self.action(flow, 'consent', decision='deny').status_code, 400)

    def test_resource_errors_explain_expected_target_without_consuming_tokens(self):
        client = self.register()
        params = {'response_type': 'code', 'client_id': client['client_id'],
                  'redirect_uri': client['redirect_uris'][0], 'code_challenge': 'a' * 43,
                  'code_challenge_method': 'S256', 'scope': 'lynse:read', 'state': 'keep'}
        description = f'resource is required and must equal {RESOURCE}'
        for resource in (None, '', 'https://other.test/mcp'):
            with self.subTest(endpoint='authorize', resource=resource):
                request = params if resource is None else params | {'resource': resource}
                response = self.client.get('/authorize', params=request, follow_redirects=False)
                self.assertEqual(response.status_code, 302, response.text)
                query = parse_qs(urlsplit(response.headers['location']).query)
                self.assertEqual(query['error'], ['invalid_target'])
                self.assertEqual(query['error_description'], [description])
                self.assertEqual(query['state'], ['keep'])
                self.assertEqual(query['iss'], [self.app.state.oauth_provider.issuer])

        flow = self.grant(client=client)
        code_form = {'client_id': client['client_id'], 'grant_type': 'authorization_code',
                     'code': flow['code'], 'code_verifier': flow['verifier'], 'redirect_uri': flow['redirect']}
        for grant_type in ('authorization_code', 'refresh_token'):
            for resource in (None, '', 'https://other.test/mcp', [RESOURCE, RESOURCE]):
                with self.subTest(grant_type=grant_type, resource=resource):
                    request = code_form if resource is None else code_form | {'resource': resource}
                    response = self.client.post('/token', data=request)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertEqual(response.json(), {'error': 'invalid_target', 'error_description': description})
            response = self.client.post('/token', data=code_form | {'resource': RESOURCE})
            self.assertEqual(response.status_code, 200, response.text)
            code_form = {'client_id': client['client_id'], 'grant_type': 'refresh_token',
                         'refresh_token': response.json()['refresh_token']}

    def test_refresh_after_access_expiry_preserves_same_account_session(self):
        flow = self.grant()
        tokens = self.exchange(flow).json()
        session = self.initialize(tokens['access_token'])
        with patch('time.time', return_value=time.time() + 901):
            self.assertEqual(self.rpc(tokens['access_token'], 'tools/list', session=session).status_code, 401)
            refreshed = self.client.post('/token', data={
                'client_id': flow['client']['client_id'], 'grant_type': 'refresh_token',
                'refresh_token': tokens['refresh_token'], 'resource': RESOURCE})
            self.assertEqual(refreshed.status_code, 200, refreshed.text)
            access = refreshed.json()['access_token']
            self.assertEqual(self.rpc(access, 'tools/list', session=session).status_code, 200)
            owners = []
            def files(api, days):
                owners.append(api.owner_id)
                return {'code': 200, 'data': [{'id': 'meeting-after-refresh'}]}
            with patch.object(AccountLynseAPI, 'list_files_by_time_range', files):
                result = self.rpc(access, 'tools/call',
                    {'name': 'list_meetings', 'arguments': {'days': 3}}, session)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertFalse(result.json()['result'].get('isError', False))
            self.assertIn('meeting-after-refresh', result.text)
            self.assertEqual(owners, ['13900000001'])

    def test_refresh_slides_idle_window_within_absolute_grant_expiry(self):
        flow = self.grant()
        tokens = self.exchange(flow).json()
        provider = self.app.state.oauth_provider
        access = asyncio.run(provider.verify_token(tokens['access_token']))
        grant_id = access.claims['grant_id']
        grant = provider.store.get('grant', grant_id)
        # The grant decouples from the 24h login session; the upstream credential does not.
        self.assertGreater(grant['expires'], time.time() + 89 * 86400)
        self.assertLess(grant['upstream_expires'], time.time() + 86400)
        form = {'client_id': flow['client']['client_id'], 'grant_type': 'refresh_token',
                'refresh_token': tokens['refresh_token'], 'resource': RESOURCE}
        # Refresh works long after the login session died and keeps sliding the
        # idle window; each stop stays inside the previous window (30 days idle).
        def slide(at, token, days_out=None):
            with patch('time.time', return_value=at):
                step = self.client.post('/token', data=form | {'refresh_token': token})
                self.assertEqual(step.status_code, 200, step.text)
                step = step.json()
                self.assertEqual(provider.store.get('grant', grant_id)['expires'], grant['expires'])
                if days_out is not None:
                    expected = int(min(at + days_out * 86400, grant['expires']))
                    self.assertEqual(provider.store.get('refresh',
                        fingerprint(step['refresh_token']))['expires_at'], expected)
                return step
        start = time.time()
        chained = slide(start + 25 * 3600, tokens['refresh_token'], 30)
        chained = slide(start + 25 * 86400, chained['refresh_token'])
        chained = slide(start + 50 * 86400, chained['refresh_token'])
        chained = slide(start + 75 * 86400, chained['refresh_token'])
        # Near the absolute expiry the idle window is capped and refresh stops after it.
        capped = slide(grant['expires'] - 60, chained['refresh_token'])
        self.assertEqual(capped['expires_in'], 60)
        self.assertEqual(provider.store.get('refresh', fingerprint(capped['refresh_token']))['expires_at'], int(grant['expires']))
        with patch('time.time', return_value=grant['expires'] + 1):
            response = self.client.post('/token', data=form | {'refresh_token': capped['refresh_token']})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()['error'], 'invalid_grant')
            self.assertEqual(self.rpc(capped['access_token'], 'tools/list').status_code, 401)

    def test_refresh_replay_revokes_grant_family(self):
        flow = self.grant()
        tokens = self.exchange(flow).json()
        form = {'client_id': flow['client']['client_id'], 'grant_type': 'refresh_token',
                'refresh_token': tokens['refresh_token'], 'resource': RESOURCE}
        second = self.client.post('/token', data=form)
        self.assertEqual(second.status_code, 200, second.text)
        second = second.json()
        provider = self.app.state.oauth_provider
        grant_id = asyncio.run(provider.verify_token(second['access_token'])).claims['grant_id']
        # Replaying the consumed first-generation token revokes the whole family.
        replay = self.client.post('/token', data=form)
        self.assertEqual(replay.status_code, 400, replay.text)
        self.assertEqual(replay.json()['error'], 'invalid_grant')
        self.assertEqual(provider.store.get('authorization', grant_id)['status'], 'revoked')
        self.assertIsNone(asyncio.run(provider.verify_token(second['access_token'])))
        self.assertEqual(self.client.post('/token', data=form | {'refresh_token': second['refresh_token']}).json()['error'], 'invalid_grant')
        # A forged unknown token is a plain rejection and does not revoke anything else.
        forged = self.client.post('/token', data={'client_id': flow['client']['client_id'],
            'grant_type': 'refresh_token', 'refresh_token': 'z' * 107 + '.', 'resource': RESOURCE})
        self.assertEqual(forged.json()['error'], 'invalid_grant')

    def test_legacy_grants_without_new_fields_still_validate(self):
        flow = self.grant()
        token = self.exchange(flow).json()['access_token']
        provider = self.app.state.oauth_provider
        grant_id = asyncio.run(provider.verify_token(token)).claims['grant_id']
        with provider.store.transaction() as db:
            legacy = provider.store.read(db, 'grant', grant_id)
            legacy.pop('idle_expires', None)
            legacy.pop('upstream_expires', None)
            provider.store.write(db, 'grant', grant_id, legacy, legacy['expires'])
        self.assertIsNotNone(asyncio.run(provider.verify_token(token)))

    def test_website_login_refreshes_upstream_credentials(self):
        flow = self.grant()
        other = TestClient(self.app, base_url=WEB)
        foreign = self.grant('13900000002', web=other, client=flow['client'])
        provider = self.app.state.oauth_provider
        tokens = self.exchange(flow).json()
        grant_id = asyncio.run(provider.verify_token(tokens['access_token'])).claims['grant_id']
        foreign_id = asyncio.run(provider.verify_token(
            self.exchange(foreign).json()['access_token'])).claims['grant_id']
        foreign_upstream = provider.store.get('grant', foreign_id)['upstream_expires']
        session = self.initialize(tokens['access_token'])
        with patch('time.time', return_value=time.time() + 25 * 3600):
            refreshed = self.client.post('/token', data={
                'client_id': flow['client']['client_id'], 'grant_type': 'refresh_token',
                'refresh_token': tokens['refresh_token'], 'resource': RESOURCE})
            self.assertEqual(refreshed.status_code, 200, refreshed.text)
            access = refreshed.json()['access_token']
            # The stale upstream credential explains itself without leaking it.
            with patch.object(AccountLynseAPI, 'list_files_by_time_range') as calls:
                stale = self.rpc(access, 'tools/call', {'name': 'list_meetings', 'arguments': {'days': 3}}, session)
            self.assertTrue(stale.json()['result'].get('isError'))
            self.assertIn('sign in again on the website', stale.text)
            calls.assert_not_called()
            # A fresh website login hands the account's grants new credentials.
            console = TestClient(self.app, base_url=WEB)
            csrf = console.get(API_PATH + '/authorizations').json()['csrf']
            self.assertEqual(console.post(API_PATH + '/phone-login', json={'phone': '13900000001', 'code': '123456'},
                headers={'Origin': WEB, 'X-MCP-CSRF': csrf}).status_code, 200)
            grant = provider.store.get('grant', grant_id)
            self.assertGreater(grant['upstream_expires'], time.time() + 23 * 3600)
            # Only this account's grants are refreshed.
            self.assertEqual(provider.store.get('grant', foreign_id)['upstream_expires'], foreign_upstream)
            with patch.object(AccountLynseAPI, 'list_files_by_time_range', return_value={'code': 200, 'data': []}):
                result = self.rpc(access, 'tools/call', {'name': 'list_meetings', 'arguments': {'days': 3}}, session)
            self.assertFalse(result.json()['result'].get('isError', False))
            console.close()
        other.close()

    def test_account_login_lifetime_respects_upstream_and_local_cap(self):
        backend = AccountBackend('https://api.lynse.cn')
        for upstream, expected in ((3600, 3600), (30 * 86400, 86400)):
            with self.subTest(expireIn=upstream), patch.object(backend, 'request', side_effect=[
                    {'token': 'fake-login', 'expireIn': upstream}, {'id': 'verified'}]):
                self.assertEqual(backend.phone_login('13900000001', '123456')['lifetime'], expected)

    def test_refresh_rotation_scope_and_revocation(self):
        flow = self.grant(scopes='lynse:read lynse:write')
        tokens = self.exchange(flow).json()
        form = {'client_id': flow['client']['client_id'], 'grant_type': 'refresh_token',
                'refresh_token': tokens['refresh_token'], 'resource': RESOURCE}
        refreshed = self.client.post('/token', data=form | {'scope': 'lynse:read'})
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(refreshed.json()['scope'], 'lynse:read')
        self.assertEqual(self.client.post('/token', data=form).json()['error'], 'invalid_grant')
        revoke = self.client.post('/revoke', data={'client_id': flow['client']['client_id'], 'token': refreshed.json()['refresh_token']})
        self.assertEqual(revoke.status_code, 200, revoke.text)
        self.assertIsNone(asyncio.run(self.app.state.oauth_provider.verify_token(tokens['access_token'])))

    def test_client_binding_and_expired_grant(self):
        flow = self.grant()
        other = self.register()
        self.assertEqual(self.exchange(flow, client_id=other['client_id']).json()['error'], 'invalid_grant')
        token = self.exchange(flow).json()['access_token']
        provider = self.app.state.oauth_provider
        access = asyncio.run(provider.verify_token(token))
        with provider.store.transaction() as db:
            db.execute('UPDATE records SET expires=0 WHERE kind=? AND id=?', ('grant', access.claims['grant_id']))
        self.assertIsNone(asyncio.run(provider.verify_token(token)))

    def test_account_isolation_tools_and_write_scope(self):
        first = self.grant()
        token_a = self.exchange(first).json()['access_token']
        browser_b = TestClient(self.app, base_url=WEB)
        second = self.grant('13900000002', web=browser_b, client=first['client'])
        token_b = self.exchange(second).json()['access_token']
        session_a = self.initialize(token_a)
        session_b = self.initialize(token_b)
        seen = []
        def files(api, days):
            seen.append(api.owner_id)
            return {'code': 200, 'data': [{'id': 'meeting-' + api.owner_id}]}
        with patch.object(AccountLynseAPI, 'list_files_by_time_range', files):
            for name in ('list_meetings', 'show_meetings', 'read_meetings_view'):
                for token, session, user in [(token_a, session_a, '13900000001'), (token_b, session_b, '13900000002')]:
                    response = self.rpc(token, 'tools/call', {'name': name, 'arguments': {'days': 3}}, session)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIn('meeting-' + user, response.text)
            stolen = self.rpc(token_b, 'tools/list', session=session_a)
            self.assertEqual(stolen.status_code, 404, stolen.text)
        self.assertEqual(seen, ['13900000001', '13900000002'] * 3)
        tools = self.rpc(token_a, 'tools/list', session=session_a).json()['result']['tools']
        self.assertNotIn('reset_lynse_key', [tool['name'] for tool in tools])
        write = self.rpc(token_a, 'tools/call', {'name': 'delete_todos', 'arguments': {'todo_ids': ['test'], 'confirm': True}}, session_a)
        self.assertTrue(write.json()['result']['isError'])
        self.assertIn('read permission only', write.text)
        browser_b.close()

    def test_token_login_bootstraps_console_session(self):
        flow = self.grant()
        tokens = self.exchange(flow).json()
        provider = self.app.state.oauth_provider
        grant_id = asyncio.run(provider.verify_token(tokens['access_token'])).claims['grant_id']
        browser = TestClient(self.app, base_url=WEB)
        data = browser.get(API_PATH + '/authorizations').json()
        self.assertIsNone(data['user'])
        csrf = data['csrf']
        token = 'private-upstream-13900000001-9'
        # Origin and CSRF guard the exchange like every ticketless POST.
        self.assertEqual(browser.post(API_PATH + '/token-login', json={'token': token}).status_code, 403)
        self.assertEqual(browser.post(API_PATH + '/token-login', json={'token': token},
            headers={'Origin': WEB, 'X-MCP-CSRF': 'forged'}).status_code, 403)
        self.assertEqual(browser.post(API_PATH + '/token-login', json={'token': ''},
            headers={'Origin': WEB, 'X-MCP-CSRF': csrf}).status_code, 400)
        # An account the backend cannot verify is rejected without detail leaks.
        with patch.object(self.backend, 'current_user',
                          side_effect=AccountError('无法验证当前账号，请重新登录。')):
            stale = browser.post(API_PATH + '/token-login', json={'token': 'stale-token'},
                headers={'Origin': WEB, 'X-MCP-CSRF': csrf})
        self.assertEqual(stale.status_code, 400, stale.text)
        self.assertEqual(stale.json()['error'], '无法验证当前账号，请重新登录。')
        # A verified token opens the console session and refreshes grant credentials.
        granted = browser.post(API_PATH + '/token-login', json={'token': token},
            headers={'Origin': WEB, 'X-MCP-CSRF': csrf})
        self.assertEqual(granted.status_code, 200, granted.text)
        self.assertEqual(granted.json(), {'user': {'name': 'User 0001'}})
        listed = browser.get(API_PATH + '/authorizations').json()
        self.assertEqual([item['authorization_id'] for item in listed['authorizations']], [grant_id])
        grant = provider.store.get('grant', grant_id)
        self.assertEqual(grant['upstream_token'], token)
        self.assertGreater(grant['upstream_expires'], time.time() + 23 * 3600)
        # The upstream token never reaches the database in plaintext.
        self.assertNotIn(token.encode(), self.db.read_bytes())
        browser.close()

    def test_wechat_state_is_bound_and_single_use(self):
        flow = self.start()
        started = self.action(flow, 'wechat-start')
        self.assertEqual(started.status_code, 200, started.text)
        query = parse_qs(urlsplit(started.json()['url']).query)
        self.assertEqual(query['redirect_uri'], [WEB + '/platform/oauth/wechat'])
        self.assertEqual(self.action(flow, 'wechat-complete', state='forged', code='x').status_code, 400)
        self.assertEqual(self.action(flow, 'wechat-complete', state='wechat-state', code='x').status_code, 200)
        self.assertEqual(self.action(flow, 'wechat-complete', state='wechat-state', code='x').status_code, 400)

    def test_sms_limits_and_existing_account_contract(self):
        flow = self.start()
        self.assertEqual(self.action(flow, 'sms', phone='13900000001').status_code, 200)
        self.assertEqual(self.action(flow, 'sms', phone='13900000001').status_code, 429)
        self.assertEqual(self.backend.calls, [('sms', '13900000001')])
        backend = AccountBackend('https://api.lynse.cn')
        with patch.object(backend, 'request', side_effect=[{'token': 'backend-login', 'customerId': 'spoof', 'expireIn': 1000}, {'id': 'verified'}]) as request:
            result = backend.phone_login('13900000001', '123456')
            self.assertEqual(result['subject'], 'verified')
            self.assertEqual(request.call_args_list[0].kwargs['json']['authType'], 'PHONE_CODE')

    def test_remote_api_never_inherits_machine_credentials(self):
        with patch.dict('os.environ', {'LYNSE_API_KEY': 'shared-key', 'LYNSE_ACCESS_TOKEN': 'shared-token'}):
            api = AccountLynseAPI('https://api.lynse.cn', 'explicit-token', 'user-1')
            self.assertEqual(api._get_token(), 'explicit-token')
            self.assertEqual(api._resolve_x_api_key('explicit-token'), '')
            with self.assertRaises(Exception):
                api._get_token(refresh=True)

    def test_storage_survives_restart(self):
        client = self.register()
        restarted = create_app(self.settings, FakeBackend()).state.oauth_provider
        restored = asyncio.run(restarted.get_client(client['client_id']))
        self.assertEqual(restored.client_name, 'Test Client')

    def test_distinct_clients_share_authorization_and_match_own_callbacks(self):
        for redirect in ['https://chatgpt.com/connector_platform_oauth_redirect',
                         'workbuddy://workbuddy/mcp/connector%3Alynse/oauth/callback',
                         'https://example-client.test/oauth/callback']:
            with self.subTest(redirect=redirect):
                client = self.register(redirect=redirect)
                flow = self.grant(client=client)
                self.assertEqual(flow['redirect'], redirect)
                self.assertEqual(self.exchange(flow).status_code, 200)
        invalid = self.client.post('/register', json={'redirect_uris': ['javascript:alert(1)'],
            'token_endpoint_auth_method': 'none'})
        self.assertEqual(invalid.status_code, 400)

    def test_error_redirects_include_issuer_and_scope_cannot_escalate(self):
        flow = self.start()
        r = self.client.get('/authorize', params={'response_type': 'code',
            'client_id': flow['client']['client_id'], 'redirect_uri': flow['redirect'],
            'code_challenge': 'a' * 43, 'code_challenge_method': 'S256',
            'resource': 'https://other.test/mcp', 'state': 'keep'}, follow_redirects=False)
        self.assertEqual(r.status_code, 302, r.text)
        query = parse_qs(urlsplit(r.headers['location']).query)
        self.assertEqual(query['iss'], [self.app.state.oauth_provider.issuer])
        self.assertEqual(query['state'], ['keep'])
        self.assertEqual(query['error'], ['invalid_target'])
        flow = self.grant(client=flow['client'])
        tokens = self.exchange(flow).json()
        r = self.client.post('/token', data={'grant_type': 'refresh_token',
            'client_id': flow['client']['client_id'], 'refresh_token': tokens['refresh_token'],
            'scope': 'lynse:read lynse:write', 'resource': RESOURCE})
        self.assertEqual(r.status_code, 400)

    def test_write_grant_still_requires_confirmation(self):
        flow = self.grant(scopes='lynse:read lynse:write')
        token = self.exchange(flow).json()['access_token']
        session = self.initialize(token)
        with patch.object(AccountLynseAPI, 'delete_todos', return_value={'code': 200}) as deletion:
            r = self.rpc(token, 'tools/call', {'name': 'delete_todos',
                'arguments': {'todo_ids': ['x'], 'confirm': False}}, session)
            self.assertTrue(r.json()['result']['isError'])
            deletion.assert_not_called()
            r = self.rpc(token, 'tools/call', {'name': 'delete_todos',
                'arguments': {'todo_ids': ['x'], 'confirm': True}}, session)
            self.assertFalse(r.json()['result'].get('isError', False))
            deletion.assert_called_once_with(['x'])

    def test_actual_api_headers_and_owner_check_fail_closed(self):
        api = AccountLynseAPI('https://api.lynse.cn', 'private-account-token', 'verified-user')
        owner = Mock(status_code=200)
        owner.json.return_value = {'code': 200, 'data': {'id': 'different-user'}}
        with patch('lynse_account.requests.get', return_value=owner), patch('lynse_account.requests.request') as data:
            with self.assertRaises(Exception):
                api._request('GET', '/api/test')
            data.assert_not_called()
        owner.json.return_value = {'code': 200, 'data': {'id': 'verified-user'}}
        result = Mock(status_code=200, text='')
        result.json.return_value = {'code': 200, 'data': []}
        with patch('lynse_account.requests.get', return_value=owner), patch('lynse_account.requests.request', return_value=result) as data:
            api._request('GET', '/api/test')
            headers = data.call_args.kwargs['headers']
            self.assertEqual(headers['Authorization'], 'private-account-token')
            self.assertNotIn('X-API-Key', headers)

    def test_confidential_client_revocation_requires_secret_and_owns_grant(self):
        flow = self.grant()
        tokens = self.exchange(flow).json()
        other = self.client.post('/register', json={'redirect_uris': ['https://client.test/callback'],
            'token_endpoint_auth_method': 'client_secret_post'}).json()
        form = {'client_id': other['client_id'], 'token': tokens['access_token']}
        self.assertEqual(self.client.post('/revoke', data=form).status_code, 401)
        self.assertEqual(self.client.post('/revoke', data=form | {'client_secret': other['client_secret']}).status_code, 200)
        self.assertIsNotNone(asyncio.run(self.app.state.oauth_provider.verify_token(tokens['access_token'])))

    def test_upstream_errors_do_not_disclose_credentials(self):
        flow = self.grant()
        token = self.exchange(flow).json()['access_token']
        session = self.initialize(token)
        with patch.object(AccountLynseAPI, 'list_files_by_time_range',
                          side_effect=LynseAPIError('secret-account-token', http_code=500)):
            r = self.rpc(token, 'tools/call', {'name': 'list_meetings', 'arguments': {}}, session)
            self.assertTrue(r.json()['result']['isError'])
            self.assertNotIn('secret-account-token', r.text)

    def test_public_test_client_callback_and_cleanup(self):
        started = self.web.get('/platform/oauth/test/start', follow_redirects=False)
        self.assertEqual(started.status_code, 302, started.text)
        params = {key: value[0] for key, value in parse_qs(urlsplit(started.headers['location']).query).items()}
        authorized = self.client.get('/authorize', params=params, follow_redirects=False)
        self.assertEqual(authorized.status_code, 302)
        ticket = parse_qs(urlsplit(authorized.headers['location']).query)['ticket'][0]
        context = self.web.get(API_PATH + '/context', params={'ticket': ticket}).json()
        flow = {'web': self.web, 'ticket': ticket, 'csrf': context['csrf']}
        self.assertEqual(self.action(flow, 'phone-login', phone='13900000001', code='123456').status_code, 200)
        consent = self.action(flow, 'consent', decision='allow').json()
        callback = urlsplit(consent['redirect_uri'])
        stolen = TestClient(self.app, base_url=WEB)
        self.assertEqual(stolen.get(callback.path + '?' + callback.query).status_code, 400)
        stolen.close()
        issued = []
        def forward(method, path, **kwargs):
            response = self.client.request(method, path, **kwargs)
            self.assertLess(response.status_code, 400, response.text)
            if path == '/token':
                issued.append(response.json()['access_token'])
            return response
        with patch('lynse_test_client.TestOAuthClient._request', side_effect=forward):
            result = self.web.get(callback.path + '?' + callback.query)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn('授权测试成功', result.text)
        self.assertNotIn('private-upstream', result.text)
        self.assertNotIn(issued[0], result.text)
        self.assertIsNone(asyncio.run(self.app.state.oauth_provider.verify_token(issued[0])))
        self.assertEqual(self.web.get(callback.path + '?' + callback.query).status_code, 400)

    def test_public_test_ui_is_optional(self):
        disabled = TestClient(create_app(replace(self.settings, test_ui=False), self.backend), base_url=WEB)
        self.assertEqual(disabled.get('/platform/oauth/test').status_code, 404)
        disabled.close()

    def console_browser(self, phone):
        """Ticketless console login on a fresh browser; returns (client, csrf)."""
        browser = TestClient(self.app, base_url=WEB)
        data = browser.get(API_PATH + '/authorizations').json()
        self.assertIsNone(data['user'])
        self.assertEqual(data['authorizations'], [])
        csrf = data['csrf']
        login = browser.post(API_PATH + '/phone-login', json={'phone': phone, 'code': '123456'},
                             headers={'Origin': WEB, 'X-MCP-CSRF': csrf})
        self.assertEqual(login.status_code, 200, login.text)
        return browser, browser.get(API_PATH + '/authorizations').json()['csrf']

    def test_authorization_records_list_revoke_and_isolation(self):
        flow = self.grant()
        tokens = self.exchange(flow).json()
        console, csrf = self.console_browser('13900000001')
        listed = console.get(API_PATH + '/authorizations').json()
        self.assertEqual(listed['user']['name'], 'User 0001')
        record = listed['authorizations'][0]
        self.assertEqual((record['client_name'], record['client_id'], record['status'],
                          record['grant_type'], record['scopes']), ('Test Client', flow['client']['client_id'],
                          'valid', 'oauth', ['lynse:read']))
        self.assertNotIn('private-upstream', console.get(API_PATH + '/authorizations').text)
        # Ticketless login still enforces origin and browser CSRF.
        for headers in ({'Origin': WEB, 'X-MCP-CSRF': 'forged'}, {'Origin': 'https://evil.test', 'X-MCP-CSRF': 'forged'}):
            body = {'phone': '13900000002', 'code': '123456'}
            self.assertEqual(console.post(API_PATH + '/phone-login', json=body, headers=headers).status_code, 403)
        # Another account sees no records and cannot revoke or delete foreign ones.
        other, other_csrf = self.console_browser('13900000002')
        self.assertEqual(other.get(API_PATH + '/authorizations').json()['authorizations'], [])
        for action in ('authorization-revoke', 'authorization-delete'):
            self.assertEqual(other.post(API_PATH + '/' + action, json={'authorization_id': record['authorization_id']},
                headers={'Origin': WEB, 'X-MCP-CSRF': other_csrf}).status_code, 400)
        # Revoking requires the console browser CSRF and ends every token of the grant.
        self.assertEqual(console.post(API_PATH + '/authorization-revoke',
            json={'authorization_id': record['authorization_id']},
            headers={'Origin': WEB, 'X-MCP-CSRF': 'forged'}).status_code, 403)
        revoke = console.post(API_PATH + '/authorization-revoke',
            json={'authorization_id': record['authorization_id']},
            headers={'Origin': WEB, 'X-MCP-CSRF': csrf})
        self.assertEqual(revoke.status_code, 200, revoke.text)
        self.assertIsNone(asyncio.run(self.app.state.oauth_provider.verify_token(tokens['access_token'])))
        refreshed = self.client.post('/token', data={'client_id': flow['client']['client_id'],
            'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'resource': RESOURCE})
        self.assertEqual(refreshed.json()['error'], 'invalid_grant')
        listed = console.get(API_PATH + '/authorizations').json()['authorizations']
        self.assertEqual(listed[0]['status'], 'revoked')
        self.assertEqual(console.post(API_PATH + '/authorization-revoke',
            json={'authorization_id': record['authorization_id']},
            headers={'Origin': WEB, 'X-MCP-CSRF': csrf}).status_code, 400)
        # A revoked record can be deleted and then disappears from the list.
        self.assertEqual(console.post(API_PATH + '/authorization-delete',
            json={'authorization_id': record['authorization_id']},
            headers={'Origin': WEB, 'X-MCP-CSRF': csrf}).status_code, 200)
        self.assertEqual(console.get(API_PATH + '/authorizations').json()['authorizations'], [])
        console.close()
        other.close()

    def test_rfc7009_revocation_marks_record_and_expiry_delete(self):
        flow = self.grant()
        tokens = self.exchange(flow).json()
        provider = self.app.state.oauth_provider
        grant_id = asyncio.run(provider.verify_token(tokens['access_token'])).claims['grant_id']
        self.assertEqual(self.client.post('/revoke', data={'client_id': flow['client']['client_id'],
            'token': tokens['refresh_token']}).status_code, 200)
        # self.web is already logged in from the consent flow.
        data = self.web.get(API_PATH + '/authorizations').json()
        self.assertEqual(data['user']['name'], 'User 0001')
        record = data['authorizations'][0]
        self.assertEqual(record['status'], 'revoked')
        self.assertEqual(record['authorization_id'], grant_id)

    def test_expired_authorization_shows_status_and_allows_delete(self):
        flow = self.grant()
        tokens = self.exchange(flow).json()
        provider = self.app.state.oauth_provider
        grant_id = asyncio.run(provider.verify_token(tokens['access_token'])).claims['grant_id']
        grant_expires = provider.store.get('authorization', grant_id)['grant_expires']
        # The login session is gone by the time the record expires; re-login on the console.
        with patch('time.time', return_value=grant_expires + 1):
            console, csrf = self.console_browser('13900000001')
            data = console.get(API_PATH + '/authorizations').json()
            self.assertEqual(data['authorizations'][0]['status'], 'expired')
            self.assertEqual(console.post(API_PATH + '/authorization-delete',
                json={'authorization_id': grant_id},
                headers={'Origin': WEB, 'X-MCP-CSRF': csrf}).status_code, 200)
            console.close()
        self.assertEqual(self.web.get(API_PATH + '/authorizations').json()['authorizations'], [])
        # a still-valid record cannot be deleted directly (fresh client: the
        # original 90-day DCR record was cleaned up in the expired timeline)
        flow = self.grant()
        tokens = self.exchange(flow).json()
        grant_id = asyncio.run(provider.verify_token(tokens['access_token'])).claims['grant_id']
        csrf = self.web.get(API_PATH + '/authorizations').json()['csrf']
        self.assertEqual(self.web.post(API_PATH + '/authorization-delete',
            json={'authorization_id': grant_id},
            headers={'Origin': WEB, 'X-MCP-CSRF': csrf}).status_code, 400)

    def test_console_requires_login_for_record_actions(self):
        anonymous = TestClient(self.app, base_url=WEB)
        csrf = anonymous.get(API_PATH + '/authorizations').json()['csrf']
        for action in ('authorization-revoke', 'authorization-delete'):
            self.assertEqual(anonymous.post(API_PATH + '/' + action, json={'authorization_id': 'a' * 43},
                headers={'Origin': WEB, 'X-MCP-CSRF': csrf}).status_code, 400)
        # consent still requires a ticket
        self.assertEqual(anonymous.post(API_PATH + '/consent', json={'decision': 'allow'},
            headers={'Origin': WEB, 'X-MCP-CSRF': csrf}).status_code, 400)
        anonymous.close()

    def test_console_test_ui_page_lists_and_revokes(self):
        flow = self.grant()
        token = self.exchange(flow).json()['access_token']

        def forward(method, action, request, form=None, csrf=""):
            headers = {'Origin': WEB, 'X-MCP-CSRF': csrf} if csrf else {'Origin': WEB}
            if method == 'GET':
                return self.web.get(API_PATH + '/' + action, headers=headers)
            return self.web.post(API_PATH + '/' + action, json=form, headers=headers)

        with patch.object(TestOAuthClient, '_api', side_effect=forward):
            page = self.web.get('/platform/oauth/test/authorizations')
            self.assertEqual(page.status_code, 200, page.text)
            self.assertIn('当前账号', page.text)
            self.assertIn('Test Client', page.text)
            self.assertIn('撤销授权', page.text)
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
            grant_id = re.search(r'name="authorization_id" value="([^"]+)"', page.text).group(1)
            submitted = self.web.post('/platform/oauth/test/authorizations/revoke',
                                      data={'csrf': csrf, 'authorization_id': grant_id}, follow_redirects=False)
            self.assertEqual(submitted.status_code, 303, submitted.text)
            refreshed = self.web.get(submitted.headers['location'])
            self.assertIn('已撤销', refreshed.text)
            self.assertIn('已吊销', refreshed.text)
            self.assertIn('删除', refreshed.text)
        self.assertIsNone(asyncio.run(self.app.state.oauth_provider.verify_token(token)))


if __name__ == '__main__':
    unittest.main()
