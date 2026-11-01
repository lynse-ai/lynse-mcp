/* Development-only MCP Apps host. Never included in npm or production Docker images. */
const frame = document.getElementById('preview-frame');
const view = new URLSearchParams(location.search).get('view') || 'meetings';
document.querySelector(`[data-view="${['meetings','todos','confirmation'].includes(view) ? view : 'meetings'}"]`).setAttribute('aria-current', 'page');
const preview = window.preview = { messages: [], calls: [], failMessages: false, failTools: {}, delays: {}, theme: 'light', accent: '#0f766e' };
const send = message => frame.contentWindow.postMessage({ jsonrpc: '2.0', ...message }, '*');
const context = () => ({ theme: preview.theme, styles: { variables: {
  '--color-text-info': preview.accent,
  '--color-ring-primary': preview.accent,
  '--color-background-primary': preview.theme === 'dark' ? '#1b1d21' : '#ffffff',
  '--color-background-secondary': preview.theme === 'dark' ? '#24272c' : '#f7f8fa',
  '--color-text-primary': preview.theme === 'dark' ? '#f1f3f5' : '#182230',
  '--color-text-secondary': preview.theme === 'dark' ? '#a5adba' : '#667085',
  '--color-border-primary': preview.theme === 'dark' ? '#383d45' : '#e7e9ed',
} }, displayMode: 'inline', availableDisplayModes: ['inline'] });
preview.notify = params => send({ method: 'ui/notifications/tool-result', params });
preview.setTheme = (theme, accent = preview.accent) => {
  preview.theme = theme; preview.accent = accent;
  document.body.classList.toggle('dark', theme === 'dark');
  document.getElementById('theme').textContent = theme === 'dark' ? '切换浅色' : '切换深色';
  send({ method: 'ui/notifications/host-context-changed', params: context() });
};
document.getElementById('theme').onclick = () => preview.setTheme(preview.theme === 'dark' ? 'light' : 'dark');
document.getElementById('accent').oninput = event => preview.setTheme(preview.theme, event.target.value);
window.addEventListener('message', async event => {
  if (event.source !== frame.contentWindow || event.data?.jsonrpc !== '2.0') return;
  const { id, method, params } = event.data;
  const reply = result => send({ id, result });
  try {
    if (method === 'ui/initialize') reply({ protocolVersion: params.protocolVersion,
      hostInfo: { name: 'Lynse local preview', version: '1.0.0' }, hostCapabilities: { serverTools: {}, message: { text: {} } }, hostContext: context() });
    else if (method === 'ui/notifications/initialized') {
      const result = await fetch(`/api/initial?view=${encodeURIComponent(view)}`).then(response => response.json());
      preview.initial = result; preview.notify(result);
    } else if (method === 'tools/call') {
      preview.calls.push(params);
      if (preview.delays[params.name]) await new Promise(resolve => setTimeout(resolve, preview.delays[params.name]));
      if (preview.failTools[params.name]) { reply({ content: [{ type: 'text', text: '示例：读取失败，请重试' }], isError: true }); return; }
      reply(await fetch('/api/tool', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) }).then(response => response.json()));
    } else if (method === 'ui/message') {
      if (preview.failMessages) { reply({ isError: true }); return; }
      preview.messages.push(params);
      document.getElementById('messages').textContent = params.content.map(item => item.text || '').join('\n');
      reply({});
    } else if (method === 'ui/notifications/size-changed') {
      frame.style.height = `${Math.max(1, Math.min(1800, params.height)) + 2}px`;
    } else if (id !== undefined) send({ id, error: { code: -32601, message: 'Unsupported preview method' } });
  } catch (error) { if (id !== undefined) send({ id, error: { code: -32603, message: String(error) } }); }
});
