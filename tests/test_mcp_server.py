import asyncio
import json
from pathlib import Path
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

import requests

from mcp import Client

from lynse import CLI_VERSION, LynseAPI
from lynse_mcp import LynseMCPTools, MCP_VERSION, create_server


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

    def test_handshake_reports_released_version_everywhere(self):
        root = Path(__file__).resolve().parents[1]
        package_path = root / "package.json"
        if package_path.is_file():
            package = json.loads(package_path.read_text(encoding="utf-8"))
            self.assertEqual(MCP_VERSION, package["version"])
            # Production releases ship runtime files only; check metadata when present.
            for relative in ("package-lock.json", "integrations/workbuddy/connector-meta.json"):
                path = root / relative
                if not path.is_file():
                    continue
                metadata = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(metadata.get("version"), package["version"], relative)
                if relative == "package-lock.json":
                    self.assertEqual(metadata["packages"][""]["version"], package["version"])

        async def run():
            async with Client(create_server(lambda: FakeAPI(), authorized)) as client:
                return client.server_info.version

        self.assertEqual(asyncio.run(run()), MCP_VERSION)

    def test_uses_canonical_lynse_cli_1_8_5(self):
        self.assertGreaterEqual(
            tuple(int(part) for part in CLI_VERSION.split('.')),
            (1, 8, 5),
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

    @staticmethod
    def _get_meeting(api, args=None):
        async def run():
            async with Client(create_server(lambda: api, authorized, remote=True)) as client:
                return await client.call_tool(
                    "get_meeting", {"file_id": "meeting-1", **(args or {})}
                )

        return asyncio.run(run())

    def test_get_meeting_defaults_to_conclusion_prose_without_mind_map(self):
        api = Mock(spec=LynseAPI)
        api.get_conclusion.return_value = {
            "code": 200,
            "data": {
                "id": "c-1",
                "conclusionText": "会议结论正文\n![思维导图](data:image/png;base64," + "A" * 30000 + ")",
                "mindMap": {"huge": "x" * 20000},
            },
        }
        result = self._get_meeting(api)
        self.assertFalse(result.is_error, result)
        api.get_conclusion.assert_called_once_with(
            "meeting-1", first_only=True, include_mind_map=False
        )
        payload = result.structured_content
        self.assertEqual(payload["data"], "会议结论正文\n[inline image omitted]")
        self.assertEqual(payload["content"], "conclusion")
        self.assertFalse(payload["truncated"])
        # The base64 mind map must never reach the caller or the length budget.
        self.assertLess(payload["total_chars"], 100)
        self.assertEqual(api.get_file_info.call_count, 0)

    def test_get_meeting_strips_inline_base64_images_for_every_content_kind(self):
        api = Mock(spec=LynseAPI)
        api.get_file_info.return_value = {
            "code": 200,
            "data": {"id": "meeting-1",
                     "note": "![x](data:image/png;base64," + "B" * 40000 + ")"},
        }
        result = self._get_meeting(api, {"content": "info"}).structured_content
        self.assertNotIn("base64", json.dumps(result))
        self.assertEqual(result["data"]["note"], "[inline image omitted]")
        self.assertLess(result["total_chars"], 1000)

    def test_get_meeting_caps_long_content_and_paginates(self):
        prose = "".join(chr(0x4E00 + (i % 2000)) for i in range(2500))
        api = Mock(spec=LynseAPI)
        api.get_conclusion.return_value = {"code": 200, "data": {"content": prose}}
        first = self._get_meeting(api, {"max_chars": 500}).structured_content
        self.assertTrue(first["truncated"])
        self.assertEqual(first["total_chars"], len(prose))
        self.assertEqual(first["returned_chars"], 500)
        self.assertEqual(first["next_offset"], 500)
        self.assertEqual(first["data"], prose[:500])

        rest = []
        offset = first["next_offset"]
        while offset is not None:
            page = self._get_meeting(api, {"max_chars": 500, "offset": offset}).structured_content
            rest.append(page["data"])
            offset = page["next_offset"]
        self.assertEqual(first["data"] + "".join(rest), prose)

    def test_get_meeting_exposes_schema_bounds_for_content_length(self):
        api = Mock(spec=LynseAPI)
        api.get_conclusion.return_value = {"code": 200, "data": {"content": "ok"}}

        async def run():
            async with Client(create_server(lambda: api, authorized, remote=True)) as client:
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                return tools["get_meeting"].input_schema

        schema = asyncio.run(run())
        props = schema["properties"]
        self.assertEqual(props["content"]["default"], "conclusion")
        self.assertEqual(props["max_chars"]["maximum"], 50000)
        self.assertEqual(props["offset"]["minimum"], 0)

    def test_get_meeting_selects_other_content_explicitly(self):
        api = Mock(spec=LynseAPI)
        api.get_file_info.return_value = {"code": 200, "data": {"id": "meeting-1"}}
        api.get_transcription_text.return_value = {"code": 200, "data": "转写文本"}
        info = self._get_meeting(api, {"content": "info"})
        transcript = self._get_meeting(api, {"content": "transcript"})
        self.assertEqual(info.structured_content["data"]["id"], "meeting-1")
        self.assertEqual(transcript.structured_content["data"], "转写文本")
        self.assertEqual(api.get_conclusion.call_count, 0)
        api.get_file_info.assert_called_once_with("meeting-1")
        api.get_transcription_text.assert_called_once_with("meeting-1")

    def test_get_meeting_opts_into_all_conclusions_and_mind_map(self):
        api = Mock(spec=LynseAPI)
        conclusions = [{"content": "one"}, {"content": "two"}]
        api.get_conclusion.return_value = {"code": 200, "data": conclusions}
        result = self._get_meeting(
            api, {"all_conclusions": True, "include_mind_map": True}
        ).structured_content
        api.get_conclusion.assert_called_once_with(
            "meeting-1", first_only=False, include_mind_map=True
        )
        self.assertEqual(result["data"], conclusions)

    def test_get_meeting_works_with_legacy_cli_without_mind_map_flag(self):
        class LegacyAPI:
            def __init__(self):
                self.calls = []

            def get_conclusion(self, file_id, first_only=True):
                self.calls.append((file_id, first_only))
                return {"code": 200, "data": {"content": "legacy conclusion"}}

        api = LegacyAPI()
        result = self._get_meeting(api)
        self.assertFalse(result.is_error, result)
        self.assertEqual(api.calls, [("meeting-1", True)])
        self.assertEqual(result.structured_content["data"], "legacy conclusion")

    def test_mark_tools_expose_read_only_results(self):
        api = Mock(spec=LynseAPI)
        image = {"imageId": "image-1", "timestamp": 2000}
        signed_url = "https://images.example.test/mark.jpg?Signature=temporary%2Bsignature"
        api.get_recording_marks.return_value = {
            "code": 200, "data": {"recordMarks": [2000], "recordMarkResults": [], "markImages": [image]},
        }
        api.list_mark_images.return_value = {"code": 200, "data": [image]}
        api.get_mark_image_url.return_value = {"code": 200, "data": signed_url}

        async def run():
            async with Client(create_server(lambda: api, authorized, remote=True)) as client:
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                for name in ("get_meeting_marks", "list_mark_images", "get_mark_image"):
                    self.assertTrue(tools[name].annotations.read_only_hint)
                marks = await client.call_tool("get_meeting_marks", {"file_id": "meeting-1"})
                images = await client.call_tool("list_mark_images", {"md5": "recording-md5"})
                link = await client.call_tool("get_mark_image", {"image_id": "image-1"})
                return marks, images, link

        marks, images, link = asyncio.run(run())
        self.assertFalse(marks.is_error)
        self.assertFalse(images.is_error)
        self.assertFalse(link.is_error)
        self.assertEqual(marks.structured_content["data"]["markImages"], [image])
        self.assertEqual(images.structured_content["data"], [image])
        self.assertEqual(link.content[0].type, "resource_link")
        self.assertEqual(str(link.content[0].uri), signed_url)
        api.get_recording_marks.assert_called_once_with("meeting-1")
        api.list_mark_images.assert_called_once_with("recording-md5")
        api.get_mark_image_url.assert_called_once_with("image-1")

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
