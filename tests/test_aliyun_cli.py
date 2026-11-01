import json
import subprocess
import unittest
from unittest.mock import patch

from scripts.aliyun_cli import cli


class AliyunDiagnosticsTests(unittest.TestCase):
    def test_oauth_failure_never_echoes_refresh_token_or_raw_stderr(self):
        for stderr in (json.dumps({'error_code': 'invalid_grant',
                                  'message': 'Refresh token expired: private-refresh-token'}),
                       'failed with private-refresh-token'):
            with self.subTest(stderr=stderr), patch('scripts.aliyun_cli.subprocess.run',
                    return_value=subprocess.CompletedProcess([], 1, '', stderr)):
                with self.assertRaises(SystemExit) as error:
                    cli('RunCommand', 'cn-shanghai', [])
                self.assertNotIn('private-refresh-token', str(error.exception))
                self.assertIn('Aliyun RunCommand failed', str(error.exception))
