"""Submit/read deployment tasks through the official Tencent Cloud CLI."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--cli', default=os.environ.get('LYNSE_TCCLI_BIN', 'tccli'))
parser.add_argument('--region', default='na-siliconvalley')
parser.add_argument('--instance', default='lhins-m5rp3ob7')
sub = parser.add_subparsers(dest='operation', required=True)
run = sub.add_parser('run')
run.add_argument('script')
run.add_argument('--timeout', type=int, default=900)
poll = sub.add_parser('poll')
poll.add_argument('invocation')
args = parser.parse_args()
if args.operation == 'run':
    content = base64.b64encode(Path(args.script).read_bytes()).decode()
    if len(content) > 65536:
        raise SystemExit('TAT command exceeds 64 KB')
    action = 'RunCommand'
    params = {'Content': content, 'InstanceIds': [args.instance], 'CommandType': 'SHELL',
              'CommandName': 'lynse-mcp-ai-test', 'Timeout': args.timeout,
              'WorkingDirectory': '/tmp', 'Username': 'root', 'SaveCommand': False}
else:
    action = 'DescribeInvocationTasks'
    params = {'Filters': [{'Name': 'invocation-id', 'Values': [args.invocation]}], 'HideOutput': False, 'Limit': 10}
with tempfile.NamedTemporaryFile(mode='w', suffix='.json', prefix='lynse-tat-', delete=False) as output:
    json.dump(params, output)
    path = output.name
try:
    result = subprocess.run([args.cli, 'tat', action, '--region', args.region,
                             '--cli-input-json', 'file://' + path], capture_output=True, text=True)
finally:
    Path(path).unlink(missing_ok=True)
if result.returncode:
    # Keep diagnostics local; do not print CLI credential refresh payloads.
    descriptor, log = tempfile.mkstemp(prefix='lynse-tat-error-', suffix='.log')
    with os.fdopen(descriptor, 'w') as output:
        output.write(result.stdout + result.stderr)
    raise SystemExit('Tencent CLI call failed; diagnostics saved privately: ' + log)
data = json.loads(result.stdout)
data = data.get('Response', data)
if args.operation == 'run':
    print(json.dumps({key: data.get(key) for key in ['InvocationId', 'RequestId']}, ensure_ascii=False))
else:
    for task in data.get('InvocationTaskSet', []):
        value = task.get('TaskResult') or {}
        print(json.dumps({'status': task.get('TaskStatus'), 'exit_code': value.get('ExitCode'),
            'output': base64.b64decode(value.get('Output') or '').decode(errors='replace'),
            'output_truncated': value.get('Dropped'), 'request_id': data.get('RequestId')}, ensure_ascii=False))
