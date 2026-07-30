import asyncio
import unittest

from mcp.shared.memory import create_connected_server_and_client_session

from lynse import LynseAPI
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
            async with create_connected_server_and_client_session(
                server, raise_exceptions=True
            ) as client:
                return await client.call_tool("list_meetings", {"days": 3})

        result = asyncio.run(run())

        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent["data"][0]["id"], "meeting-1")
        self.assertEqual(api.calls, [("list", 3)])

    def test_audio_tool_returns_resource_link(self):
        api = FakeAPI()
        server = create_server(lambda: api, authorized)

        async def run():
            async with create_connected_server_and_client_session(
                server, raise_exceptions=True
            ) as client:
                return await client.call_tool(
                    "get_meeting_audio",
                    {"file_id": "meeting-1"},
                )

        result = asyncio.run(run())

        self.assertFalse(result.isError)
        self.assertEqual(result.content[0].type, "resource_link")
        self.assertEqual(result.content[0].name, "meeting.m4a")
        self.assertEqual(result.content[0].mimeType, "audio/mp4")
        self.assertEqual(result.structuredContent["data"]["size"], 1024)
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
