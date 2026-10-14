"""CLI is the implementation and contract source for MCP transcription tools."""

import asyncio
import inspect
from pathlib import Path
import unittest
from unittest.mock import Mock

from mcp import Client

from lynse import LynseAPI, TRANSCRIPTION_OPERATIONS
from lynse_mcp import LynseMCPTools, create_server


def authorized():
    return {'configured': True, 'credential_source': 'user_config'}


class TranscriptionToolTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock(spec=LynseAPI)
        self.api.transcribe_file.return_value = {'code': 200, 'data': {'taskId': 'task-1'}}
        self.api.get_transcription_status.return_value = {
            'code': 200, 'data': {'file-1': {'status': 'running', 'taskId': 'task-1'}}}
        self.api.auto_transcribe_new_recordings.return_value = {'code': 200, 'data': {
            'initialized': True, 'baselineCount': 3, 'stateFile': '/private/server/state.json',
            'submitted': [], 'pending': [], 'skipped': [], 'needsReview': []}}
        self.factory = Mock(return_value=self.api)
        self.tools = LynseMCPTools(self.factory, authorized)

    def test_shared_contract_covers_actual_api_signatures(self):
        self.assertFalse((Path(__file__).resolve().parents[1] / 'lynse.py').exists(),
                         'MCP must import the canonical lynse-cli client, not a copied implementation')
        for method, operation in TRANSCRIPTION_OPERATIONS.items():
            expected = set(inspect.signature(getattr(LynseAPI, method)).parameters) - {'self', 'state_file'}
            actual = set(inspect.signature(getattr(LynseMCPTools, operation['mcp_tool'])).parameters)
            self.assertEqual(actual - {'self', 'ctx', 'confirm'}, expected)
            self.assertEqual('confirm' in actual, operation['write'])

    def test_submission_delegates_parameters_and_returns_task_id(self):
        result = asyncio.run(self.tools.transcribe_meeting(
            None, 'file-1', True, 'template-1', 'model-1', 'language-1'))
        self.api.transcribe_file.assert_called_once_with(
            'file-1', template_id='template-1', model_id='model-1', language_id='language-1')
        self.assertEqual(result['data']['taskId'], 'task-1')

    def test_status_is_read_only_and_delegates_ids(self):
        result = asyncio.run(self.tools.get_transcription_status(None, ['file-1']))
        self.api.get_transcription_status.assert_called_once_with(['file-1'])
        self.assertEqual(result['data']['file-1']['status'], 'running')
        self.api.transcribe_file.assert_not_called()

    def test_writes_require_confirmation_before_accessing_api(self):
        for tool, arguments in ((self.tools.transcribe_meeting, ('file-1',)),
                                (self.tools.auto_transcribe_new_recordings, ())):
            with self.subTest(tool=tool.__name__), self.assertRaisesRegex(ValueError, 'confirm=true'):
                asyncio.run(tool(None, *arguments, confirm=False))
        self.factory.assert_not_called()

    def test_auto_scan_is_single_call_and_hides_server_path(self):
        result = asyncio.run(self.tools.auto_transcribe_new_recordings(None, True, model_id='model-1'))
        self.api.auto_transcribe_new_recordings.assert_called_once_with(
            template_id=None, model_id='model-1', language_id=None)
        self.assertTrue(result['data']['initialized'])
        self.assertNotIn('stateFile', result['data'])
        self.assertIn('stateFile', self.api.auto_transcribe_new_recordings.return_value['data'])

    def test_review_reports_do_not_disclose_upstream_error_bodies(self):
        self.api.auto_transcribe_new_recordings.return_value['data']['needsReview'] = [
            {'fileId': 'file-1', 'state': 'needs_review', 'error': 'private-upstream-token'}]
        result = asyncio.run(self.tools.auto_transcribe_new_recordings(None, True))
        self.assertEqual(result['data']['needsReview'], [
            {'fileId': 'file-1', 'state': 'needs_review'}])
        self.assertNotIn('private-upstream-token', str(result))

    def test_mcp_registration_schema_and_annotations_follow_shared_contract(self):
        async def run():
            async with Client(create_server(self.factory, authorized)) as client:
                listed = await client.list_tools()
                return {tool.name: tool for tool in listed.tools}
        registered = asyncio.run(run())
        for operation in TRANSCRIPTION_OPERATIONS.values():
            tool = registered[operation['mcp_tool']]
            self.assertEqual(tool.annotations.read_only_hint, not operation['write'])
            if operation['write']:
                self.assertIn('confirm', tool.input_schema['required'])
            self.assertNotIn('state_file', tool.input_schema['properties'])
        batch = registered['get_transcription_status'].input_schema['properties']['file_ids']
        self.assertEqual(batch['maxItems'], TRANSCRIPTION_OPERATIONS['get_transcription_status']['max_batch'])

    def test_protocol_calls_return_structured_results_and_reject_invalid_status_ids(self):
        async def run():
            async with Client(create_server(self.factory, authorized)) as client:
                submission = await client.call_tool('transcribe_meeting', {'file_id': 'file-1', 'confirm': True})
                invalid = await client.call_tool('get_transcription_status', {'file_ids': []})
                return submission, invalid
        submission, invalid = asyncio.run(run())
        self.assertFalse(submission.is_error)
        self.assertEqual(submission.structured_content['data']['taskId'], 'task-1')
        self.assertTrue(invalid.is_error)
        self.api.get_transcription_status.assert_not_called()


if __name__ == '__main__':
    unittest.main()
