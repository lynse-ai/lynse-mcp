import asyncio
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

import requests

from mcp import Client

from lynse import CLI_VERSION, LynseAPI
from lynse_mcp import LynseMCPTools, create_server


def authorized():
    return {
        "configured": True,
        "api_host": "https://api.lynse.cn",
        "credential_source": "user_config",
    }


class FakeAPI:
    def __init__(self):
        self.calls = []

    def list_files_by_time_range(self, days):
        self.calls.append(("list", days))
        return {"code": 200, "data": [{"id": "meeting-1"}]}

    def delete_todos(self, todo_ids):
        self.calls.append(("delete", todo_ids))
        return {"code": 200, "data": todo_ids}

    def change_folder(self, payload):
        self.calls.append(("move", payload))
        return {"code": 200, "data": True}

    def get_audio_file(self, file_id):
        self.calls.append(("audio", file_id))
        return {
            "code": 200,
            "data": {
                "fileId": file_id,
                "fileName": "meeting.m4a",
                "mimeType": "audio/mp4",
                "size": 1024,
                "url": "https://files.example.test/meeting.m4a?signature=short-lived",
            },
        }


class FakeAuthResult:
    configured = True
    message = "Lynse authorization completed."

    def to_dict(self):
        return {"configured": True, "message": self.message}


class FakeAuthFlow:
    elicitation_id = "test-elicitation"

    def __init__(self):
        self.started = False
        self.waited = False
        self.stopped = False

    def start(self):
        self.started = True
        return "http://127.0.0.1:54321/authorize/test"

    def wait(self):
        self.waited = True
        return FakeAuthResult()

    def stop(self):
        self.stopped = True


class FakeContext:
    class Response:
        action = "accept"

    async def elicit_url(self, **_kwargs):
        return self.Response()


class LynseMCPToolTests(unittest.TestCase):
    def test_audio_reports_missing_oss_object_without_exposing_signed_link(self):
        self._check_oss_result(404, "missing from cloud storage")

    def test_audio_reports_unavailable_oss_download(self):
        self._check_oss_result(403, "download is unavailable")

    def test_audio_checks_oss_with_one_byte_stream_and_no_account_credentials(self):
        self._check_oss_result(206)

    def test_audio_probe_hides_network_exception_details(self):
        self._check_oss_result(requests.ConnectionError("private-download-signature"), "Unable to check")

    def _check_oss_result(self, status, error=None):
        api = FakeAPI()
        result = api.get_audio_file("meeting-1")
        result["data"]["url"] = "https://recordings.oss-cn-shenzhen.aliyuncs.com/audio.m4a?private-download-signature"
        api.get_audio_file = lambda _: result
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        if isinstance(status, Exception):
            session.get.side_effect = status
        else:
            response = Mock(status_code=status)
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            session.get.return_value = response

        async def run():
            async with Client(create_server(lambda: api, authorized)) as client:
                return await client.call_tool("get_meeting_audio", {"file_id": "meeting-1"})

        with patch("lynse_mcp.requests.Session", return_value=session):
            protocol_result = asyncio.run(run())
        self.assertFalse(session.trust_env)
        session.get.assert_called_once_with(result["data"]["url"], headers={"Range": "bytes=0-0"},
                                            stream=True, allow_redirects=False, timeout=(3, 10))
        if error:
            self.assertTrue(protocol_result.is_error)
            self.assertIn(error, str(protocol_result))
            self.assertNotIn("private-download-signature", str(protocol_result))
            self.assertTrue(all(c.type != "resource_link" for c in protocol_result.content))
        else:
            self.assertFalse(protocol_result.is_error)
            self.assertEqual(protocol_result.content[0].type, "resource_link")

    def test_profile_filters_credentials_from_all_protocol_result_formats(self):
        upstream = {
            "code": 200, "msg": "SUCCESS", "token": "envelope-private-token",
            "data": {
                "id": "account-1", "nickname": "Test account", "phone": "13900000001",
                "password": "private-password-hash", "apiKey": "private-api-key",
                "accessToken": "private-access-token", "wxOpenid": "private-openid",
                "futureSecret": "private-future-secret",
                "avatarUrl": {"password": "private-nested-secret"},
            },
        }
        original = deepcopy(upstream)
        api = FakeAPI()
        api.get_current_customer = lambda: upstream

        async def run():
            async with Client(create_server(lambda: api, authorized)) as client:
                return await client.call_tool("get_current_user", {})

        result = asyncio.run(run())
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content, {
            "code": 200, "msg": "SUCCESS",
            "data": {"id": "account-1", "nickname": "Test account", "phone": "13900000001"},
        })
        self.assertNotIn("private-", str(result))
        self.assertEqual(upstream, original)

    def test_protocol_moves_to_a_folder_and_back_to_unfiled(self):
        api = FakeAPI()

        async def run():
            async with Client(create_server(lambda: api, authorized)) as client:
                moved = await client.call_tool("move_meetings", {
                    "file_ids": ["meeting-1"], "new_folder_id": "folder-1", "confirm": True,
                })
                restored = await client.call_tool("move_meetings", {
                    "file_ids": ["meeting-1"], "old_folder_id": "folder-1",
                    "new_folder_id": "", "confirm": True,
                })
                denied = await client.call_tool("move_meetings", {
                    "file_ids": ["meeting-1"], "new_folder_id": "", "confirm": False,
                })
                return moved, restored, denied

        moved, restored, denied = asyncio.run(run())
        self.assertFalse(moved.is_error)
        self.assertFalse(restored.is_error)
        self.assertTrue(denied.is_error)
        self.assertEqual(api.calls, [
            ("move", {"oldFolderId": "", "newFolderId": "folder-1", "fileIds": ["meeting-1"]}),
            ("move", {"oldFolderId": "folder-1", "newFolderId": "", "fileIds": ["meeting-1"]}),
        ])

    def test_uses_canonical_lynse_cli_1_7(self):
        self.assertGreaterEqual(
            tuple(int(part) for part in CLI_VERSION.split('.')),
            (1, 7, 0),
        )

    def test_read_tool_delegates_to_existing_api(self):
        api = FakeAPI()
        tools = LynseMCPTools(lambda: api, authorized)

        result = asyncio.run(tools.list_meetings(None, 14))

        self.assertEqual(result["data"][0]["id"], "meeting-1")
        self.assertEqual(api.calls, [("list", 14)])

    def test_destructive_tool_requires_confirmation(self):
        api = FakeAPI()
        tools = LynseMCPTools(lambda: api, authorized)

        with self.assertRaisesRegex(ValueError, "confirm=true"):
            asyncio.run(tools.delete_todos(None, ["todo-1"], confirm=False))

        self.assertEqual(api.calls, [])

    def test_server_exposes_structured_tool_result(self):
        api = FakeAPI()
        server = create_server(lambda: api, authorized)

        async def run():
            async with Client(server) as client:
                return await client.call_tool("list_meetings", {"days": 3})

        result = asyncio.run(run())

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["data"][0]["id"], "meeting-1")
        self.assertEqual(api.calls, [("list", 3)])

    def test_audio_tool_returns_resource_link(self):
        api = FakeAPI()
        server = create_server(lambda: api, authorized)

        async def run():
            async with Client(server) as client:
                return await client.call_tool(
                    "get_meeting_audio",
                    {"file_id": "meeting-1"},
                )

        result = asyncio.run(run())

        self.assertFalse(result.is_error)
        self.assertEqual(result.content[0].type, "resource_link")
        self.assertEqual(result.content[0].name, "meeting.m4a")
        self.assertEqual(result.content[0].mime_type, "audio/mp4")
        self.assertEqual(result.structured_content["data"]["size"], 1024)
        self.assertEqual(api.calls, [("audio", "meeting-1")])

    def test_reset_key_validates_replacement_before_switching_api(self):
        api = FakeAPI()
        flow = FakeAuthFlow()
        tools = LynseMCPTools(lambda: api, authorized, lambda: flow)
        tools._api = api

        result = asyncio.run(tools.reset_lynse_key(FakeContext(), confirm=True))

        self.assertTrue(result["configured"])
        self.assertTrue(flow.started)
        self.assertTrue(flow.waited)
        self.assertIsNone(tools._api)

    def test_reset_key_requires_confirmation(self):
        flow = FakeAuthFlow()
        tools = LynseMCPTools(FakeAPI, authorized, lambda: flow)

        with self.assertRaisesRegex(ValueError, "confirm=true"):
            asyncio.run(tools.reset_lynse_key(FakeContext(), confirm=False))

        self.assertFalse(flow.started)

    def test_audio_api_uses_presigned_download_endpoint(self):
        api = LynseAPI.__new__(LynseAPI)
        api._sanitize_param = lambda value, _kind: value
        api.get_file_info = lambda file_id: {
            "code": 200,
            "data": {
                "id": file_id,
                "originalFilename": "meeting.wav",
                "contentType": "audio/wav",
                "size": 4096,
            },
        }
        calls = []

        def request(method, path, params=None):
            calls.append((method, path, params))
            return {
                "code": 200,
                "msg": "SUCCESS",
                "data": "https://files.example.test/meeting.wav?signature=short-lived",
            }

        api._request = request

        result = api.get_audio_file("meeting-1")

        self.assertEqual(
            calls,
            [
                (
                    "GET",
                    "/api/business/file/presign/download",
                    {"fileId": "meeting-1"},
                )
            ],
        )
        self.assertEqual(result["data"]["fileName"], "meeting.wav")
        self.assertEqual(result["data"]["mimeType"], "audio/wav")
        self.assertEqual(result["data"]["size"], 4096)


if __name__ == "__main__":
    unittest.main()
