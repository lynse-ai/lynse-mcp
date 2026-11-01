#!/usr/bin/env python3
"""Run and inspect ECS Cloud Assistant commands using the configured Aliyun CLI."""
import argparse
import base64
import json
from pathlib import Path
import re
import subprocess


def cli(action, region, args):
    result = subprocess.run(['aliyun', 'ecs', action, '--region', region,
                             '--RegionId', region, *args], capture_output=True, text=True)
    if result.returncode:
        # OAuth failures can echo a refresh token in the provider's message.
        # Do not copy untrusted CLI diagnostics into chat or deployment logs.
        try:
            error = json.loads(result.stderr.strip() or result.stdout.strip())
            code = error.get('error_code', '') if isinstance(error, dict) else ''
        except ValueError:
            code = ''
        label = code if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', code) else 'unknown'
        hint = (' Reauthenticate with: aliyun configure --mode OAuth --oauth-site-type CN'
                if label == 'invalid_grant' else ' Check the configured Aliyun CLI account.')
        raise SystemExit(f'Aliyun {action} failed ({label}).{hint}')
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', default='cn-shanghai')
    parser.add_argument('--instance', required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run')
    run.add_argument('script', type=Path)
    run.add_argument('--timeout', type=int, default=120)
    poll = sub.add_parser('poll')
    poll.add_argument('invoke_id')
    send = sub.add_parser('send')
    send.add_argument('file', type=Path)
    send.add_argument('--directory', required=True)
    args = parser.parse_args()
    if args.action == 'run':
        content = base64.b64encode(args.script.read_bytes()).decode()
        if len(content) > 24 * 1024:
            raise SystemExit('Command exceeds Cloud Assistant 24 KiB encoded limit')
        result = cli('RunCommand', args.region, ['--InstanceId.1',args.instance,
                      '--Type','RunShellScript','--ContentEncoding','Base64',
                      '--CommandContent',content,'--Timeout',str(args.timeout),
                      '--KeepCommand','false','--Name','lynse-mcp-deployment'])
        print(json.dumps({k:result.get(k) for k in ['CommandId','InvokeId']},ensure_ascii=False))
    elif args.action == 'send':
        content = base64.b64encode(args.file.read_bytes()).decode()
        if len(content) > 32 * 1024:
            raise SystemExit('File exceeds Cloud Assistant 32 KiB encoded limit; split first')
        result = cli('SendFile',args.region,['--InstanceId.1',args.instance,
                     '--Name',args.file.name,'--TargetDir',args.directory,
                     '--ContentType','Base64','--Content',content,
                     '--Overwrite','false','--FileMode','0600'])
        print(json.dumps({k:result.get(k) for k in ['InvokeId','RequestId']},ensure_ascii=False))
    else:
        if args.invoke_id.startswith('f-'):
            result = cli('DescribeSendFileResults', args.region,
                         ['--InvokeId', args.invoke_id, '--InstanceId', args.instance])
            for invocation in result.get('Invocations', {}).get('Invocation', []):
                for item in invocation.get('InvokeInstances', {}).get('InvokeInstance', []):
                    print(json.dumps({k: item.get(k) for k in
                                      ['InvocationStatus', 'ErrorCode', 'ErrorInfo']}, ensure_ascii=False))
            return
        result = cli('DescribeInvocationResults',args.region,
                     ['--InvokeId',args.invoke_id,'--InstanceId',args.instance,'--ContentEncoding','PlainText'])
        values = result.get('Invocation',{}).get('InvocationResults',{}).get('InvocationResult',[])
        if not values:
            print(json.dumps(result,ensure_ascii=False))
        for item in values:
            print(json.dumps({k:item.get(k) for k in ['InvocationStatus','ExitCode','ErrorCode','ErrorInfo','Output']},ensure_ascii=False))


if __name__ == '__main__':
    main()
