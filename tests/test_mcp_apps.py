import asyncio
import os
import unittest
from unittest.mock import MagicMock, patch

from mcp import Client

from lynse_mcp import create_server
from lynse_ui import APP_MIME_TYPE, APP_URIS, media_origins


class MCPAppsTests(unittest.TestCase):
    def setUp(self):
        self.api = MagicMock()
        self.api.list_files_by_time_range.return_value = {"code": 200, "data": [
            {"id": f"file-{index}", "originalFilename": f"Meeting {index}", "bizDuration": 60,
             "url": "https://private.test/audio?signature=secret", "internalToken": "private-token"}
            for index in range(3)]}
        self.api.get_file_info.return_value = {"code": 200, "data": {
            "id": "file-1", "originalFilename": "Meeting 1", "bizDuration": "NaN"}}
        self.api.get_conclusion.return_value = {"code": 200, "data": {"content": "Summary prose"}}
        self.api.list_todos.return_value = {"code": 200, "total": 1, "data": [
            {"todoId": "todo-1", "todoContent": "Follow up", "isCompleted": "0", "fileId": "file-1"}]}

    def run_client(self, action):
        async def run():
            async with Client(create_server(lambda: self.api, lambda: {"configured": True})) as client:
                return await action(client)
        return asyncio.run(run())

    def test_resources_and_tools_advertise_portable_ui_metadata(self):
        async def action(client):
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            resources = (await client.list_resources()).resources
            for view, uri in APP_URIS.items():
                self.assertEqual(tools[f"show_{view}"].meta["ui"]["resourceUri"], uri)
                self.assertTrue(tools[f"show_{view}"].annotations.read_only_hint)
                result = await client.read_resource(uri)
                self.assertEqual(result.contents[0].mime_type, APP_MIME_TYPE)
                self.assertIn("<!doctype html>", result.contents[0].text)
                self.assertIn("resourceDomains", result.contents[0].meta["ui"]["csp"])
            self.assertEqual({str(resource.uri) for resource in resources}, set(APP_URIS.values()))
            for name in ("read_meetings_view", "read_meeting_view", "read_todos_view"):
                self.assertEqual(tools[name].meta["ui"], {"visibility": ["app"]})
            self.assertIsNone(tools["list_meetings"].meta)
        self.run_client(action)

    def test_meetings_page_data_is_normalized_and_excludes_private_urls(self):
        result = self.run_client(lambda c: c.call_tool("show_meetings", {"days": 30, "page_size": 2}))
        data = result.structured_content
        self.assertFalse(result.is_error)
        self.assertEqual([meeting["id"] for meeting in data["meetings"]], ["file-0", "file-1"])
        self.assertEqual(data["total"], 3)
        self.assertTrue(data["hasNext"])
        self.assertNotIn("private-token", result.model_dump_json())
        self.assertNotIn("signature=secret", result.model_dump_json())
        self.api.list_files_by_time_range.assert_called_once_with(30)
        page = self.run_client(lambda c: c.call_tool("read_meetings_view", {"page": 2, "page_size": 2}))
        self.assertEqual(page.structured_content["meetings"][0]["id"], "file-2")
        self.assertFalse(page.structured_content["hasNext"])

    def test_search_accepts_paginated_shapes_and_forwards_query(self):
        self.api.search_files.return_value = {"code": 200, "data": {"records": [], "total": 0}}
        result = self.run_client(lambda c: c.call_tool("show_meetings", {"keyword": " project ", "page": 2}))
        self.assertEqual(result.structured_content["meetings"], [])
        self.assertEqual(result.structured_content["total"], 0)
        self.api.search_files.assert_called_once_with("project", page=2, page_size=20)
        self.api.list_files_by_time_range.assert_not_called()

    def test_single_meeting_and_content_reuse_authorized_adapter(self):
        result = self.run_client(lambda c: c.call_tool("show_meetings", {"file_id": "file-1"}))
        self.assertEqual(result.structured_content["selectedId"], "file-1")
        self.assertEqual(result.structured_content["meetings"][0]["duration"], 0)
        result = self.run_client(lambda c: c.call_tool("read_meeting_view", {"file_id": "file-1", "content": "summary"}))
        self.assertEqual(result.structured_content["text"], "Summary prose")
        self.api.get_conclusion.assert_called_once_with(
            "file-1", first_only=True, include_mind_map=False
        )

    def test_real_api_summary_and_outline_fields_render_body_without_internal_metadata(self):
        self.api.get_conclusion.return_value = {"code": 200, "data": [
            {"conclusionText": "# Meeting summary\n\nA decision.", "taskId": "private-task"},
            {"conclusionText": "   ", "id": "empty-summary"},
            {"taskId": "private-task-without-body"},
        ]}
        self.api.get_outline.return_value = {"code": 200, "data": {
            "outlineText": "00:00 Introduction\n01:30 Decision", "taskId": "private-task"}}
        for kind, expected in (("summary", "# Meeting summary\n\nA decision."),
                               ("outline", "00:00 Introduction\n01:30 Decision")):
            with self.subTest(content=kind):
                result = self.run_client(lambda c: c.call_tool("read_meeting_view", {
                    "file_id": "file-1", "content": kind}))
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["text"], expected)
                self.assertNotIn("private-task", result.model_dump_json())

    def test_todos_preserve_status_filter_and_do_not_treat_string_zero_as_completed(self):
        result = self.run_client(lambda c: c.call_tool("show_todos", {"status": "open", "page": 2}))
        todo = result.structured_content["todos"][0]
        self.assertFalse(todo["completed"])
        self.assertEqual(todo["fileId"], "file-1")
        self.api.list_todos.assert_called_once_with(status="open", page_num=2, page_size=20)
        self.api.delete_todos.assert_not_called()

    def test_confirmation_is_pending_has_text_fallback_and_never_accesses_api(self):
        result = self.run_client(lambda c: c.call_tool("show_confirmation", {
            "question": "Choose an action", "options": [{"id": "A", "label": "Keep"}, {"id": "B", "label": "Review"}]}))
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["status"], "awaiting_user")
        self.assertNotIn("selected", result.structured_content)
        self.assertIn("A. Keep", result.content[0].text)
        self.assertIn("当前尚未确认", result.content[0].text)
        self.assertEqual(self.api.mock_calls, [])

    def test_confirmation_rejects_duplicate_empty_or_insufficient_options(self):
        for options in ([{"id": "A", "label": "One"}],
                        [{"id": "A", "label": "One"}, {"id": "A", "label": "Two"}],
                        [{"id": " ", "label": "One"}, {"id": "B", "label": "Two"}]):
            with self.subTest(options=options):
                result = self.run_client(lambda c: c.call_tool("show_confirmation", {"question": "Choose", "options": options}))
                self.assertTrue(result.is_error)

    def test_invalid_page_is_rejected_before_an_api_call(self):
        result = self.run_client(lambda c: c.call_tool("show_meetings", {"page": 0}))
        self.assertTrue(result.is_error)
        self.assertEqual(self.api.mock_calls, [])

    def test_upstream_failure_is_not_silently_displayed_as_an_empty_list(self):
        self.api.list_todos.return_value = {"code": 500, "msg": "internal private token", "data": None}
        result = self.run_client(lambda c: c.call_tool("show_todos", {}))
        self.assertTrue(result.is_error)
        self.assertNotIn("internal private token", result.model_dump_json())

    def test_media_csp_only_accepts_deployment_owned_https_origins(self):
        with patch.dict(os.environ, {"LYNSE_MCP_MEDIA_ORIGINS": "https://media.example.test,https://media.example.test/"}):
            self.assertEqual(media_origins().count("https://media.example.test"), 1)
        for value in ("*", "http://media.test", "https://media.test/path", "https://user:secret@media.test"):
            with self.subTest(value=value), patch.dict(os.environ, {"LYNSE_MCP_MEDIA_ORIGINS": value}):
                with self.assertRaises(ValueError):
                    media_origins()


if __name__ == "__main__":
    unittest.main()
