#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lynse CLI - 核心 API 封装模块
跨平台支持：Windows / macOS / Linux

用法：
    python3 lynse.py <command> [参数...]

示例：
    python3 lynse.py getCurrentCustomer
    python3 lynse.py getFileInfo 12345

注：Windows 上用 `python` 或 `py -3` 替代 `python3`。
"""

import base64
import mimetypes
import os
import sys
import json
import re
import hashlib
import warnings
from pathlib import Path
import platform
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Union
from urllib.parse import urlsplit

MIN_PYTHON_VERSION = (3, 11)
MIN_PYTHON_VERSION_TEXT = '.'.join(str(part) for part in MIN_PYTHON_VERSION)

if sys.version_info < MIN_PYTHON_VERSION:
    print(
        f"Error: lynse-cli requires Python {MIN_PYTHON_VERSION_TEXT} or newer "
        f"(found {platform.python_version()}).",
        file=sys.stderr,
    )
    raise SystemExit(1)

warnings.filterwarnings(
    "ignore",
    message=r"urllib3 v2 only supports OpenSSL 1\.1\.1\+",
)
warnings.filterwarnings(
    "ignore",
    message=r"urllib3 .* doesn't match a supported version",
)

try:
    import requests
except ImportError:
    class _MissingRequests:
        """Sentinel stub when requests is not installed. Allows tests to patch methods onto it."""
        class RequestException(Exception):
            pass
        # HTTP method stubs — raise ImportError if called without being patched by tests
        def get(*args, **kwargs):
            raise ImportError("requests library is not installed")
        def post(*args, **kwargs):
            raise ImportError("requests library is not installed")
        def request(*args, **kwargs):
            raise ImportError("requests library is not installed")
    requests = _MissingRequests()
    print("Error: requests library is not installed. Run: pip install requests", file=sys.stderr)


def _lynse_log_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# CLI 版本
CLI_VERSION = '1.7.0'

# 语义化退出码
EXIT_SUCCESS = 0
EXIT_INVALID = 1       # 参数错误或未知异常
EXIT_AUTH = 2          # 鉴权失败 / Token 失效
EXIT_NETWORK = 3       # 网络连接错误
EXIT_TIMEOUT = 4       # 请求超时
EXIT_PERMISSION = 5    # 权限不足
EXIT_SERVER = 6        # 服务端业务错误


def _resolve_exit_code(error: 'LynseAPIError') -> int:
    """将 LynseAPIError 映射为语义化退出码。"""
    http = error.http_code
    code = error.code
    msg = (error.message or '').lower()
    if http in (401,) or code in (401, 2000) or 'token' in msg and ('过期' in msg or 'invalid' in msg or 'expired' in msg or 'failed' in msg):
        return EXIT_AUTH
    if http in (403,) or code in (403,) or '权限不足' in msg or 'permission' in msg or 'insufficient' in msg:
        return EXIT_PERMISSION
    if http is not None and 500 <= http < 600:
        return EXIT_SERVER
    if 'timeout' in msg or '超时' in msg:
        return EXIT_TIMEOUT
    if '网络' in msg or 'network' in msg or 'connect' in msg or 'dns' in msg or 'unreachable' in msg:
        return EXIT_NETWORK
    if http is not None and http != 200:
        return EXIT_SERVER
    if code is not None and code != 200:
        return EXIT_SERVER
    return EXIT_INVALID


# 友好命令别名
_SIMPLE_ALIASES = {
    'me': 'getCurrentCustomer',
    'whoami': 'getCurrentCustomer',
    'profile': 'getCurrentCustomer',
    'version': '__version__',
    'doctor': '__doctor__',
    'update': '__update__',
}

_SUBCOMMAND_ALIASES = {
    'meetings': {
        'list': 'listFilesByTimeRange',
        'month': 'listFilesByMonth',
        'week': 'listFilesByWeek',
        'range': 'listFilesByRange',
        'search': 'searchFiles',
        'transcript': 'getTranscriptionRecord',
        'transcript-text': 'getTranscriptionText',
        'summary': 'getConclusion',
        'info': 'getFileInfo',
        'outline': 'getOutline',
        'organize': 'organizeMeetings',
    },
    'folders': {
        'list': 'listFolders',
        'move': 'changeFolder',
        'create': 'createFolder',
    },
    'todos': {
        'list': 'listTodos',
        'clear': 'clearCompletedTodos',
        'delete': 'deleteTodos',
    },
    'devices': {
        'list': 'getMyDevices',
        'info': 'getDeviceInfo',
        'unbind': 'unbindDevice',
    },
    'models': {
        'list': 'getAiModels',
        'add': 'addModel',
        'delete': 'deleteModel',
        'edit': 'editModel',
        'enable': 'enableModel',
    },
    'auth': {
        'login': '__auth_login__',
        'status': '__auth_status__',
        'logout': '__auth_logout__',
        'doctor': '__auth_doctor__',
    },
}

_ALIAS_HANDLERS = {
    'listFilesByTimeRange': lambda api, a: api.list_files_by_time_range(_extract_days(a)),
    'listFilesByMonth': lambda api, a: api.list_files_by_month(*_parse_month_args(a)),
    'listFilesByWeek': lambda api, a: api.list_files_by_week(*_parse_week_args(a)),
    'listFilesByRange': lambda api, a: api.list_files_by_range(a[0], a[1]) if len(a) >= 2 else _missing_arg('start_date end_date (YYYY-MM-DD)'),
    'searchFiles': lambda api, a: api.search_files(a[0], **_extract_page_kwargs(a[1:])) if a else _missing_arg('search keyword'),
    'getTranscriptionRecord': lambda api, a: api.get_transcription_record(a[0]) if a else _missing_arg('file ID'),
    'getTranscriptionText': lambda api, a: api.get_transcription_text(a[0]) if a else _missing_arg('file ID'),
    'getConclusion': lambda api, a: api.get_conclusion(a[0]) if a else _missing_arg('file ID'),
    'getFileInfo': lambda api, a: api.get_file_info(a[0]) if a else _missing_arg('file ID'),
    'getOutline': lambda api, a: api.get_outline(a[0]) if a else _missing_arg('file ID'),
    'organizeMeetings': lambda api, a: api.organize_meetings(**_parse_organize_args(a)),
    'listFolders': lambda api, a: api.list_folders(),
    'changeFolder': lambda api, a: api.change_folder(json.loads(a[0])) if a else _missing_arg('JSON payload'),
    'createFolder': lambda api, a: api.create_folder(json.loads(a[0])) if a else _missing_arg('JSON data'),
    'listTodos': lambda api, a: api.list_todos(status=(a[0] if a else 'all'), page_num=int(a[1]) if len(a) > 1 else 1, page_size=int(a[2]) if len(a) > 2 else 20),
    'clearCompletedTodos': lambda api, a: api.clear_completed_todos(),
    'deleteTodos': lambda api, a: _handle_delete_todos(api, a),
    'getMyDevices': lambda api, a: api.get_my_devices(),
    'getDeviceInfo': lambda api, a: api.get_device_info(a[0]) if a else _missing_arg('device ID'),
    'unbindDevice': lambda api, a: api.unbind_device(a[0]) if a else _missing_arg('device ID'),
    'getAiModels': lambda api, a: api.get_ai_models(),
    'addModel': lambda api, a: api.add_model(json.loads(a[0])) if a else _missing_arg('JSON data'),
    'deleteModel': lambda api, a: api.delete_model(a[0]) if a else _missing_arg('model ID'),
    'editModel': lambda api, a: api.edit_model(json.loads(a[0])) if a else _missing_arg('JSON data'),
    'enableModel': lambda api, a: api.enable_model(a[0], a[1].lower() in ('true', '1', 'yes')) if len(a) >= 2 else _missing_arg('model ID and true/false'),
    'getCurrentCustomer': lambda api, a: api.get_current_customer(),
}

_ALIAS_INFO = {
    'listFilesByTimeRange': 'meetings list',
    'listFilesByMonth': 'meetings month',
    'listFilesByWeek': 'meetings week',
    'listFilesByRange': 'meetings range',
    'searchFiles': 'meetings search',
    'getTranscriptionRecord': 'meetings transcript',
    'getTranscriptionText': 'meetings transcript-text',
    'getConclusion': 'meetings summary',
    'getFileInfo': 'meetings info',
    'getOutline': 'meetings outline',
    'organizeMeetings': 'meetings organize',
    'listFolders': 'folders list',
    'changeFolder': 'folders move',
    'createFolder': 'folders create',
    'listTodos': 'todos list',
    'clearCompletedTodos': 'todos clear',
    'deleteTodos': 'todos delete',
    'getMyDevices': 'devices list',
    'getDeviceInfo': 'devices info',
    'unbindDevice': 'devices unbind',
    'getAiModels': 'models list',
    'addModel': 'models add',
    'deleteModel': 'models delete',
    'editModel': 'models edit',
    'enableModel': 'models enable',
    'getCurrentCustomer': 'me',
}


def _extract_days(args: list) -> int:
    """从参数中提取 --days N。"""
    for i, arg in enumerate(args):
        if arg == '--days' and i + 1 < len(args):
            return int(args[i + 1])
        if arg.startswith('--days='):
            return int(arg.split('=', 1)[1])
    return 7


def _parse_organize_args(args: list) -> dict:
    """Parse `meetings organize` flags.

    Returns {days:int|None, execute:bool, yes:bool, include_no_conclusion:bool}.
    `days=None` means all meetings (no time filter).
    """
    out = {'days': None, 'execute': False, 'yes': False, 'include_no_conclusion': False}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ('--days',) and i + 1 < len(args):
            try:
                out['days'] = int(args[i + 1])
            except ValueError:
                print(f"Error: --days expects an integer, got '{args[i + 1]}'", file=sys.stderr)
                sys.exit(EXIT_INVALID)
            i += 2
        elif arg.startswith('--days='):
            try:
                out['days'] = int(arg.split('=', 1)[1])
            except ValueError:
                print(f"Error: --days expects an integer", file=sys.stderr)
                sys.exit(EXIT_INVALID)
            i += 1
        elif arg == '--execute':
            out['execute'] = True; i += 1
        elif arg == '--yes' or arg == '-y':
            out['yes'] = True; i += 1
        elif arg == '--include-no-conclusion':
            out['include_no_conclusion'] = True; i += 1
        else:
            print(f"Error: unknown option '{arg}'. Usage: meetings organize [--days N] [--execute] [--yes] [--include-no-conclusion]", file=sys.stderr)
            sys.exit(EXIT_INVALID)
    return out


def _extract_page_kwargs(args: list) -> dict:
    """从参数中提取 --page 和 --size。"""
    kwargs = {}
    i = 0
    while i < len(args):
        if args[i] == '--page' and i + 1 < len(args):
            kwargs['page'] = int(args[i + 1]); i += 2
        elif args[i] == '--size' and i + 1 < len(args):
            kwargs['page_size'] = int(args[i + 1]); i += 2
        else:
            i += 1
    return kwargs


def _missing_arg(what: str):
    """参数缺失时打印错误并返回空 dict。"""
    print(f"Error: missing required argument: {what}", file=sys.stderr)
    sys.exit(EXIT_INVALID)


def _transcription_entries_to_text(data) -> str:
    """Render transcription records as speaker-prefixed plain text."""
    if isinstance(data, dict):
        for key in ('records', 'list', 'rows', 'items', 'segments'):
            value = data.get(key)
            if isinstance(value, list):
                data = value
                break
    if not isinstance(data, list):
        return str(data or '')

    lines = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get('text') or entry.get('content') or '').strip()
        if not text:
            continue
        speaker = entry.get('speakerName') or entry.get('speaker') or ''
        if not speaker:
            speaker_id = entry.get('speakerId')
            speaker = f"Speaker {speaker_id}" if speaker_id is not None else ''
        prefix = _format_transcription_timestamp(entry)
        speaker_text = f"{speaker}: {text}" if speaker else text
        lines.append(f"{prefix} {speaker_text}" if prefix else speaker_text)
    return '\n'.join(lines)


def _format_transcription_timestamp(entry: dict) -> str:
    start = _first_present(entry, ('beginTime', 'startTime', 'timestamp', 'time'))
    end = _first_present(entry, ('endTime', 'stopTime'))
    if start in (None, ''):
        return ''
    start_text = _format_transcription_time_value(start)
    if end not in (None, ''):
        return f"[{start_text}-{_format_transcription_time_value(end)}]"
    return f"[{start_text}]"


def _first_present(data: dict, keys: tuple):
    for key in keys:
        if key in data and data.get(key) not in (None, ''):
            return data.get(key)
    return None


def _format_transcription_time_value(value) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        total_ms = int(round(float(value)))
        minutes, rem_ms = divmod(total_ms, 60000)
        seconds, millis = divmod(rem_ms, 1000)
        return f"{minutes:02d}:{seconds:02d}.{millis:03d}"
    return str(value).strip()


def _handle_delete_todos(api, args: list):
    """处理 deleteTodos 的别名调用。"""
    if not args:
        print("Error: missing todo IDs", file=sys.stderr)
        sys.exit(EXIT_INVALID)
    raw_ids = " ".join(args).strip()
    try:
        parsed = json.loads(raw_ids)
        todo_ids = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        todo_ids = [item.strip() for item in raw_ids.split(',') if item.strip()]
    return api.delete_todos([str(item) for item in todo_ids])


def _parse_month_args(args: list):
    """Parse month query args. Accepts: '2026-04', '2026 4', or '4' (current year)."""
    if not args:
        print("Error: 'meetings month' requires: <YYYY-MM> or <YYYY> <M> or <M> (current year)", file=sys.stderr)
        sys.exit(EXIT_INVALID)
    if len(args) >= 2:
        return int(args[0]), int(args[1])
    token = args[0]
    if '-' in token:
        parts = token.split('-')
        return int(parts[0]), int(parts[1])
    # Single number = month in current year
    return datetime.now().year, int(token)


def _parse_week_args(args: list):
    """Parse week query args. Accepts: '2026-W15', '2026 15', or '15' (current year)."""
    if not args:
        print("Error: 'meetings week' requires: <YYYY-Wnn> or <YYYY> <W> or <W> (current year)", file=sys.stderr)
        sys.exit(EXIT_INVALID)
    if len(args) >= 2:
        return int(args[0]), int(args[1])
    token = args[0]
    # Support YYYY-Wnn format
    if '-W' in token or '-w' in token:
        parts = token.upper().split('-W')
        return int(parts[0]), int(parts[1])
    if '-' in token:
        parts = token.split('-')
        return int(parts[0]), int(parts[1])
    return datetime.now().isocalendar()[0], int(token)


def _resolve_alias(command: str, args: list):
    """将友好命令别名解析为内部命令名。返回 (canonical, modified_args, is_alias)。"""
    if command in _SIMPLE_ALIASES:
        return _SIMPLE_ALIASES[command], args, True
    if command in _SUBCOMMAND_ALIASES:
        subs = _SUBCOMMAND_ALIASES[command]
        if not args:
            print(f"Error: '{command}' requires a subcommand. Available: {', '.join(subs.keys())}", file=sys.stderr)
            sys.exit(EXIT_INVALID)
        sub = args[0]
        if sub not in subs:
            print(f"Error: unknown subcommand '{command} {sub}'. Available: {', '.join(subs.keys())}", file=sys.stderr)
            sys.exit(EXIT_INVALID)
        return subs[sub], args[1:], True
    return command, args, False


def _parse_global_flags(args: list):
    """从参数列表中提取并剥离全局标志。返回 (flags, remaining_args)。"""
    flags = {'format': None, 'output_file': None}
    remaining = []
    i = 0
    while i < len(args):
        if args[i] == '--json':
            flags['format'] = 'json'; i += 1
        elif args[i] == '--pretty':
            flags['format'] = 'pretty'; i += 1
        elif args[i] == '--text':
            flags['format'] = 'text'; i += 1
        elif args[i] == '--table':
            flags['format'] = 'table'; i += 1
        elif args[i] == '--output' and i + 1 < len(args):
            flags['output_file'] = args[i + 1]; i += 2
        elif args[i].startswith('--output='):
            flags['output_file'] = args[i].split('=', 1)[1]; i += 1
        else:
            remaining.append(args[i]); i += 1
    if flags['format'] is None:
        flags['format'] = 'pretty' if hasattr(sys.stdout, 'isatty') and sys.stdout.isatty() else 'json'
    return flags, remaining


# ==================== Meeting organizer (classification + planning) ====================
# Source spec: references/system-prompt.md (10 standard categories). Two practical
# extras (法务/面试) are included so common business folders are reused rather than
# dumped into 🗂其他. Order = priority for full-title keyword fallback.
_CATEGORY_SPEC = [
    {"key": "产品", "icon": "📦", "name": "产品", "aliases": ["产品", "product", "prd"],
     "keywords": ["产品", "需求", "功能", "版本", "迭代", "demo", "原型", "roadmap", "feature"],
     "color": "#EAEBFF"},
    {"key": "市场", "icon": "📣", "name": "市场", "aliases": ["市场", "营销", "marketing", "推广", "品牌"],
     "keywords": ["市场", "营销", "推广", "品牌", "marketing", "campaign", "launch", "early-bird"],
     "color": "#E8F5E9"},
    {"key": "销售", "icon": "💼", "name": "销售", "aliases": ["销售", "商务", "售前"],
     "keywords": ["销售", "商务", "客户", "合同", "定价", "报价", "deal", "budget", "合作洽谈"],
     "color": "#FFF4E5"},
    {"key": "战略", "icon": "🎯", "name": "战略", "aliases": ["战略", "规划", "愿景"],
     "keywords": ["战略", "规划", "方向", "愿景", "全球化", "布局", "expansion", "投资", "金融"],
     "color": "#F3E8FF"},
    {"key": "旅游", "icon": "🏝", "name": "旅游", "aliases": ["旅游", "出行"],
     "keywords": ["旅游", "出行", "酒店", "机票", "行程", "travel", "trip", "vacation", "度假"],
     "color": "#E0F7FA"},
    {"key": "教育", "icon": "📚", "name": "教育", "aliases": ["教育", "家校", "培训"],
     "keywords": ["教育", "培训", "课程", "学习", "家校", "学生", "纪律", "亲子", "workshop"],
     "color": "#FFF8E1"},
    {"key": "技术", "icon": "🔬", "name": "技术", "aliases": ["技术", "研发"],
     "keywords": ["技术", "研发", "架构", "算法", "开发", "engineering", "世界模型", "游戏技术"],
     "color": "#E3F2FD"},
    {"key": "设计", "icon": "🎨", "name": "设计", "aliases": ["设计"],
     "keywords": ["设计", "视觉", "交互", "材质", "design", "figma"],
     "color": "#FCE4EC"},
    {"key": "客服", "icon": "🎧", "name": "客服", "aliases": ["客服", "售后"],
     "keywords": ["客服", "支持", "售后", "反馈", "complaint", "support", "ticket"],
     "color": "#EDE7F6"},
    {"key": "运营", "icon": "⚙", "name": "运营", "aliases": ["运营"],
     "keywords": ["运营", "增长", "留存", "活跃", "operation", "growth", "metric"],
     "color": "#ECEFF1"},
    # practical extras — reuse existing user folders when present
    {"key": "法务", "icon": "⚖️", "name": "法务", "aliases": ["法务", "合规"],
     "keywords": ["法务", "合规", "知识产权"], "color": "#E0E0E0"},
    {"key": "面试", "icon": "👤", "name": "面试", "aliases": ["面试", "招聘"],
     "keywords": ["面试", "候选人", "招聘", "岗位"], "color": "#F1F8E9"},
]
_MAX_TARGET_FOLDERS = 10
_OVERFLOW_KEY = "其他"
_OVERFLOW_FOLDER_NAME = "🗂其他"

# Strips a leading icon/emoji/symbol run so "🏗️产品研发" -> "产品研发".
_FOLDER_NAME_STRIP_RE = re.compile(r'^[^一-鿿A-Za-z]+')


def _normalize_folder_name(name: str) -> str:
    """Strip leading icon/emoji run and whitespace from a folder name."""
    s = (name or '').strip()
    return _FOLDER_NAME_STRIP_RE.sub('', s).strip()


def _split_title_prefix(title: str) -> str:
    """Return the declared-category text before the first full/half-width colon."""
    t = (title or '').strip()
    for sep in ('：', ':'):
        if sep in t:
            return t.split(sep, 1)[0].strip()
    return ''


def _category_by_key(key: str):
    for c in _CATEGORY_SPEC:
        if c['key'] == key:
            return c
    return None


def _classify_meeting_title(title: str) -> str:
    """Classify a meeting title to a category key.

    Prefix-primary (the word(s) before ：declare the category), then a full-title
    keyword fallback in spec priority order. Unknown -> 其他.
    """
    t = (title or '').strip()
    if not t:
        return _OVERFLOW_KEY
    prefix = _split_title_prefix(t)
    if prefix:
        plow = prefix.lower()
        for cat in _CATEGORY_SPEC:
            for al in cat['aliases']:
                if al and al.lower() in plow:
                    return cat['key']
    tlow = t.lower()
    for cat in _CATEGORY_SPEC:
        for kw in cat['keywords']:
            if kw and kw.lower() in tlow:
                return cat['key']
    return _OVERFLOW_KEY


def _match_existing_folder(category_key: str, existing_folders: list) -> Optional[str]:
    """Return the id of an existing folder to reuse for a category, or None.

    Matching uses the category's primary name only (exact > startswith > contains)
    against each folder's icon-stripped name — this avoids keyword false positives
    (e.g. 技术 must not reuse 产品研发). Ties favor the longest (most specific) name.
    """
    if category_key == _OVERFLOW_KEY:
        primary = '其他'
    else:
        cat = _category_by_key(category_key)
        primary = cat['name'] if cat else None
    if not primary:
        return None

    candidates = []  # (score, name_len, folder_id)
    for f in (existing_folders or []):
        fid = f.get('id')
        norm = _normalize_folder_name(f.get('folderName') or f.get('name') or '')
        if not norm or not fid:
            continue
        if norm == primary:
            candidates.append((3, len(norm), fid))
        elif norm.startswith(primary):
            candidates.append((2, len(norm), fid))
        elif primary in norm:
            candidates.append((1, len(norm), fid))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c[0], -c[1]))
    return candidates[0][2]


def build_organize_plan(meetings: list, existing_folders: list,
                        include_no_conclusion: bool = False) -> dict:
    """Build a folder-organization plan (pure, no network).

    Splits meetings with/without a conclusion, classifies the eligible ones into
    categories, reuses matching existing folders (else proposes new ones), caps
    distinct categories at _MAX_TARGET_FOLDERS (overflow -> 🗂其他), and marks
    meetings already in their target folder as already-organized.
    """
    valid, no_concl = [], []
    for m in (meetings or []):
        if not isinstance(m, dict):
            continue
        cid = m.get('conclusionId')
        has_concl = bool(str(cid).strip()) if cid is not None else False
        (valid if has_concl else no_concl).append(m)

    pool = valid if not include_no_conclusion else (valid + no_concl)

    groups = {}
    for m in pool:
        title = m.get('originalFilename') or m.get('filename') or ''
        groups.setdefault(_classify_meeting_title(title), []).append(m)

    # Cap distinct categories; fold the smallest into 🗂其他.
    if len(groups) > _MAX_TARGET_FOLDERS:
        ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        kept = dict(ordered[:_MAX_TARGET_FOLDERS])
        other_items = list(kept.get(_OVERFLOW_KEY, []))
        for cat, items in ordered[_MAX_TARGET_FOLDERS:]:
            if cat == _OVERFLOW_KEY:
                other_items = list(items) + other_items
            else:
                other_items.extend(items)
        groups = kept
        if other_items:
            groups[_OVERFLOW_KEY] = other_items

    folders = []
    to_move = 0
    already = 0
    for cat, items in groups.items():
        reuse_id = _match_existing_folder(cat, existing_folders)
        if reuse_id:
            action, target_id = 'REUSE', reuse_id
            target_name = next((f.get('folderName') for f in existing_folders
                                if f.get('id') == reuse_id), None) or cat
            color = None
        else:
            action, target_id = 'CREATE', None
            if cat == _OVERFLOW_KEY:
                target_name, color = _OVERFLOW_FOLDER_NAME, '#EEEEEE'
            else:
                spec = _category_by_key(cat)
                target_name = f"{spec['icon']}{spec['name']}" if spec else _OVERFLOW_FOLDER_NAME
                color = spec['color'] if spec else '#EEEEEE'

        move_ids = []
        for m in items:
            if target_id and str(m.get('folderId') or '') == str(target_id):
                already += 1
            else:
                mid = m.get('id')
                if mid is not None:
                    move_ids.append(str(mid))
                    to_move += 1

        folders.append({
            'category': cat,
            'target_folder_id': target_id,
            'target_folder_name': target_name,
            'action': action,
            'color': color,
            'meeting_ids': move_ids,
            'meeting_count': len(items),
            'is_overflow': cat == _OVERFLOW_KEY,
        })

    folders.sort(key=lambda f: (-f['meeting_count'], f['category']))

    return {
        'mode': 'plan',
        'code': 200,
        'scope': {'include_no_conclusion': include_no_conclusion},
        'totals': {
            'scanned': len(valid) + len(no_concl),
            'with_conclusion': len(valid),
            'no_conclusion': len(no_concl),
            'already_organized': already,
            'to_move': to_move,
        },
        'folders': folders,
        'skipped_no_conclusion': ([] if include_no_conclusion else
                                   [{'id': m.get('id'),
                                     'title': m.get('originalFilename') or m.get('filename') or ''}
                                    for m in no_concl]),
    }


# Max files per change_folder call — it is a GET with repeated fileIds= params,
# so chunk to keep the request URL bounded.
_MOVE_CHUNK = 50


def _extract_folder_id(resp) -> Optional[str]:
    """Tolerantly extract a newly-created folder id from a create_folder response.

    The API may return the id as a scalar, as data.id, or nested; handle all.
    """
    if not isinstance(resp, dict):
        return None
    data = resp.get('data')
    if isinstance(data, dict):
        return data.get('id') or data.get('folderId')
    if isinstance(data, str) and data:
        return data
    return resp.get('id') or resp.get('folderId')


def _format_organize_text(result: dict) -> str:
    """Render an organize plan (dry-run) or execute result as human-readable text."""
    if not isinstance(result, dict):
        return str(result)
    mode = result.get('mode', 'plan')

    if mode == 'execute':
        r = result.get('results', {}) or {}
        created = r.get('folders_created', []) or []
        lines = ["Organize: executed",
                 f"  Folders created: {len(created)}" +
                 (f" ({', '.join(c.get('name', '') for c in created)})" if created else ""),
                 f"  Folders reused: {r.get('folders_reused', 0)}",
                 f"  Moved: {r.get('moves_succeeded', 0)}/{r.get('moves_attempted', 0)} meetings",
                 f"  Already in place: {r.get('already_in_place', 0)}"]
        failed = r.get('folders_failed', []) or []
        if failed:
            lines.append(f"  Folders FAILED to create: {len(failed)} "
                         f"({', '.join(f.get('name', '') for f in failed)} — re-run once the server recovers)")
        errs = r.get('errors', []) or []
        if errs:
            lines.append(f"  Errors: {len(errs)} (e.g. {errs[0].get('error')})")
        skipped = result.get('skipped_no_conclusion', 0)
        if skipped:
            lines.append(f"  Skipped (no conclusion): {skipped}")
        return '\n'.join(lines)

    # plan / dry-run
    totals = result.get('totals', {}) or {}
    lines = [
        "Organize plan (dry-run — nothing changed):",
        f"  Scanned: {totals.get('scanned', 0)}  "
        f"(with conclusion: {totals.get('with_conclusion', 0)}, no conclusion: {totals.get('no_conclusion', 0)})",
        f"  To move: {totals.get('to_move', 0)}  Already organized: {totals.get('already_organized', 0)}",
        "",
        "Folders:",
    ]
    for f in result.get('folders', []) or []:
        tag = 'OVERFLOW' if f.get('is_overflow') else ('REUSE' if f.get('action') == 'REUSE' else 'CREATE')
        name = f.get('target_folder_name') or f.get('category')
        lines.append(f"  [{tag}] {name} — {f.get('meeting_count', 0)} meeting(s)")
        if f.get('_error'):
            lines.append(f"      ! error: {f['_error']}")
    skipped = result.get('skipped_no_conclusion', []) or []
    if skipped:
        lines.append("")
        lines.append(f"Skipped (no conclusion, not moved): {len(skipped)}")
        for s in skipped[:10]:
            lines.append(f"  - {s.get('title') or s.get('id')}")
        if len(skipped) > 10:
            lines.append(f"  ... and {len(skipped) - 10} more")
    return '\n'.join(lines)


def _format_text(result: dict, command: str) -> str:
    """为常用命令生成人类可读文本摘要。"""
    data = result.get('data') if isinstance(result, dict) else result
    if command == 'getCurrentCustomer':
        d = data if isinstance(data, dict) else {}
        lines = [f"Name: {d.get('nickname') or d.get('name') or 'N/A'}",
                 f"Phone: {d.get('phone') or 'N/A'}",
                 f"Member Level: {d.get('memberLevel') or 'N/A'}",
                 f"Points: {d.get('pointsAmount', 0)} (used: {d.get('usedPointsAmount', 0)})"]
        return '\n'.join(lines)
    if command == 'refreshMembership':
        d = data if isinstance(data, dict) else {}
        return f"Member Level: {d.get('memberLevel') or 'N/A'}\nQuota: {d.get('quota') or 'N/A'}"
    if command == 'organizeMeetings':
        return _format_organize_text(result)
    if command in ('listFilesByTimeRange', 'listFilesByMonth', 'listFilesByWeek',
                   'listFilesByRange', 'listFiles', 'listFilesPaged', 'searchFiles'):
        items = data if isinstance(data, list) else []
        if not items:
            return 'No files found.'
        lines = [f'Total: {len(items)} file(s)']
        for item in items[:20]:
            if isinstance(item, dict):
                name = item.get('originalFilename') or item.get('name') or item.get('id', '?')
                created = item.get('createTime') or ''
                lines.append(f'  [{item.get("id", "?")}] {name}  {created}')
        if len(items) > 20:
            lines.append(f'  ... and {len(items) - 20} more')
        return '\n'.join(lines)
    if command == 'getConclusion':
        if isinstance(data, list):
            return '\n\n'.join(str(c.get('content', c)) if isinstance(c, dict) else str(c) for c in data)
        return str(data)
    if command == 'getTranscriptionRecord':
        if isinstance(data, list):
            lines = []
            for entry in data:
                if isinstance(entry, dict):
                    speaker = entry.get('speakerName') or f"Speaker {entry.get('speakerId', '?')}"
                    text = entry.get('text', '')
                    lines.append(f'{speaker}: {text}')
            return '\n'.join(lines) if lines else str(data)
        return str(data)
    if command == 'getTranscriptionText':
        return str(data or '')
    if command == 'getOutline':
        return str(data) if data else 'No outline available.'
    if command == 'getFileInfo':
        d = data if isinstance(data, dict) else {}
        return '\n'.join([
            f"ID: {d.get('id', 'N/A')}",
            f"Name: {d.get('originalFilename') or d.get('name', 'N/A')}",
            f"Size: {d.get('fileSize', 'N/A')}",
            f"Created: {d.get('createTime', 'N/A')}",
            f"Status: {d.get('status', 'N/A')}",
        ])
    if command == 'listFolders':
        items = data if isinstance(data, list) else []
        if not items:
            return 'No folders found.'
        lines = []
        for item in items:
            if isinstance(item, dict):
                lines.append(f'  [{item.get("id", "?")}] {item.get("folderName") or item.get("name") or "?"}')
        return '\n'.join(lines)
    if command == 'listTodos':
        items = data if isinstance(data, list) else []
        if not items:
            return 'No todos found.'
        lines = []
        for item in items:
            if isinstance(item, dict):
                done = '✓' if item.get('isCompleted') else '○'
                content = item.get('todoContent', '')
                lines.append(f'  {done} {content}')
        return '\n'.join(lines)
    if command == 'getMyDevices':
        items = data if isinstance(data, list) else []
        if not items:
            return 'No devices found.'
        lines = []
        for item in items:
            if isinstance(item, dict):
                sn = item.get('serialNumber') or item.get('authSn') or 'N/A'
                lines.append(f'  [{item.get("id", "?")}] SN: {sn}  {item.get("deviceName", "")}')
        return '\n'.join(lines)
    if command == 'getAiModels':
        items = data if isinstance(data, list) else (data.get('list', []) if isinstance(data, dict) else [])
        if not items:
            return 'No models found.'
        lines = []
        for item in items:
            if isinstance(item, dict):
                enabled = '✓' if item.get('enabled') else '✗'
                lines.append(f'  [{item.get("id", "?")}] {enabled} {item.get("name", "?")}')
        return '\n'.join(lines)
    if isinstance(data, dict) or (data is None and isinstance(result, dict)):
        d = data if isinstance(data, dict) else result
        return '\n'.join(f'{k}: {v}' for k, v in d.items())
    return json.dumps(result, ensure_ascii=False, indent=2)


def _format_table(result: dict, command: str) -> str:
    """为列表型结果生成 ASCII 表格。"""
    data = result.get('data') if isinstance(result, dict) else result
    list_commands = {
        'listFilesByTimeRange': [('ID', 'id'), ('Name', 'originalFilename'), ('Created', 'createTime')],
        'listFilesByMonth': [('ID', 'id'), ('Name', 'originalFilename'), ('Created', 'createTime')],
        'listFilesByWeek': [('ID', 'id'), ('Name', 'originalFilename'), ('Created', 'createTime')],
        'listFilesByRange': [('ID', 'id'), ('Name', 'originalFilename'), ('Created', 'createTime')],
        'listFiles': [('ID', 'id'), ('Name', 'originalFilename'), ('Created', 'createTime')],
        'listFilesPaged': [('ID', 'id'), ('Name', 'originalFilename'), ('Created', 'createTime')],
        'searchFiles': [('ID', 'id'), ('Name', 'originalFilename'), ('Created', 'createTime')],
        'listFolders': [('ID', 'id'), ('Name', 'folderName')],
        'listTodos': [('Done', 'isCompleted'), ('Content', 'todoContent'), ('Deadline', 'expectedCompleteTime')],
        'getMyDevices': [('ID', 'id'), ('SN', 'serialNumber'), ('Name', 'deviceName')],
        'getAiModels': [('ID', 'id'), ('Name', 'name'), ('Enabled', 'enabled')],
    }
    if command not in list_commands:
        return _format_text(result, command)
    columns = list_commands[command]
    items = data if isinstance(data, list) else []
    if not items:
        return 'No data.'
    headers = [c[0] for c in columns]
    keys = [c[1] for c in columns]
    rows = []
    # 列宽上限：ID 列不截断（下游 summary/transcript/outline/info 需要完整可复制的 ID），
    # 其余列超长时截断并加省略号，避免静默丢失信息。
    col_limit = {}
    for j, h in enumerate(headers):
        col_limit[j] = 0 if h == 'ID' else 40
    for item in items:
        if not isinstance(item, dict):
            continue
        row = []
        for j, k in enumerate(keys):
            v = item.get(k, '')
            if k == 'isCompleted':
                v = '✓' if v else '○'
            elif k == 'enabled':
                v = '✓' if v else '✗'
            else:
                s = str(v)
                limit = col_limit.get(j, 40)
                if limit and len(s) > limit:
                    s = s[:limit - 3] + '...'
                v = s
            row.append(str(v))
        rows.append(row)
    widths = [len(h) for h in headers]
    for row in rows:
        for j, cell in enumerate(row):
            widths[j] = max(widths[j], len(cell))
    def _fmt_row(cells):
        return '| ' + ' | '.join(c.ljust(widths[j]) for j, c in enumerate(cells)) + ' |'
    sep = '+-' + '-+-'.join('-' * w for w in widths) + '-+'
    lines = [sep, _fmt_row(headers), sep]
    for row in rows:
        lines.append(_fmt_row(row))
    lines.append(sep)
    return '\n'.join(lines)


def _format_output(result, command: str, flags: dict) -> None:
    """根据 flags 格式化并输出结果。"""
    fmt = flags.get('format', 'json')
    output_file = flags.get('output_file')
    if fmt == 'text':
        text = _format_text(result, command)
    elif fmt == 'table':
        text = _format_table(result, command)
    elif fmt == 'pretty':
        text = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        text = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(text)
            f.write('\n')
        print(f"Output saved to: {output_file}", file=sys.stderr)
    else:
        print(text)


def _get_user_config_dir() -> Path:
    """返回 ~/.lynse 目录路径。"""
    return Path.home() / '.lynse'


def _load_user_config() -> dict:
    """从 ~/.lynse/config.json 加载用户级配置。"""
    config_file = _get_user_config_dir() / 'config.json'
    if not config_file.exists():
        return {}
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _load_install_env(config_file: Optional[str] = None) -> None:
    """加载安装目录下的 .env 到 os.environ（最低优先级的凭据来源）。

    调用方若需要特定优先级，必须在本函数之前先捕获 shell 环境变量，因为本
    函数会覆写 os.environ。
    """
    if config_file is None:
        config_file = str(Path(__file__).parent.resolve() / '.env')

    config_path = Path(config_file)
    if not config_path.exists():
        return

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                if key in ('LYNSE_API_HOST', 'LYNSE_API_KEY', 'LYNSE_OWNER_ID'):
                    os.environ[key] = value
    except Exception as e:
        print(f"Warning: failed to read config file: {e}", file=sys.stderr)


def _resolve_api_credentials(
    param_host: Optional[str] = None,
    param_key: Optional[str] = None,
    install_env_path: Optional[str] = None,
    user_config: Optional[Dict[str, Any]] = None,
) -> tuple:
    """按统一优先级解析 (api_host, api_key, key_source)：

        显式参数  >  shell 环境变量  >  用户配置 (~/.lynse/config.json)  >  安装 .env

    过时的安装 .env 绝不能覆盖用户通过 `auth login` 保存的密钥或显式 shell 导出，
    否则 token 刷新会使用错误的密钥并以误导性的 "API Key authentication failed" 失败。

    `key_source` 取值 'param' | 'shell' | 'config' | 'env' | None，用于展示来源。
    """
    ucfg = user_config if user_config is not None else _load_user_config()
    # 在安装 .env 写入 os.environ 之前先捕获 shell 提供的值
    shell_host = os.environ.get('LYNSE_API_HOST')
    shell_key = os.environ.get('LYNSE_API_KEY')
    _load_install_env(install_env_path)
    env_host = os.environ.get('LYNSE_API_HOST')
    env_key = os.environ.get('LYNSE_API_KEY')

    if param_key:
        key, key_source = param_key, 'param'
    elif shell_key:
        key, key_source = shell_key, 'shell'
    elif ucfg.get('api_key'):
        key, key_source = ucfg.get('api_key'), 'config'
    else:
        key, key_source = env_key, 'env'

    if param_host:
        host = param_host
    elif shell_host:
        host = shell_host
    elif ucfg.get('api_host'):
        host = ucfg.get('api_host')
    else:
        host = env_host

    return host, key, key_source


class LynseAPIError(Exception):
    """API 调用异常"""
    def __init__(self, message: str, http_code: int = None, code: int = None):
        self.message = message
        self.http_code = http_code
        self.code = code
        super().__init__(self.message)


class LynseAPI:
    """Lynse API 客户端 - 处理认证和 API 调用"""

    # HTTP 错误码处理映射
    HTTP_ERROR_MESSAGES = {
        401: "Token expired, refreshing automatically...",
        403: "Insufficient permissions. Contact your administrator to upgrade.",
        404: "The requested resource was not found.",
        429: "Rate limit exceeded. Please wait 60 seconds and try again.",
        500: "Server temporarily unavailable. Please try again later.",
        502: "Server temporarily unavailable. Please try again later.",
        503: "Server temporarily unavailable. Please try again later.",
    }

    def __init__(self, api_host: str = None, api_key: str = None, config_file: str = None):
        """
        初始化 API 客户端

        Args:
            api_host: API 服务器地址
            api_key: API Key
            config_file: 配置文件路径（默认当前目录 .env）
        """
        # 1. 解析凭据（统一优先级：参数 > shell 环境变量 > 用户配置 ~/.lynse/config.json > 安装 .env）
        self._user_config = _load_user_config()
        self.api_host, self.api_key, _ = _resolve_api_credentials(
            param_host=api_host,
            param_key=api_key,
            install_env_path=config_file,
            user_config=self._user_config,
        )
        self.owner_id = os.environ.get('LYNSE_OWNER_ID')

        # 3. 验证配置
        if not self.api_host:
            raise LynseAPIError(
                "LYNSE_API_HOST is not configured.\n"
                "Run 'lynse auth login --api-key <key> --host <url>' or set it in .env"
            )
        if not self.api_key:
            if not os.environ.get("LYNSE_ACCESS_TOKEN", "").strip():
                raise LynseAPIError(
                    "LYNSE_API_KEY is not configured.\n"
                    "Run 'lynse auth login --api-key <key>' or set it in .env"
                )
            self.api_key = ""

        # 4. Token 缓存（优先 ~/.lynse/tokens.json）
        env_token = os.environ.get('LYNSE_TOKEN_FILE')
        if env_token:
            self.token_file = Path(env_token)
        else:
            user_token = _get_user_config_dir() / 'tokens.json'
            legacy_token = Path(__file__).parent.resolve() / '.token_cache'
            if legacy_token.exists() and not user_token.exists():
                self.token_file = legacy_token
            else:
                self.token_file = user_token
        self._access_token: Optional[str] = None

    def _load_config(self, config_file: str = None):
        """从 .env 文件加载配置（委托给模块级 _load_install_env，保留向后兼容）。"""
        _load_install_env(config_file)

    def _validate_token(self, token: str) -> bool:
        """验证 Token 格式（JWT 基本格式）"""
        if not token:
            return False
        # JWT 格式：三段 base64 由.连接
        pattern = r'^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$'
        return bool(re.match(pattern, token))

    def _strip_bearer_prefix(self, token: str) -> str:
        cleaned = (token or "").strip()
        if cleaned.lower().startswith("bearer "):
            return cleaned[7:].strip()
        return cleaned

    def _is_http_injected_token(self, token: str) -> bool:
        """HTTP 请求体注入的 accessToken（无 TUI 式 dk_ API Key）。"""
        forced = (os.environ.get("LYNSE_ACCESS_TOKEN") or "").strip()
        if not forced:
            return False
        return self._strip_bearer_prefix(forced) == self._strip_bearer_prefix(token)

    def _should_use_bearer_authorization(self, token: str) -> bool:
        """默认裸 JWT（与 Lynse SKILL/TUI 一致）；仅 LYNSE_AUTH_USE_BEARER=1 时加 Bearer。"""
        pref = os.environ.get("LYNSE_AUTH_USE_BEARER", "").strip().lower()
        return pref in ("1", "true", "yes")

    def _resolve_x_api_key(self, token: str) -> str:
        """TUI 路径带 X-API-Key；HTTP 仅 token 时不发（服务端确认可省略）。"""
        if self._is_http_injected_token(token) and not self.api_key:
            return ""
        if self.api_key:
            return self.api_key
        return (os.environ.get("LYNSE_API_KEY") or "").strip()

    def _build_auth_headers_with_mode(
        self,
        token: str,
        *,
        use_bearer: bool,
        extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        cleaned = self._strip_bearer_prefix(token)
        auth_value = f"Bearer {cleaned}" if use_bearer and cleaned else cleaned
        headers = {
            "Authorization": auth_value,
            "Content-Type": "application/json",
        }
        x_api_key = self._resolve_x_api_key(token)
        if x_api_key:
            headers["X-API-Key"] = x_api_key
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _lynse_debug_enabled() -> bool:
        return os.environ.get("LYNSE_HTTP_DEBUG", "").strip().lower() in ("1", "true", "yes")

    def _mask_headers_for_log(self, headers: Dict[str, str]) -> Dict[str, str]:
        safe = dict(headers)
        for key in list(safe.keys()):
            if key.lower() in ("authorization", "x-api-key"):
                safe[key] = "[REDACTED]"
        return safe

    @staticmethod
    def _destination_for_log(url: str) -> str:
        """Return only the request origin, never a path, query, or user info."""
        try:
            parsed = urlsplit(url)
            if not parsed.scheme or not parsed.hostname:
                return "[invalid URL]"
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{parsed.hostname}{port}"
        except ValueError:
            return "[invalid URL]"

    def _log_lynse_request(
        self,
        *,
        method: str,
        url: str,
        headers: Dict[str, str],
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        note: str = "",
    ) -> None:
        if not self._lynse_debug_enabled():
            return
        payload = {
            "note": note or None,
            "method": method,
            "destination": self._destination_for_log(url),
            "headers": self._mask_headers_for_log(headers),
            "param_names": sorted(str(key) for key in params) if params else None,
            "json_fields": sorted(str(key) for key in json_data) if json_data else None,
            "http_injected_token": self._is_http_injected_token(
                self._strip_bearer_prefix(headers.get("Authorization", ""))
            ),
        }
        print(
            f"[{_lynse_log_ts()}] [lynse-cli] >>> request {json.dumps(payload, ensure_ascii=False)}",
            file=sys.stderr,
            flush=True,
        )

    def _log_lynse_response(
        self,
        *,
        method: str,
        url: str,
        status_code: int,
        data: Any,
        text_preview: str = "",
    ) -> None:
        if not self._lynse_debug_enabled():
            return
        summary: Dict[str, Any] = {
            "method": method,
            "destination": self._destination_for_log(url),
            "http_status": status_code,
        }
        if isinstance(data, dict):
            summary["business_code"] = data.get("code")
            if "data" in data:
                inner = data.get("data")
                if isinstance(inner, list):
                    summary["data_count"] = len(inner)
                elif isinstance(inner, dict):
                    summary["data_keys"] = list(inner.keys())[:20]
        elif text_preview:
            summary["raw_length"] = len(text_preview)
        print(
            f"[{_lynse_log_ts()}] [lynse-cli] <<< response {json.dumps(summary, ensure_ascii=False)}",
            file=sys.stderr,
            flush=True,
        )

    def _build_auth_headers(self, token: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        return self._build_auth_headers_with_mode(
            token,
            use_bearer=self._should_use_bearer_authorization(token),
            extra=extra,
        )

    def _get_cached_token(self) -> Optional[str]:
        """从缓存获取 Token"""
        if not self.token_file.exists():
            return None

        try:
            # 检查文件权限（Unix 系统）
            if os.name != 'nt':
                mode = self.token_file.stat().st_mode & 0o777
                if mode != 0o600:
                    self.token_file.chmod(0o600)

            token = self.token_file.read_text(encoding='utf-8').strip()
            if self._validate_token(token):
                return token
        except Exception:
            pass

        return None

    def _save_token(self, token: str):
        """保存 Token 到缓存文件"""
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(token, encoding='utf-8')

            # 设置文件权限为 600（仅所有者可读写）
            if os.name != 'nt':
                self.token_file.chmod(0o600)
        except Exception as e:
            print(f"Warning: failed to save token: {e}", file=sys.stderr)

    def _refresh_token(self) -> str:
        """使用 API Key 刷新 Token（瞬时错误自动重试，错误信息准确归类）。"""
        url = f"{self.api_host}/api/auth/apikey/token"
        headers = {
            'X-API-Key': self.api_key,
            'Content-Type': 'application/json'
        }

        # 瞬时错误（服务端 5xx / 429 / 网络）重试，避免一次抖动就整体失败。
        # 401/403 表示密钥被拒，不重试；其余非 200 视为可重试的瞬时错误。
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            self._log_lynse_request(
                method="POST",
                url=url,
                headers=headers,
                note=f"exchange_token attempt {attempt}/{max_attempts}",
            )
            try:
                response = requests.post(url, headers=headers, timeout=30)
            except requests.RequestException as e:
                if attempt < max_attempts:
                    time.sleep(0.5 * attempt)
                    continue
                raise LynseAPIError(
                    f"Network error during token exchange (retried {max_attempts}x): {e}"
                )

            if response.status_code == 200:
                try:
                    data = response.json()
                except json.JSONDecodeError:
                    raise LynseAPIError(
                        "API Key authentication failed: server returned invalid response format"
                    )
                self._log_lynse_response(
                    method="POST", url=url, status_code=response.status_code, data=data
                )
                data_payload = data.get('data') if isinstance(data, dict) else None
                data_payload = data_payload if isinstance(data_payload, dict) else {}
                access_token = data_payload.get('accessToken') or data.get('accessToken')

                if not access_token or access_token == 'null':
                    raise LynseAPIError("API Key authentication failed: returned token is empty")

                if not self._validate_token(access_token):
                    raise LynseAPIError(
                        "API Key authentication failed: returned token format is invalid"
                    )

                self._save_token(access_token)
                return access_token

            # 非 200：区分“密钥被拒”与“瞬时服务端错误”
            if response.status_code in (401, 403):
                raise LynseAPIError(
                    f"API key rejected by server (HTTP {response.status_code}). "
                    "Check your LYNSE_API_KEY (get it from the system console).",
                    http_code=response.status_code,
                )

            if attempt < max_attempts:
                time.sleep(0.5 * attempt)
                continue

            raise LynseAPIError(
                f"Token exchange failed: server returned HTTP {response.status_code} "
                f"(transient server error, retried {max_attempts}x — please try again).",
                http_code=response.status_code,
            )

        raise LynseAPIError("Token exchange failed after retries.")

    def _get_token(self, refresh: bool = False) -> str:
        """获取有效 Token，支持自动刷新"""
        forced = (os.environ.get("LYNSE_ACCESS_TOKEN") or "").strip()
        if forced and not refresh:
            if self._validate_token(forced):
                self._access_token = forced
                return forced

        if not refresh:
            cached = self._get_cached_token()
            if cached:
                # 验证 Token 是否有效
                try:
                    test_url = f"{self.api_host}/api/business/customer/current"
                    test_headers = self._build_auth_headers(cached)
                    test_response = requests.get(test_url, headers=test_headers, timeout=10)
                    if test_response.status_code == 200:
                        try:
                            test_data = test_response.json()
                        except json.JSONDecodeError:
                            test_data = {}
                        if test_data.get('code') == 200:
                            self._access_token = cached
                            return cached
                except Exception:
                    pass  # Token 无效，刷新

        # 刷新 Token
        self._access_token = self._refresh_token()
        return self._access_token

    def _check_http_error(self, http_code: int, response_text: str):
        """检查 HTTP 错误并抛出异常"""
        if http_code == 200:
            return

        message = self.HTTP_ERROR_MESSAGES.get(http_code)
        if message:
            raise LynseAPIError(message, http_code=http_code)

        # 未知错误
        error_msg = f"API 请求失败 (HTTP {http_code})"
        if response_text:
            error_msg += f" - {response_text}"
        raise LynseAPIError(error_msg, http_code=http_code)

    def _check_owner_id(self, token: str):
        """验证 Owner ID"""
        if not self.owner_id:
            return

        try:
            url = f"{self.api_host}/api/business/customer/current"
            headers = self._build_auth_headers(token)
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                data_payload = data.get('data') if isinstance(data, dict) else None
                data_payload = data_payload if isinstance(data_payload, dict) else {}
                current_id = data_payload.get('id') or data.get('id')
                if current_id and current_id != self.owner_id:
                    raise LynseAPIError("Access denied: this is a private account.")
        except LynseAPIError:
            raise
        except Exception:
            pass  # 忽略验证错误，继续执行

    def _sanitize_param(self, param: str, allow_type: str = 'safe') -> str:
        """清理参数，防止注入"""
        if allow_type == 'digit':
            return re.sub(r'[^0-9]', '', str(param))
        elif allow_type == 'safe':
            # 移除危险字符
            return re.sub(r'[;$`]', '', str(param)).replace('..', '')
        return str(param)

    def _request(self, method: str, path: str,
                 headers: Dict[str, str] = None,
                 params: Dict[str, Any] = None,
                 json_data: Dict[str, Any] = None,
                 retry_count: int = 0,
                 _force_auth_mode: Optional[bool] = None) -> Dict[str, Any]:
        """
        发送 API 请求

        Args:
            method: HTTP 方法 (GET/POST/DELETE 等)
            path: API 路径（不含 api_host）
            headers: 额外请求头
            params: URL 参数
            json_data: JSON Body 数据
            retry_count: 重试次数

        Returns:
            解析后的 JSON 响应
        """
        url = f"{self.api_host}{path}"

        # 获取 Token
        token = self._get_token()

        # 构建请求头（HTTP 注入的 accessToken 默认带 Bearer 前缀）
        if _force_auth_mode is None:
            request_headers = self._build_auth_headers(token, headers)
        else:
            request_headers = self._build_auth_headers_with_mode(
                token,
                use_bearer=_force_auth_mode,
                extra=headers,
            )
        self._log_lynse_request(
            method=method,
            url=url,
            headers=request_headers,
            params=params,
            json_data=json_data,
            note=f"business_api retry={retry_count}",
        )

        try:
            response = requests.request(
                method,
                url,
                headers=request_headers,
                params=params,
                json=json_data,
                timeout=30
            )

            # Retry transient server errors (429 / 5xx) so a momentary server
            # hiccup doesn't fail the whole request.
            if response.status_code in (429, 500, 502, 503, 504) and retry_count < 2:
                time.sleep(0.5 * (retry_count + 1))
                return self._request(
                    method, path, headers=headers, params=params,
                    json_data=json_data, retry_count=retry_count + 1,
                    _force_auth_mode=_force_auth_mode,
                )

            # 检查 HTTP 错误
            self._check_http_error(response.status_code, response.text)

            # 解析响应
            try:
                data = response.json()
            except json.JSONDecodeError:
                data = {'raw': response.text}

            self._log_lynse_response(
                method=method,
                url=url,
                status_code=response.status_code,
                data=data,
                text_preview=response.text,
            )

            # 检查业务错误码
            code = data.get('code')
            if code and code != 200:
                message = data.get('message') or data.get('msg') or data.get('raw') or 'unknown error'
                # Bearer / 裸 JWT 不一致时自动切换一次（兼容不同 Lynse 网关约定）
                if retry_count == 0 and code in (2000, 401, 403):
                    use_bearer_now = self._should_use_bearer_authorization(token)
                    alt_headers = self._build_auth_headers_with_mode(
                        token,
                        use_bearer=not use_bearer_now,
                        extra=headers,
                    )
                    if alt_headers.get("Authorization") != request_headers.get("Authorization"):
                        return self._request(
                            method,
                            path,
                            headers=headers,
                            params=params,
                            json_data=json_data,
                            retry_count=retry_count + 1,
                            _force_auth_mode=not use_bearer_now,
                        )
                raise LynseAPIError(f"API error: {message}", code=code)

            return data

        except LynseAPIError:
            raise
        except requests.RequestException as e:
            if retry_count < 2:
                # 网络错误，重试
                return self._request(method, path, headers, params, json_data, retry_count + 1)
            raise LynseAPIError(f"网络错误：{e}")

    # ==================== 认证辅助 ====================

    def auth_login(self, api_key: str, api_host: str = None) -> str:
        """用 API Key 换取 access token 并缓存，返回 token。"""
        old_host = self.api_host
        old_key = self.api_key
        if api_host:
            self.api_host = api_host
        self.api_key = api_key
        try:
            token = self._refresh_token()
            return token
        except Exception:
            self.api_host = old_host
            self.api_key = old_key
            raise

    # ==================== 业务方法 ====================

    def get_current_customer(self) -> Dict[str, Any]:
        """获取当前用户信息"""
        return self._request('GET', '/api/business/customer/current')

    def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """获取指定用户信息"""
        safe_id = self._sanitize_param(user_id, 'safe')
        return self._request('GET', '/api/business/sysUser/info',
                            headers={'id': safe_id})

    def get_user_points(self) -> Dict[str, Any]:
        """获取用户积分"""
        data = self.get_current_customer()
        points_data = data.get('data') if isinstance(data, dict) else {}
        points_data = points_data if isinstance(points_data, dict) else {}
        return {
            'pointsAmount': points_data.get('pointsAmount', 0),
            'usedPointsAmount': points_data.get('usedPointsAmount', 0)
        }

    def get_user_phone(self) -> str:
        """获取用户手机号"""
        data = self.get_current_customer()
        phone_data = data.get('data') if isinstance(data, dict) else {}
        phone_data = phone_data if isinstance(phone_data, dict) else {}
        phone = phone_data.get('phone', '')
        # 脱敏处理
        if len(phone) >= 11:
            return f"{phone[:3]}****{phone[7:]}"
        return phone

    def refresh_membership(self) -> Dict[str, Any]:
        """刷新并获取当前会员等级与额度"""
        return self._request('GET', '/api/business/customer/membership/refresh')

    # 文件管理
    def list_files(self) -> Dict[str, Any]:
        """获取文件列表"""
        return self._request('GET', '/api/business/file/list')

    def page_files(self, page: int = 1, page_size: int = 100) -> Dict[str, Any]:
        """分页获取文件列表"""
        safe_page = self._sanitize_param(str(page), 'digit') or '1'
        safe_page_size = self._sanitize_param(str(page_size), 'digit') or '100'
        return self._request(
            'GET',
            '/api/business/file/page',
            params={'pageNum': safe_page, 'pageSize': safe_page_size},
        )

    def list_files_paged(self, page_size: int = 100) -> Dict[str, Any]:
        """分页拉取全部文件，避免默认列表接口返回数量被截断。"""
        page_size = max(1, min(int(page_size or 100), 500))
        page = 1
        all_items: List[Dict[str, Any]] = []
        seen_ids = set()
        total = None

        while True:
            response = self.page_files(page, page_size)
            payload = response.get('data')
            items = payload if isinstance(payload, list) else []
            if isinstance(payload, dict):
                for key in ('records', 'list', 'rows', 'items'):
                    value = payload.get(key)
                    if isinstance(value, list):
                        items = value
                        break

            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get('id') or '')
                if item_id and item_id in seen_ids:
                    continue
                if item_id:
                    seen_ids.add(item_id)
                all_items.append(item)

            raw_total = response.get('total')
            if raw_total is None and isinstance(payload, dict):
                raw_total = payload.get('total')
            try:
                total = int(raw_total) if raw_total is not None else total
            except (TypeError, ValueError):
                total = None

            if not items:
                break
            if total is not None and len(all_items) >= total:
                break
            if len(items) < page_size:
                break
            page += 1

        return {
            'code': 200,
            'msg': 'SUCCESS',
            'total': total if total is not None else len(all_items),
            'data': all_items,
        }

    def search_files(self, keyword: str, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """按标题关键词搜索文件。"""
        safe_keyword = self._sanitize_param(keyword, 'safe').strip()
        if not safe_keyword:
            return {'code': 200, 'msg': 'SUCCESS', 'total': 0, 'data': []}
        safe_page = self._sanitize_param(str(page), 'digit') or '1'
        safe_page_size = self._sanitize_param(str(page_size), 'digit') or '20'
        return self._request(
            'GET',
            '/api/business/file/page',
            params={
                'originalFilename': safe_keyword,
                'pageNum': int(safe_page),
                'pageSize': int(safe_page_size),
            },
        )

    def get_file_info(self, file_id: str) -> Dict[str, Any]:
        """获取文件详情"""
        safe_id = self._sanitize_param(file_id, 'safe')
        return self._request('GET', '/api/business/file/info',
                            params={'fileId': safe_id})

    def get_audio_file(self, file_id: str) -> Dict[str, Any]:
        """获取会议音频的预签名下载地址和文件元数据。"""
        safe_id = self._sanitize_param(file_id, 'safe')
        info_response = self.get_file_info(safe_id)
        info = info_response.get('data') if isinstance(info_response, dict) else {}
        info = info if isinstance(info, dict) else {}
        download_response = self._request(
            'GET',
            '/api/business/file/presign/download',
            params={'fileId': safe_id},
        )
        download_data = (
            download_response.get('data')
            if isinstance(download_response, dict)
            else download_response
        )
        if isinstance(download_data, dict):
            download_url = download_data.get('url')
        else:
            download_url = download_data
        download_url = str(download_url or info.get('url') or '').strip()
        try:
            parsed_url = urlsplit(download_url)
        except ValueError:
            parsed_url = None
        if not parsed_url or parsed_url.scheme not in ('http', 'https') or not parsed_url.netloc:
            raise LynseAPIError(
                'Lynse did not return a valid audio download URL. '
                'The recording may still be processing or unavailable.'
            )

        file_name = str(
            info.get('originalFilename') or info.get('filename') or f'{safe_id}.audio'
        ).strip()
        mime_type = str(info.get('contentType') or '').strip().lower()
        if not mime_type:
            mime_type = (
                mimetypes.guess_type(file_name)[0]
                or mimetypes.guess_type(parsed_url.path)[0]
                or 'application/octet-stream'
            )
        raw_size = info.get('size')
        try:
            size = max(0, int(raw_size)) if raw_size not in (None, '') else None
        except (TypeError, ValueError):
            size = None

        return {
            'code': download_response.get('code', 200)
            if isinstance(download_response, dict)
            else 200,
            'msg': download_response.get(
                'msg', download_response.get('message', 'SUCCESS')
            )
            if isinstance(download_response, dict)
            else 'SUCCESS',
            'data': {
                'fileId': safe_id,
                'fileName': file_name,
                'mimeType': mime_type,
                'size': size,
                'url': download_url,
            },
        }

    def get_conclusion(self, file_id: str) -> Dict[str, Any]:
        """获取文件总结"""
        safe_id = self._sanitize_param(file_id, 'safe')
        return self._request('GET', '/api/business/file/conclusion/list',
                            params={'fileId': safe_id})

    def list_todos(self, status: str = 'all', page_num: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """获取全量文件待办后在本地按状态和页码切片。"""
        safe_status = self._sanitize_param(status, 'safe').lower() or 'all'
        safe_page_num = max(1, int(page_num or 1))
        safe_page_size = max(1, min(int(page_size or 20), 100))
        response = self.list_all_todos()
        items = response.get('data') if isinstance(response.get('data'), list) else []
        if safe_status in ('open', 'todo', 'pending', 'unfinished', '0'):
            items = [item for item in items if int((item or {}).get('isCompleted') or 0) == 0]
        elif safe_status in ('done', 'completed', 'finished', '1'):
            items = [item for item in items if int((item or {}).get('isCompleted') or 0) == 1]
        total = len(items)
        start = (safe_page_num - 1) * safe_page_size
        end = start + safe_page_size
        return {
            'code': response.get('code', 200),
            'msg': response.get('msg', 'SUCCESS'),
            'total': total,
            'data': items[start:end],
        }

    def list_all_todos(self) -> Dict[str, Any]:
        """获取全量文件待办列表。"""
        return self._request('POST', '/api/business/file/todo/listall', json_data={})

    def delete_todos(self, todo_ids: list[str]) -> Dict[str, Any]:
        """删除待办。"""
        clean_ids = [
            self._sanitize_param(str(todo_id), 'safe')
            for todo_id in todo_ids
            if str(todo_id or '').strip()
        ]
        return self._request('POST', '/api/business/file/todo/delete',
                            json_data={'todoIds': clean_ids})

    def clear_completed_todos(self) -> Dict[str, Any]:
        """清理已完成待办。"""
        return self._request('POST', '/api/business/file/todo/clear', json_data={})

    def get_outline(self, file_id: str) -> Dict[str, Any]:
        """获取文件大纲"""
        safe_id = self._sanitize_param(file_id, 'safe')
        return self._request('GET', '/api/business/file/outline/get',
                            params={'fileId': safe_id})

    def export_outline(self, file_id: str) -> Dict[str, Any]:
        """导出大纲"""
        safe_id = self._sanitize_param(file_id, 'safe')
        return self._request('GET', '/api/business/file/outline/export',
                            params={'fileId': safe_id})

    def list_files_by_time_range(self, days: int = 7) -> Dict[str, Any]:
        """按时间范围查询文件（过去 N 天）"""
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)

        params = {
            'startTime': start_time.strftime('%Y-%m-%dT%H:%M:%S'),
            'endTime': end_time.strftime('%Y-%m-%dT%H:%M:%S')
        }
        return self._request('GET', '/api/business/file/timeRange/list', params=params)

    def list_files_by_month(self, year: int, month: int) -> Dict[str, Any]:
        """Query meetings in a specific month.

        Args:
            year: 4-digit year (e.g. 2026)
            month: Month number 1-12
        """
        if not (1 <= month <= 12):
            raise LynseAPIError(f"Invalid month: {month}. Must be 1-12.")
        # First day of month, last day of month
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
        else:
            end = datetime(year, month + 1, 1) - timedelta(seconds=1)
        params = {
            'startTime': start.strftime('%Y-%m-%dT%H:%M:%S'),
            'endTime': end.strftime('%Y-%m-%dT%H:%M:%S')
        }
        return self._request('GET', '/api/business/file/timeRange/list', params=params)

    def list_files_by_week(self, year: int, week: int) -> Dict[str, Any]:
        """Query meetings in a specific ISO week.

        Args:
            year: 4-digit year (e.g. 2026)
            week: ISO week number 1-53
        """
        if not (1 <= week <= 53):
            raise LynseAPIError(f"Invalid week: {week}. Must be 1-53.")
        # ISO week: Monday is day 1
        start = datetime.strptime(f'{year}-W{week:02d}-1', '%G-W%V-%u')
        end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        params = {
            'startTime': start.strftime('%Y-%m-%dT%H:%M:%S'),
            'endTime': end.strftime('%Y-%m-%dT%H:%M:%S')
        }
        return self._request('GET', '/api/business/file/timeRange/list', params=params)

    def list_files_by_range(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Query meetings in an arbitrary date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d')
            end = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        except ValueError as e:
            raise LynseAPIError(f"Invalid date format: {e}. Use YYYY-MM-DD.")
        if start > end:
            raise LynseAPIError(f"Start date ({start_date}) must be before end date ({end_date}).")
        params = {
            'startTime': start.strftime('%Y-%m-%dT%H:%M:%S'),
            'endTime': end.strftime('%Y-%m-%dT%H:%M:%S')
        }
        return self._request('GET', '/api/business/file/timeRange/list', params=params)

    def list_folders(self) -> Dict[str, Any]:
        """获取文件夹/分组列表"""
        return self._request('GET', '/api/business/file/folder/list')

    def create_folder(self, folder_data: Dict[str, Any]) -> Dict[str, Any]:
        """创建文件夹/分组"""
        return self._request('POST', '/api/business/file/folder/add', json_data=folder_data)

    def change_folder(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """移动文件到文件夹/分组"""
        params = {
            'oldFolderId': payload.get('oldFolderId') or '',
            'newFolderId': payload.get('newFolderId') or '',
            'fileIds': payload.get('fileIds') or [],
        }
        return self._request('GET', '/api/business/file/changeFolder', params=params)

    def organize_meetings(self, days: int = None, execute: bool = False,
                          yes: bool = False, include_no_conclusion: bool = False) -> Dict[str, Any]:
        """Organize meetings into topic folders.

        Fetches all meetings (optionally limited to the last `days` days) and the
        existing folders, builds a classification plan, and — only when
        `execute=True` — creates the proposed folders and moves meetings. The
        default is a dry-run plan that changes nothing.

        Safety: `execute=True` in a non-interactive (non-TTY) context requires
        `yes=True`, so AI agents must opt in explicitly rather than have a plan
        auto-applied.
        """
        # 1. gather meetings + folders
        resp = self.list_files_paged(page_size=100)
        meetings = resp.get('data') if isinstance(resp, dict) else (resp or [])
        meetings = [m for m in meetings if isinstance(m, dict)]
        if days:
            cutoff = datetime.now() - timedelta(days=int(days))

            def _meeting_dt(m):
                for k in ('recordStartTime', 'createTime'):
                    raw = (m.get(k) or '').replace('T', ' ')
                    try:
                        return datetime.strptime(raw[:19], '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        continue
                return None
            meetings = [m for m in meetings if _meeting_dt(m) is None or _meeting_dt(m) >= cutoff]

        folders_resp = self.list_folders()
        existing = folders_resp.get('data') if isinstance(folders_resp, dict) else (folders_resp or [])
        existing = [f for f in existing if isinstance(f, dict)]

        # 2. plan (pure)
        plan = build_organize_plan(meetings, existing, include_no_conclusion=include_no_conclusion)

        if not execute:
            return plan

        # 3. safety gate
        is_tty = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()
        if not yes and not is_tty:
            print("Error: --execute is non-interactive here. Re-run with --yes to apply the plan "
                  "(this creates folders and moves meetings).", file=sys.stderr)
            sys.exit(EXIT_INVALID)
        if not yes:
            print(_format_text(plan, 'organizeMeetings'))
            try:
                ans = input("\nApply these changes? [y/N] ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print("", file=sys.stderr)
                sys.exit(EXIT_INVALID)
            if ans not in ('y', 'yes'):
                print("Aborted — no changes made.", file=sys.stderr)
                plan['mode'] = 'plan'
                return plan

        # 4. execute: create new folders, then chunked moves
        reuse_count = sum(1 for f in plan['folders'] if f['action'] == 'REUSE')
        created = []
        create_errors = []
        folder_id_by_cat = {}
        for f in plan['folders']:
            if f['action'] == 'REUSE':
                folder_id_by_cat[f['category']] = f['target_folder_id']
                continue
            try:
                r = self.create_folder({'folderName': f['target_folder_name'],
                                        'color': f.get('color') or '#EEEEEE'})
                new_id = _extract_folder_id(r)
                if not new_id:
                    raise RuntimeError("create_folder returned no folder id")
                folder_id_by_cat[f['category']] = new_id
                created.append({'category': f['category'], 'id': new_id, 'name': f['target_folder_name']})
                f['target_folder_id'] = new_id
            except Exception as e:
                f['_error'] = str(e)
                create_errors.append({'category': f['category'],
                                      'name': f['target_folder_name'], 'error': str(e)})

        moves_attempted = moves_succeeded = 0
        errors = []
        for f in plan['folders']:
            target = folder_id_by_cat.get(f['category'])
            ids = f.get('meeting_ids') or []
            if not target or not ids:
                continue
            for i in range(0, len(ids), _MOVE_CHUNK):
                chunk = ids[i:i + _MOVE_CHUNK]
                moves_attempted += len(chunk)
                try:
                    self.change_folder({'oldFolderId': '', 'newFolderId': target, 'fileIds': chunk})
                    moves_succeeded += len(chunk)
                except Exception as e:
                    errors.extend({'meeting_id': mid, 'error': str(e)} for mid in chunk)

        return {
            'mode': 'execute',
            'code': 200,
            'scope': plan['scope'],
            'results': {
                'folders_created': created,
                'folders_reused': reuse_count,
                'folders_failed': create_errors,
                'moves_attempted': moves_attempted,
                'moves_succeeded': moves_succeeded,
                'already_in_place': plan['totals'].get('already_organized', 0),
                'errors': errors,
            },
            'skipped_no_conclusion': len(plan.get('skipped_no_conclusion', [])),
        }

    @staticmethod
    def _normalize_speaker_name(value: str) -> str:
        return re.sub(r'\s+', '', str(value or '').strip())

    @classmethod
    def _speaker_name_aliases(cls, value: str) -> set[str]:
        normalized = cls._normalize_speaker_name(value)
        aliases = {str(value or '').strip(), normalized}
        speaker_match = re.fullmatch(r'发言人(\d+)', normalized)
        if speaker_match:
            aliases.add(speaker_match.group(1))
        elif re.fullmatch(r'\d+', normalized):
            aliases.add(f'发言人{normalized}')
        return {item for item in aliases if item}

    def get_transcription_record(self, file_id: str) -> Dict[str, Any]:
        """获取转写记录"""
        safe_id = self._sanitize_param(file_id, 'safe')
        return self._request('GET', '/api/business/file/trans/get',
                            params={'fileId': safe_id})

    def get_transcription_text(self, file_id: str) -> Dict[str, Any]:
        """获取会议转写文本，不读取 AI 总结。"""
        response = self.get_transcription_record(file_id)
        data = response.get('data') if isinstance(response, dict) else response
        return {
            'code': response.get('code', 200) if isinstance(response, dict) else 200,
            'msg': response.get('msg', response.get('message', 'SUCCESS')) if isinstance(response, dict) else 'SUCCESS',
            'data': _transcription_entries_to_text(data),
        }

    def list_transcription_record(self, task_id: str, *, team_id: str = '', file_id: str = '') -> Dict[str, Any]:
        """按 taskId 拉取转写记录。"""
        params = {
            'taskId': self._sanitize_param(task_id, 'safe'),
            'teamId': self._sanitize_param(team_id, 'safe'),
        }
        safe_file_id = self._sanitize_param(file_id, 'safe')
        if safe_file_id:
            params['fileId'] = safe_file_id
        return self._request('GET', '/api/business/file/trans/get', params=params)

    def edit_speaker_info(self, speaker_data: Dict[str, Any]) -> Dict[str, Any]:
        """批量更新发言人名称"""
        return self._request('PUT', '/api/business/file/trans/speaker', json_data=speaker_data)

    def rename_speaker(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """解析会议与 speaker 信息后，统一执行发言人改名。"""
        meeting_id = str(payload.get('meetingId') or payload.get('fileId') or '').strip()
        old_name = str(payload.get('oldName') or '').strip()
        new_name = str(payload.get('newName') or '').strip()
        task_id = str(payload.get('taskId') or '').strip()
        team_id = str(payload.get('teamId') or '').strip()
        if not meeting_id:
            raise LynseAPIError('缺少 meetingId/fileId，无法执行发言人改名')
        if not old_name or not new_name:
            raise LynseAPIError('缺少 oldName/newName，无法执行发言人改名')

        files_response = self.list_files_paged(page_size=100)
        file_items = files_response.get('data') if isinstance(files_response.get('data'), list) else []
        file_record = None
        for item in file_items:
            if isinstance(item, dict) and str(item.get('id') or '').strip() == meeting_id:
                file_record = item
                break
        if file_record is None:
            file_info = self.get_file_info(meeting_id)
            maybe_record = file_info.get('data')
            if isinstance(maybe_record, dict):
                file_record = maybe_record
        if not isinstance(file_record, dict):
            raise LynseAPIError(f'未找到会议：{meeting_id}')

        file_id = str(file_record.get('id') or meeting_id).strip()
        resolved_task_id = task_id or str(file_record.get('transcribeTaskId') or '').strip()
        if not resolved_task_id:
            raise LynseAPIError(f'会议 {meeting_id} 缺少 transcribeTaskId，无法修改发言人')

        transcription = self.list_transcription_record(resolved_task_id, team_id=team_id, file_id=file_id)
        entries = transcription.get('data') if isinstance(transcription.get('data'), list) else []
        target_aliases = self._speaker_name_aliases(old_name)
        speaker_info_list = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            speaker_id = str(item.get('speakerId') or '').strip()
            speaker_name = str(item.get('speakerName') or '').strip()
            if not speaker_id or not speaker_name:
                continue
            if self._speaker_name_aliases(speaker_name) & target_aliases:
                speaker_info_list.append({'speakerId': speaker_id, 'speakerName': new_name})
        if not speaker_info_list:
            raise LynseAPIError(f'未找到发言人：{old_name}')
        return self.edit_speaker_info({'taskId': resolved_task_id, 'speakerInfoList': speaker_info_list})

    # AI 模型管理
    def get_ai_models(self) -> Dict[str, Any]:
        """获取 AI 模型列表"""
        return self._request('GET', '/api/business/ai/getAllAIModelList')

    def add_model(self, model_data: Dict[str, Any]) -> Dict[str, Any]:
        """添加 AI 模型"""
        return self._request('POST', '/api/business/ai/addModel', json_data=model_data)

    def delete_model(self, model_id: str) -> Dict[str, Any]:
        """删除 AI 模型"""
        safe_id = self._sanitize_param(model_id, 'safe')
        return self._request('DELETE', '/api/business/ai/deleteModel',
                            headers={'id': safe_id})

    def edit_model(self, model_data: Dict[str, Any]) -> Dict[str, Any]:
        """编辑 AI 模型"""
        return self._request('POST', '/api/business/ai/editModel', json_data=model_data)

    def enable_model(self, model_id: str, enabled: bool) -> Dict[str, Any]:
        """启用/禁用 AI 模型"""
        safe_id = self._sanitize_param(model_id, 'safe')
        enabled_str = 'true' if enabled else 'false'
        return self._request('POST', '/api/business/ai/enableModel',
                            headers={'id': safe_id, 'enabled': enabled_str})

    # 设备管理
    def get_device_page(self, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        """分页获取设备列表"""
        safe_page = self._sanitize_param(str(page), 'digit')
        return self._request('GET', '/api/business/deviceMgt/page9',
                            headers={'pageNum': safe_page, 'pageSize': str(page_size)})

    def get_my_devices(self) -> Dict[str, Any]:
        """获取当前用户绑定设备列表，包含正确 SN 号"""
        return self._request('GET', '/api/business/device/mine')

    def get_device_info(self, device_id: str) -> Dict[str, Any]:
        """获取设备详情"""
        safe_id = self._sanitize_param(device_id, 'safe')
        return self._request('GET', '/api/business/deviceMgt/info5',
                            headers={'id': safe_id})

    def unbind_device(self, device_id: str) -> Dict[str, Any]:
        """解绑设备"""
        safe_id = self._sanitize_param(device_id, 'safe')
        return self._request('POST', '/api/business/deviceMgt/unbind',
                            headers={'id': safe_id})

    # 用户管理
    def get_current_user(self) -> Dict[str, Any]:
        """获取当前系统用户"""
        return self._request('GET', '/api/business/sysUser/current')

    def add_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """添加用户"""
        return self._request('POST', '/api/business/sysUser/add2', json_data=user_data)

    def edit_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """编辑用户"""
        return self._request('POST', '/api/business/sysUser/edit1', json_data=user_data)

    def remove_user(self, user_id: str) -> Dict[str, Any]:
        """删除用户"""
        safe_id = self._sanitize_param(user_id, 'safe')
        return self._request('POST', '/api/business/sysUser/remove',
                            headers={'id': safe_id})

    # 登录相关
    def login(self, username: str, password: str) -> Dict[str, Any]:
        """用户名密码登录"""
        return self._request('POST', '/api/business/sysLogin/login',
                            json_data={'username': username, 'password': password})

    def login_with_phone(self, phone: str, captcha: str) -> Dict[str, Any]:
        """手机号验证码登录"""
        return self._request('POST', '/api/business/sysLogin/login',
                            json_data={'phone': phone, 'captcha': captcha})

    def logout(self) -> Dict[str, Any]:
        """登出"""
        return self._request('POST', '/api/business/sysLogin/logout')

    # 消息管理
    def send_sms(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """发送短信"""
        return self._request('POST', '/api/business/message/sendSmsMessage',
                            json_data=message_data)

    def send_email(self, email_data: Dict[str, Any]) -> Dict[str, Any]:
        """发送邮件"""
        return self._request('POST', '/api/business/message/sendEmailMessage',
                            json_data=email_data)

    # 系统管理
    def get_role_list(self) -> Dict[str, Any]:
        """获取角色列表"""
        return self._request('GET', '/api/business/sysRole/list1')

    def get_menu_tree(self) -> Dict[str, Any]:
        """获取菜单树"""
        return self._request('GET', '/api/business/sysMenu/tree')


def _print_help():
    """打印帮助信息。"""
    print(f"Lynse CLI v{CLI_VERSION} — AI-powered meeting transcription & management")
    print()
    sections = [
        ("Quick Commands", [
            ("me", "Show current user info"),
            ("meetings list [--days N]", "List recent meetings (past N days)"),
            ("meetings month <YYYY-MM>", "List meetings in a specific month"),
            ("meetings week <YYYY-Wnn>", "List meetings in a specific ISO week"),
            ("meetings range <start> <end>", "List meetings in a date range (YYYY-MM-DD)"),
            ("meetings search <keyword>", "Search meetings by title"),
            ("meetings transcript <id>", "Get meeting transcription"),
            ("meetings transcript-text <id>", "Get meeting transcription text"),
            ("meetings summary <id>", "Get AI summary"),
            ("meetings outline <id>", "Get meeting outline"),
            ("meetings info <id>", "Get meeting details"),
            ("meetings organize [--execute] [--yes]", "Auto-classify meetings into folders (dry-run by default)"),
        ]),
        ("Folders", [
            ("folders list", "List folders/groups"),
            ("folders create <json>", "Create a folder"),
            ("folders move <json>", "Move files to folder"),
        ]),
        ("Todos", [
            ("todos list [status] [page] [size]", "List todos (all/open/done)"),
            ("todos delete <ids>", "Delete todos"),
            ("todos clear", "Clear completed todos"),
        ]),
        ("Devices & Models", [
            ("devices list", "List bound devices"),
            ("models list", "List AI models"),
        ]),
        ("Auth", [
            ("auth login --api-key <key>", "Save API key & validate"),
            ("auth status", "Show auth configuration"),
            ("auth logout [--all]", "Clear cached tokens"),
            ("auth doctor", "Diagnose auth issues"),
        ]),
        ("System", [
            ("version", "Show version info"),
            ("doctor", "Run diagnostics"),
            ("update", "Check for updates"),
        ]),
        ("Output Format", [
            ("--json", "Compact JSON (default when piped)"),
            ("--pretty", "Pretty JSON (default in terminal)"),
            ("--text", "Human-readable summary"),
            ("--table", "ASCII table for lists"),
            ("--output <file>", "Save output to file"),
        ]),
        ("Exit Codes", [
            ("0", "Success"),
            ("1", "Invalid args / unknown error"),
            ("2", "Authentication failure"),
            ("3", "Network error"),
            ("4", "Timeout"),
            ("5", "Permission denied"),
            ("6", "Server error"),
        ]),
    ]
    for title, cmds in sections:
        print(f"\n{title}:")
        for cmd, desc in cmds:
            print(f"  {cmd:<40s} {desc}")
    print("\nLegacy API commands (getCurrentCustomer, listFiles, ...) are still supported.")


def _handle_auth_command(subcommand: str, args: list, flags: dict):
    """处理 auth 子命令。"""
    if subcommand == '__auth_login__':
        api_key, api_host = None, None
        i = 0
        while i < len(args):
            if args[i] == '--api-key' and i + 1 < len(args):
                api_key = args[i + 1]; i += 2
            elif args[i] == '--host' and i + 1 < len(args):
                api_host = args[i + 1]; i += 2
            else:
                i += 1
        if not api_key:
            # Let the user input their own key interactively (terminal only).
            # In non-interactive / agent contexts (no TTY) we never hardcode or
            # guess a key — require it explicitly via --api-key.
            if sys.stdin.isatty():
                import getpass
                try:
                    api_key = (getpass.getpass(
                        "Enter your Lynse API key (input hidden, format dk_xxx): "
                    ) or "").strip()
                except (KeyboardInterrupt, EOFError):
                    print("", file=sys.stderr)
                    sys.exit(EXIT_INVALID)
            if not api_key:
                print(
                    "Error: API key is required.\n"
                    "  Interactive terminal:  lynse auth login\n"
                    "  Or pass it explicitly: lynse auth login --api-key dk_xxx\n"
                    "Get your key from the system console.",
                    file=sys.stderr,
                )
                sys.exit(EXIT_INVALID)
        config_dir = _get_user_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / 'config.json'
        config = {}
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except Exception:
                pass
        if api_key:
            config['api_key'] = api_key
        if api_host:
            config['api_host'] = api_host
        try:
            api = LynseAPI(api_key=api_key, api_host=api_host)
            token = api.auth_login(api_key, api_host)
            config['access_token'] = token
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)
            token_file = config_dir / 'tokens.json'
            api._save_token(token)
            result = {'status': 'ok', 'message': 'API key validated and saved', 'token_cached': True,
                      'config_path': str(config_file), 'token_path': str(api.token_file)}
            _format_output(result, 'auth_login', flags)
        except LynseAPIError as e:
            print(f"Error: {e.message}", file=sys.stderr)
            sys.exit(_resolve_exit_code(e))
        return

    if subcommand == '__auth_status__':
        ucfg = _load_user_config()
        host, key, key_source = _resolve_api_credentials(user_config=ucfg)
        masked_key = f"{key[:6]}...{key[-4:]}" if key and len(key) > 10 else ('(not set)' if not key else key)
        token_file = _get_user_config_dir() / 'tokens.json'
        legacy_token = Path(__file__).parent.resolve() / '.token_cache'
        actual_token = token_file if token_file.exists() else (legacy_token if legacy_token.exists() else None)
        token_status = 'cached' if actual_token else 'none'
        source_label = {
            'param': '--api-key', 'shell': 'shell env',
            'config': '~/.lynse/config.json', 'env': '.env',
        }.get(key_source, 'unknown')
        result = {'api_host': host or 'not configured', 'api_key': masked_key, 'token_status': token_status,
                  'token_file': str(actual_token) if actual_token else 'none',
                  'config_source': source_label}
        _format_output(result, 'auth_status', flags)
        return

    if subcommand == '__auth_logout__':
        clear_all = '--all' in args
        removed = []
        for tf in [_get_user_config_dir() / 'tokens.json', Path(__file__).parent.resolve() / '.token_cache']:
            if tf.exists():
                tf.unlink(); removed.append(str(tf))
        if clear_all:
            cf = _get_user_config_dir() / 'config.json'
            if cf.exists():
                config = {}
                try:
                    with open(cf, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                except Exception:
                    pass
                config.pop('api_key', None); config.pop('access_token', None)
                with open(cf, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2)
                removed.append(str(cf))
        result = {'status': 'ok', 'removed': removed} if removed else {'status': 'ok', 'message': 'No cached tokens found'}
        _format_output(result, 'auth_logout', flags)
        return

    if subcommand == '__auth_doctor__':
        checks = []
        def _check(name, ok, detail=''):
            checks.append({'check': name, 'ok': ok, 'detail': detail})
            print(f"  {'✓' if ok else '✗'} {name}" + (f": {detail}" if detail else ''), file=sys.stderr)
        print("Auth diagnostics:", file=sys.stderr)
        ucfg = _load_user_config()
        host, key, _ = _resolve_api_credentials(user_config=ucfg)
        _check('API host configured', bool(host), host or 'not set')
        _check('API key configured', bool(key), f"{key[:6]}..." if len(key) > 6 else ('not set' if not key else key))
        if host and key:
            try:
                resp = requests.get(host, timeout=5)
                _check('API host reachable', resp.status_code < 500, f"HTTP {resp.status_code}")
            except Exception as e:
                _check('API host reachable', False, str(e))
            try:
                api = LynseAPI(api_key=key, api_host=host)
                token = api.auth_login(key, host)
                _check('Token exchange', True, f"Token obtained ({len(token)} chars)")
                try:
                    api._access_token = token
                    info = api.get_current_customer()
                    d = info.get('data') if isinstance(info, dict) else {}
                    d = d if isinstance(d, dict) else {}
                    _check('Current user', True, f"{d.get('nickname') or d.get('name') or d.get('id', 'unknown')}")
                except Exception as e:
                    _check('Current user', False, str(e))
            except Exception as e:
                _check('Token exchange', False, str(e))
        all_ok = all(c['ok'] for c in checks)
        print(f"\nResult: {'All checks passed' if all_ok else 'Some checks failed'}", file=sys.stderr)
        sys.exit(EXIT_SUCCESS if all_ok else EXIT_INVALID)
        return


def main():
    """CLI 入口函数"""
    if len(sys.argv) < 2:
        _print_help()
        sys.exit(EXIT_SUCCESS)

    # 1. 剥离全局标志
    flags, cli_args = _parse_global_flags(sys.argv[1:])

    if not cli_args:
        _print_help()
        sys.exit(EXIT_SUCCESS)

    # 2. 帮助请求：--help/-h/help 直接打印帮助，不进入需要配置 host 的业务命令
    if cli_args[0] in ('--help', '-h', 'help'):
        _print_help()
        sys.exit(EXIT_SUCCESS)

    # 3. 解析命令与别名
    command, args, is_alias = _resolve_alias(cli_args[0], cli_args[1:])
    display_command = _ALIAS_INFO.get(command, command)

    if LynseAPI._lynse_debug_enabled():
        print(
            f"[{_lynse_log_ts()}] [lynse-cli] invoke command={command} "
            f"(alias={is_alias}) arg_count={len(args)}",
            file=sys.stderr, flush=True,
        )

    try:
        # 4. 本地命令（不需要 API 实例）
        if command == '__version__':
            import platform as _plat
            req_ver = 'not installed'
            try:
                import requests as _req
                req_ver = getattr(_req, '__version__', 'unknown')
            except ImportError:
                pass
            result = {
                'version': f'lynse-cli v{CLI_VERSION}',
                'python': _plat.python_version(),
                'os': f'{_plat.system()} {_plat.release()}',
                'requests': req_ver,
            }
            if flags.get('format') in ('text', None) or flags.get('format') == 'pretty':
                for k, v in result.items():
                    print(f"{k}: {v}")
            else:
                _format_output(result, command, flags)
            return

        if command == '__update__':
            result = {
                'version': f'v{CLI_VERSION}',
                'message': 'lynse-cli is managed via lynclaw skill updates. Run your package manager (npm/skill installer) to update.',
            }
            if flags.get('format') in ('text', 'pretty'):
                print(f"lynse-cli {result['version']}")
                print(result['message'])
            else:
                _format_output(result, command, flags)
            return

        if command == '__doctor__':
            checks = []
            def _check(name, ok, detail=''):
                checks.append({'check': name, 'ok': ok, 'detail': detail})
                print(f"  {'✓' if ok else '✗'} {name}" + (f": {detail}" if detail else ''), file=sys.stderr)
            print("Running diagnostics...\n", file=sys.stderr)
            import platform as _plat
            py_ver = _plat.python_version()
            py_ok = sys.version_info >= MIN_PYTHON_VERSION
            _check(f'Python >= {MIN_PYTHON_VERSION_TEXT}', py_ok, py_ver)
            try:
                import requests as _req
                _check('requests installed', True, getattr(_req, '__version__', 'unknown'))
            except ImportError:
                _check('requests installed', False, "run: pip install requests")
            ucfg = _load_user_config()
            host = os.environ.get('LYNSE_API_HOST') or ucfg.get('api_host') or ''
            key = os.environ.get('LYNSE_API_KEY') or ucfg.get('api_key') or ''
            _check('API host configured', bool(host), host[:40] if host else 'not set')
            masked_key = f"{key[:6]}..." if len(key) > 6 else ''
            _check('API key configured', bool(key), masked_key if key else 'not set')
            if host:
                try:
                    resp = requests.get(host, timeout=5)
                    _check('API host reachable', resp.status_code < 500, f"HTTP {resp.status_code}")
                except Exception as e:
                    _check('API host reachable', False, str(e)[:80])
            if host and key:
                try:
                    api = LynseAPI()
                    token = api._get_token(refresh=True)
                    _check('Token exchange', True, f"Token obtained ({len(token)} chars)")
                    try:
                        info = api.get_current_customer()
                        d = info.get('data') if isinstance(info, dict) else {}
                        d = d if isinstance(d, dict) else {}
                        name = d.get('nickname') or d.get('name') or str(d.get('id', '?'))
                        _check('Current user', True, name)
                    except Exception as e:
                        _check('Current user', False, str(e)[:80])
                except Exception as e:
                    _check('Token exchange', False, str(e)[:80])
            tf = _get_user_config_dir() / 'tokens.json'
            try:
                tf.parent.mkdir(parents=True, exist_ok=True)
                _check('Token cache writable', True, str(tf))
            except Exception as e:
                _check('Token cache writable', False, str(e)[:80])
            all_ok = all(c['ok'] for c in checks)
            print(f"\nResult: {'All checks passed ✓' if all_ok else 'Some checks failed ✗'}", file=sys.stderr)
            sys.exit(EXIT_SUCCESS if all_ok else EXIT_INVALID)
            return

        # 5. Auth 子命令（单独处理，可能需要未完整配置的 API）
        if command.startswith('__auth_'):
            _handle_auth_command(command, args, flags)
            return

        # 6. 初始化 API 并执行业务命令
        api = LynseAPI()

        if command in _ALIAS_HANDLERS:
            result = _ALIAS_HANDLERS[command](api, args)
        elif command == 'getUserInfo':
            if not args:
                print("Error: getUserInfo requires a user ID", file=sys.stderr); sys.exit(EXIT_INVALID)
            result = api.get_user_info(args[0])
        elif command == 'getUserPoints':
            result = api.get_user_points()
        elif command == 'getUserPhone':
            result = {'phone': api.get_user_phone()}
        elif command == 'refreshMembership':
            result = api.refresh_membership()
        elif command == 'listFilesPaged':
            page_size = int(args[0]) if args else 100
            result = api.list_files_paged(page_size)
        elif command == 'listAllTodos':
            result = api.list_all_todos()
        elif command == 'exportOutline':
            if not args:
                print("Error: exportOutline requires a file ID", file=sys.stderr); sys.exit(EXIT_INVALID)
            result = api.export_outline(args[0])
        elif command == 'renameSpeaker':
            if not args:
                print("Error: renameSpeaker requires JSON data", file=sys.stderr); sys.exit(EXIT_INVALID)
            result = api.rename_speaker(json.loads(args[0]))
        elif command == 'editSpeakerInfo':
            if not args:
                print("Error: editSpeakerInfo requires JSON data", file=sys.stderr); sys.exit(EXIT_INVALID)
            result = api.edit_speaker_info(json.loads(args[0]))
        elif command == 'getDevicePage':
            page = int(args[0]) if args else 1
            result = api.get_device_page(page)
        elif command == 'getCurrentUser':
            result = api.get_current_user()
        elif command == 'getRoleList':
            result = api.get_role_list()
        elif command == 'getMenuTree':
            result = api.get_menu_tree()
        elif command == 'login':
            if len(args) < 2:
                print("Error: login requires username and password", file=sys.stderr); sys.exit(EXIT_INVALID)
            result = api.login(args[0], args[1])
        elif command == 'loginWithPhone':
            if len(args) < 2:
                print("Error: loginWithPhone requires phone and captcha", file=sys.stderr); sys.exit(EXIT_INVALID)
            result = api.login_with_phone(args[0], args[1])
        elif command == 'logout':
            result = api.logout()
        else:
            print(f"Error: unknown command '{command}'", file=sys.stderr)
            print("Run 'lynse' with no arguments to see available commands.", file=sys.stderr)
            sys.exit(EXIT_INVALID)

        # 6. 输出结果（使用内部命令名匹配格式化器）
        _format_output(result, command, flags)

    except LynseAPIError as e:
        print(f"Error: {e.message}", file=sys.stderr)
        sys.exit(_resolve_exit_code(e))
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON - {e}", file=sys.stderr)
        sys.exit(EXIT_INVALID)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(EXIT_INVALID)


if __name__ == '__main__':
    if not hasattr(requests, 'get'):
        print("Error: requests library is not installed. Run: pip install requests", file=sys.stderr)
        sys.exit(EXIT_INVALID)
    main()
