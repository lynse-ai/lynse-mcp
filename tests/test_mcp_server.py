import asyncio
import unittest

from mcp import Client

from lynse_mcp import LynseMCPTools, create_server


class FakeAPI:
    def __init__(self):
        self.calls = []

    def list_files_by_time_range(self, days):
        self.calls.append(("list", days))
        return {"code": 200, "data": [{"id": "meeting-1"}]}

    def delete_todos(self, todo_ids):
        self.calls.append(("delete", todo_ids))
        return {"code": 200, "data": todo_ids}


class LynseMCPToolTests(unittest.TestCase):
    def test_read_tool_delegates_to_existing_api(self):
        api = FakeAPI()
        tools = LynseMCPTools(lambda: api)

        result = tools.list_meetings(14)

        self.assertEqual(result["data"][0]["id"], "meeting-1")
        self.assertEqual(api.calls, [("list", 14)])

    def test_destructive_tool_requires_confirmation(self):
        api = FakeAPI()
        tools = LynseMCPTools(lambda: api)

        with self.assertRaisesRegex(ValueError, "confirm=true"):
            tools.delete_todos(["todo-1"], confirm=False)

        self.assertEqual(api.calls, [])

    def test_server_exposes_structured_tool_result(self):
        api = FakeAPI()
        server = create_server(lambda: api)

        async def run():
            async with Client(server, raise_exceptions=True) as client:
                return await client.call_tool("list_meetings", {"days": 3})

        result = asyncio.run(run())

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["data"][0]["id"], "meeting-1")
        self.assertEqual(api.calls, [("list", 3)])


if __name__ == "__main__":
    unittest.main()
