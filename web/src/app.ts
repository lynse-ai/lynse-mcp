import { App, applyDocumentTheme, applyHostStyleVariables, type McpUiHostContext } from '@modelcontextprotocol/ext-apps';
import { createElement, ArrowLeft, ArrowUpRight, CheckCheck,
  ChevronLeft, ChevronRight, Circle, CircleCheck, Play, RefreshCw, Search, X, type IconNode } from 'lucide';

type Dict = Record<string, unknown>;
type Child = Node | string | null;
type Meeting = { id: string; title: string; recordedAt: string; duration: number; folder: string };
type Todo = { id: string; content: string; completed: boolean; dueAt: string; fileId: string; meetingTitle: string };
type Option = { id: string; label: string; description: string };
type Query = { days: number; keyword: string; page: number; pageSize: number; status: string };
type ListPayload = { view: 'meetings' | 'todos'; meetings: Meeting[]; todos: Todo[]; query: Query;
  total: number | null; hasNext: boolean; selectedId: string };
type Confirmation = { view: 'confirmation'; requestId: string; question: string; description: string; options: Option[] };

const app = new App({ name: 'Lynse', version: '1.0.0' }, {});
const root = document.getElementById('app')!;
let dispose = () => {};
const record = (v: unknown): Dict => v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Dict : {};
const str = (v: unknown): string => typeof v === 'string' ? v : '';
const num = (v: unknown, fallback = 0): number => typeof v === 'number' && Number.isFinite(v) ? v : fallback;
const array = (v: unknown): unknown[] => Array.isArray(v) ? v : [];

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', ...children: Child[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = cls;
  for (const child of children) if (child !== null) node.append(child);
  return node;
}
function icon(value: IconNode): SVGElement {
  return createElement(value, { width: 18, height: 18, 'stroke-width': 1.7, 'aria-hidden': 'true' });
}
function button(label: string, glyph: IconNode | null, action: () => void, cls = 'button'): HTMLButtonElement {
  const node = el('button', cls, glyph ? icon(glyph) : null, label);
  node.type = 'button';
  node.onclick = action;
  return node;
}
function iconButton(label: string, glyph: IconNode, action: () => void): HTMLButtonElement {
  const node = button('', glyph, action, 'button quiet icon-button');
  node.setAttribute('aria-label', label); node.title = label;
  return node;
}
function state(title: string, description = ''): HTMLElement {
  return el('div', 'empty', el('strong', '', title), description ? el('p', 'muted', description) : null);
}
function busy(target: HTMLElement, text: string): void {
  const node = el('div', 'loading', el('span', 'spinner'), text);
  node.setAttribute('role', 'status');
  target.replaceChildren(node);
}
function failure(target: HTMLElement, message: string, retry?: () => void): void {
  const box = el('div', 'error', el('p', '', message));
  box.setAttribute('role', 'alert');
  if (retry) box.append(button('重试', RefreshCw, retry, 'button quiet'));
  target.replaceChildren(box);
}
function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '暂时无法加载，请重试。';
}
async function call(name: string, args: Dict): Promise<Dict> {
  const result = await app.callServerTool({ name, arguments: args }, { timeout: 25000 });
  if (result.isError) {
    const detail = result.content.find(item => item.type === 'text');
    throw new Error(detail?.type === 'text' ? detail.text : '暂时无法读取，请重试或重新连接账号。');
  }
  if (!result.structuredContent) throw new Error('返回内容不完整，请重试。');
  return record(result.structuredContent);
}
function meetingFrom(value: unknown): Meeting {
  const item = record(value);
  return { id: str(item.id), title: str(item.title) || '未命名会议', recordedAt: str(item.recordedAt),
    duration: num(item.duration), folder: str(item.folder) };
}
function todoFrom(value: unknown): Todo {
  const item = record(value);
  return { id: str(item.id), content: str(item.content), completed: item.completed === true,
    dueAt: str(item.dueAt), fileId: str(item.fileId), meetingTitle: str(item.meetingTitle) };
}
function listFrom(value: Dict): ListPayload {
  const q = record(value.query);
  return { view: value.view === 'todos' ? 'todos' : 'meetings', meetings: array(value.meetings).map(meetingFrom),
    todos: array(value.todos).map(todoFrom), total: typeof value.total === 'number' ? value.total : null,
    hasNext: value.hasNext === true, selectedId: str(value.selectedId),
    query: { days: num(q.days, 7), keyword: str(q.keyword), page: num(q.page, 1),
      pageSize: num(q.pageSize, 20), status: str(q.status) || 'all' } };
}
function readableDate(value: string, dateOnly = false): string {
  if (!value || value === '0') return '';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return date.toLocaleString('zh-CN', dateOnly
    ? { month: 'long', day: 'numeric' }
    : { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function duration(value: number): string {
  if (!value) return '';
  const minutes = Math.floor(value / 60);
  return minutes > 0 ? `${minutes} 分钟` : `${Math.floor(value)} 秒`;
}
function pagination(data: ListPayload, load: (page: number) => void): HTMLElement {
  if (data.query.page === 1 && !data.hasNext) return el('div');
  const prev = button('上一页', ChevronLeft, () => load(data.query.page - 1), 'button quiet compact');
  const next = button('下一页', ChevronRight, () => load(data.query.page + 1), 'button quiet compact');
  prev.disabled = data.query.page <= 1;
  next.disabled = !data.hasNext;
  return el('footer', 'pagination', el('span', 'muted', `第 ${data.query.page} 页` + (data.total !== null ? ` · 共 ${data.total} 项` : '')),
    el('div', 'flex gap-1', prev, next));
}

/** Native media controls retain keyboard navigation, scrubbing and browser media support. */
function audioPlayer(meeting: Meeting): { node: HTMLElement; stop: () => void } {
  const status = el('div', 'player-status muted');
  status.setAttribute('role', 'status');
  const audio = el('audio', 'audio');
  audio.controls = true;
  audio.preload = 'none';
  audio.setAttribute('aria-label', `${meeting.title}的录音`);
  audio.hidden = true;
  const speed = el('select', 'select compact');
  speed.setAttribute('aria-label', '播放速度');
  for (const rate of [0.75, 1, 1.25, 1.5, 2]) {
    const option = el('option', '', `${rate}×`);
    option.value = String(rate);
    speed.append(option);
  }
  speed.value = '1';
  speed.hidden = true;
  speed.onchange = () => { audio.playbackRate = Number(speed.value); };
  let alive = true;
  let loaded = false;
  let loading = false;
  const load = async (refresh = false) => {
    if (loading) return;
    loading = true;
    loadButton.disabled = true;
    status.textContent = '正在获取录音…';
    const oldTime = refresh ? audio.currentTime : 0;
    try {
      const result = await call('get_meeting_audio', { file_id: meeting.id });
      if (!alive) return;
      const raw = str(record(result.data).url);
      const url = new URL(raw);
      const localPreview = url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
      if ((url.protocol !== 'https:' && !localPreview) || url.username || url.password) throw new Error('录音地址不可用，请刷新链接。');
      audio.src = url.href;
      audio.hidden = false;
      audio.onloadedmetadata = () => { audio.playbackRate = Number(speed.value); if (oldTime) audio.currentTime = oldTime; };
      loaded = true;
      speed.hidden = false;
      loadButton.replaceChildren(icon(RefreshCw));
      loadButton.className = 'button quiet icon-button';
      loadButton.setAttribute('aria-label', '刷新链接'); loadButton.title = '刷新链接';
      status.textContent = '';
      try { await audio.play(); } catch { status.textContent = '录音已加载，点击播放器开始播放'; }
    } catch (error) {
      if (alive) status.textContent = errorText(error);
    } finally {
      loading = false;
      if (alive) loadButton.disabled = false;
    }
  };
  const loadButton = button('播放录音', Play, () => { void load(loaded); }, 'button quiet');
  audio.onerror = () => { status.textContent = '录音暂时无法播放，请刷新链接或稍后重试'; };
  const node = el('section', 'player', el('div', 'player-controls', audio, speed, loadButton), status);
  return { node, stop: () => { alive = false; audio.pause(); audio.removeAttribute('src'); audio.load(); } };
}

function meetingDetail(meeting: Meeting, target: HTMLElement): () => void {
  const content = el('div', 'meeting-content');
  const tabs = el('div', 'tabs');
  tabs.setAttribute('role', 'tablist');
  tabs.setAttribute('aria-label', '会议内容');
  const player = audioPlayer(meeting);
  const cache = new Map<string, string>();
  let generation = 0;
  const load = async (kind: string, force = false) => {
    const ownGeneration = ++generation;
    for (const tab of tabs.querySelectorAll('button')) {
      const active = tab.dataset.kind === kind;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
    }
    const display = (text: string) => {
      // All API text stays text, including Markdown and HTML. No untrusted HTML is evaluated.
      content.replaceChildren(text ? el('article', 'prose', text) : state('内容还未生成', '可稍后重试或收听录音。'));
    };
    if (!force && cache.has(kind)) { display(cache.get(kind)!); return; }
    busy(content, '正在读取会议内容…');
    try {
      const result = await call('read_meeting_view', { file_id: meeting.id, content: kind });
      if (ownGeneration !== generation) return;
      const text = str(result.text);
      cache.set(kind, text);
      display(text);
    } catch (error) {
      if (ownGeneration === generation) failure(content, errorText(error), () => { void load(kind, true); });
    }
  };
  for (const [kind, label] of [['summary', '会议摘要'], ['outline', '大纲'], ['transcript', '转写全文']]) {
    const tab = button(label, null, () => { void load(kind); }, 'tab');
    tab.dataset.kind = kind;
    tab.setAttribute('role', 'tab');
    tab.onkeydown = event => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      const buttons = Array.from(tabs.querySelectorAll('button'));
      const next = buttons[(buttons.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : buttons.length - 1)) % buttons.length];
      next.focus(); next.click();
    };
    tabs.append(tab);
  }
  content.setAttribute('role', 'tabpanel');
  target.replaceChildren(el('div', 'detail-heading', el('h2', '', meeting.title), el('div', 'metadata',
      el('span', '', readableDate(meeting.recordedAt) || '时间未提供'),
      meeting.duration ? el('span', '', duration(meeting.duration)) : null,
      meeting.folder ? el('span', '', meeting.folder) : null)), player.node, tabs, content);
  void load('summary');
  return () => { generation++; player.stop(); };
}

function showMeetings(initial: ListPayload): void {
  let data = initial;
  let generation = 0;
  let stopDetail = () => {};
  let selectedId = '';
  const shell = el('section', 'shell');
  const listPane = el('div', 'list-pane');
  const detailPane = el('div', 'detail-pane'); detailPane.hidden = true;
  const count = el('span', 'muted result-count');
  // Sandboxed hosts may omit allow-forms; submit through explicit handlers instead.
  const form = el('div', 'search-row');
  form.setAttribute('role', 'search');
  form.hidden = !data.query.keyword;
  const input = el('input', 'search-input');
  input.type = 'search'; input.placeholder = '搜索会议标题'; input.value = data.query.keyword;
  input.maxLength = 200; input.setAttribute('aria-label', '搜索会议标题');
  const range = el('select', 'select');
  range.setAttribute('aria-label', '会议时间范围');
  for (const [value, label] of [['7', '最近 7 天'], ['30', '最近 30 天'], ['90', '最近 90 天'], ['365', '最近一年']]) {
    const option = el('option', '', label); option.value = value; range.append(option);
  }
  if (!Array.from(range.options).some(option => option.value === String(data.query.days))) {
    const option = el('option', '', `最近 ${data.query.days} 天`); option.value = String(data.query.days); range.append(option);
  }
  range.value = String(data.query.days);
  range.disabled = Boolean(data.query.keyword);
  input.oninput = () => { range.disabled = Boolean(input.value.trim()); };
  const submit = button('搜索', null, () => { void load(1); }, 'button primary');
  const searchToggle = iconButton('搜索会议', Search, () => {
    form.hidden = !form.hidden;
    searchToggle.setAttribute('aria-expanded', String(!form.hidden));
    if (!form.hidden) input.focus();
  });
  searchToggle.setAttribute('aria-expanded', String(!form.hidden));
  const list = el('div', 'meeting-list');
  list.setAttribute('aria-label', '会议列表');
  const detail = el('section', 'detail'); detail.setAttribute('aria-label', '会议详情');
  const footer = el('div');
  const messages = el('div');
  const select = (meeting: Meeting) => {
    generation++; submit.disabled = false; messages.replaceChildren();
    stopDetail(); selectedId = meeting.id;
    for (const row of list.querySelectorAll('button')) row.setAttribute('aria-pressed', String(row.dataset.id === selectedId));
    stopDetail = meetingDetail(meeting, detail);
    listPane.hidden = true; detailPane.hidden = false;
    back.focus();
  };
  const back = button('返回会议', ArrowLeft, () => {
    stopDetail(); stopDetail = () => {};
    detail.replaceChildren(); detailPane.hidden = true; listPane.hidden = false;
    Array.from(list.querySelectorAll('button')).find(row => row.dataset.id === selectedId)?.focus();
  }, 'button quiet back-button');
  const render = () => {
    stopDetail(); stopDetail = () => {}; selectedId = '';
    detail.replaceChildren(); detailPane.hidden = true; listPane.hidden = false;
    messages.replaceChildren();
    list.replaceChildren();
    count.textContent = data.total !== null ? `${data.total} 场` : '';
    if (!data.meetings.length) list.append(state('没有找到会议', '试试其他标题或时间范围。'));
    for (const meeting of data.meetings) {
      const row = button('', null, () => select(meeting), 'meeting-row');
      row.dataset.id = meeting.id;
      row.setAttribute('aria-pressed', 'false');
      row.disabled = !meeting.id;
      row.append(el('span', 'row-copy',
        el('strong', '', meeting.title), el('span', 'muted', [readableDate(meeting.recordedAt), duration(meeting.duration)].filter(Boolean).join(' · '))), icon(ChevronRight));
      list.append(row);
    }
    footer.replaceChildren(pagination(data, page => { void load(page); }));
    const selected = data.meetings.find(item => item.id === data.selectedId);
    if (selected) select(selected);
  };
  const load = async (page: number) => {
    const current = ++generation;
    submit.disabled = true;
    busy(messages, '正在查找会议…');
    try {
      const result = await call('read_meetings_view', { days: Number(range.value), keyword: input.value.trim(), page, page_size: data.query.pageSize });
      if (current !== generation) return;
      data = listFrom(result); render();
    } catch (error) {
      if (current === generation) failure(messages, errorText(error), () => { void load(page); });
    } finally { if (current === generation) submit.disabled = false; }
  };
  input.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); void load(1); } };
  range.onchange = () => { void load(1); };
  form.append(el('div', 'search-field', icon(Search), input), range, submit);
  listPane.append(el('div', 'toolbar', el('h1', 'section-title', '会议'), count, el('span', 'flex-1'), searchToggle), form, messages, list, footer);
  detailPane.append(back, detail);
  shell.append(listPane, detailPane);
  root.replaceChildren(shell); render();
  dispose = () => { generation++; stopDetail(); };
}

function showTodos(initial: ListPayload): void {
  let data = initial;
  let generation = 0;
  let stopDetail = () => {};
  const shell = el('section', 'shell');
  const contents = el('div');
  const messages = el('div');
  const filters = el('div', 'filter-row');
  filters.setAttribute('role', 'group'); filters.setAttribute('aria-label', '待办状态');
  const list = el('div', 'todo-list');
  const footer = el('div');
  const render = () => {
    messages.replaceChildren(); filters.replaceChildren(); list.replaceChildren();
    filters.append(el('h1', 'section-title', '待办'),
      el('span', 'muted result-count', data.total !== null ? String(data.total) : ''), el('span', 'flex-1'));
    for (const [value, label] of [['all', '全部'], ['open', '待完成'], ['done', '已完成']]) {
      const item = button(label, null, () => { void load(value, 1); }, 'filter');
      item.setAttribute('aria-pressed', String(data.query.status === value)); filters.append(item);
    }
    filters.append(iconButton('刷新', RefreshCw, () => { void load(data.query.status, data.query.page); }));
    if (!data.todos.length) list.append(state(data.query.status === 'done' ? '还没有已完成的待办' : '这里暂时没有待办'));
    for (const todo of data.todos) {
      const source = todo.fileId ? button(todo.meetingTitle || '查看来源会议', ArrowUpRight, () => { void openMeeting(todo); }, 'source-link') : null;
      const date = todo.dueAt && todo.dueAt !== '0' ? el('span', 'due-date', readableDate(todo.dueAt, true)) : null;
      const row = el('article', `todo-row ${todo.completed ? 'completed' : ''}`,
        el('span', 'todo-status', icon(todo.completed ? CircleCheck : Circle)),
        el('div', 'row-copy', el('p', 'todo-content', todo.content), el('div', 'metadata', source, date)));
      row.setAttribute('aria-label', `${todo.completed ? '已完成' : '待完成'}：${todo.content}`);
      list.append(row);
    }
    footer.replaceChildren(pagination(data, page => { void load(data.query.status, page); }));
  };
  const load = async (status: string, page: number) => {
    const current = ++generation;
    busy(messages, '正在读取待办…');
    try {
      const result = await call('read_todos_view', { status, page, page_size: data.query.pageSize });
      if (current === generation) { data = listFrom(result); render(); }
    } catch (error) { if (current === generation) failure(messages, errorText(error), () => { void load(status, page); }); }
  };
  const openMeeting = async (todo: Todo) => {
    const current = ++generation;
    stopDetail();
    const target = el('section', 'detail standalone');
    const back = button('返回待办', ArrowLeft, () => {
      generation++; stopDetail(); contents.replaceChildren(filters, messages, list, footer); render(); filters.querySelector('button')?.focus();
    }, 'button quiet');
    contents.replaceChildren(el('div', 'back-row', back), target); busy(target, '正在读取来源会议…');
    try {
      const result = await call('read_meeting_view', { file_id: todo.fileId, content: 'info' });
      if (current === generation) stopDetail = meetingDetail(meetingFrom(result.meeting), target);
    } catch (error) { if (current === generation) failure(target, errorText(error), () => { void openMeeting(todo); }); }
  };
  contents.append(filters, messages, list, footer);
  shell.append(contents);
  root.replaceChildren(shell); render();
  dispose = () => { generation++; stopDetail(); };
}

function showConfirmation(data: Confirmation): void {
  let alive = true;
  let selected = '';
  let sending = false;
  let submitted = false;
  const shell = el('section', 'shell confirmation-shell');
  const fieldset = el('fieldset', 'choices');
  const legend = el('legend', 'sr-only', data.question);
  fieldset.append(legend);
  const message = el('div', 'confirmation-status'); message.setAttribute('role', 'status');
  const renderSelection = () => {
    for (const label of fieldset.querySelectorAll('label')) label.dataset.selected = String(label.dataset.id === selected);
    confirm.disabled = !selected || sending || submitted;
  };
  for (const option of data.options) {
    const radio = el('input', 'choice-radio'); radio.type = 'radio'; radio.name = 'choice'; radio.value = option.id;
    radio.onchange = () => { selected = option.id; renderSelection(); };
    const code = el('span', 'choice-letter', `${option.id}.`); code.title = option.id;
    const label = el('label', 'choice', radio, code,
      el('span', 'row-copy', el('strong', '', option.label), option.description ? el('span', 'muted', option.description) : null));
    label.dataset.id = option.id; fieldset.append(label);
  }
  const send = async (cancelled: boolean) => {
    if (sending || submitted || (!cancelled && !selected)) return;
    sending = true; fieldset.disabled = true; cancel.disabled = true; renderSelection();
    message.textContent = '正在发送你的选择…';
    const option = data.options.find(item => item.id === selected);
    const text = cancelled
      ? `我取消了这次选择，请勿执行。\n问题：${data.question}\n确认编号：${data.requestId}`
      : `我确认选择 ${option?.id}：${option?.label}\n问题：${data.question}\n说明：${data.description || '无'}\n选项说明：${option?.description || '无'}\n确认编号：${data.requestId}`;
    try {
      // A real user click sends a user message; rendering the tool never grants consent.
      const result = await app.sendMessage({ role: 'user', content: [{ type: 'text', text }] }, { timeout: 20000 });
      if (result.isError) throw new Error('选择未发送成功，请重试，或直接在聊天中回复选项编号。');
      if (!alive) return;
      submitted = true;
      message.replaceChildren(icon(cancelled ? X : CheckCheck), cancelled ? '已取消' : '已发送');
      confirm.replaceChildren(cancelled ? '已取消' : '已确认');
    } catch (error) {
      if (alive) { message.textContent = errorText(error); fieldset.disabled = false; cancel.disabled = false; }
    } finally { sending = false; if (alive) renderSelection(); }
  };
  const confirm = button('确认', null, () => { void send(false); }, 'button primary'); confirm.disabled = true;
  const cancel = button('取消', null, () => { void send(true); }, 'button quiet');
  const canMessage = Boolean(app.getHostCapabilities()?.message);
  if (!canMessage) {
    message.textContent = '当前客户端不支持回传选择，请直接在聊天中回复选项编号。';
    fieldset.disabled = true; confirm.disabled = true; cancel.disabled = true;
  }
  shell.append(el('div', 'confirmation-body', el('h2', 'question', data.question),
      data.description ? el('p', 'question-description muted', data.description) : null,
      fieldset, el('div', 'confirmation-actions', cancel, confirm), message));
  root.replaceChildren(shell);
  dispose = () => { alive = false; };
}

function hostContext(context: McpUiHostContext): void {
  if (context.theme) applyDocumentTheme(context.theme);
  if (context.styles?.variables) applyHostStyleVariables(context.styles.variables);
  // Host-provided typography overrides the fallback font to blend into the conversation.
  if (context.safeAreaInsets) {
    const s = context.safeAreaInsets;
    root.style.padding = `${s.top}px ${s.right}px ${s.bottom}px ${s.left}px`;
  }
}
app.onhostcontextchanged = hostContext;
app.ontoolresult = result => {
  dispose(); dispose = () => {};
  if (result.isError) { failure(root, '读取失败，请在对话中重试或重新连接灵光记。'); return; }
  const payload = record(result.structuredContent);
  if (payload.view === 'meetings') showMeetings(listFrom(payload));
  else if (payload.view === 'todos') showTodos(listFrom(payload));
  else if (payload.view === 'confirmation') {
    const options = array(payload.options).map(value => {
      const item = record(value); return { id: str(item.id), label: str(item.label), description: str(item.description) };
    });
    if (options.length < 2 || !str(payload.requestId) || !str(payload.question)) {
      failure(root, '选项信息不完整，请在对话中重新发起选择。'); return;
    }
    showConfirmation({ view: 'confirmation', requestId: str(payload.requestId), question: str(payload.question), description: str(payload.description), options });
  } else failure(root, '暂时无法显示此内容，请在对话中重新打开。');
};
app.ontoolcancelled = () => { dispose(); root.replaceChildren(state('已取消加载')); };
app.onteardown = async () => { dispose(); return {}; };
applyDocumentTheme(matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
if (window.parent === window) {
  root.replaceChildren(state('请在对话中打开灵光记', '此界面需要支持 MCP Apps 的客户端。'));
} else {
  app.connect().then(() => {
    const context = app.getHostContext(); if (context) hostContext(context);
  }).catch(() => { failure(root, '连接未完成，请回到对话中重新打开灵光记。'); });
}
