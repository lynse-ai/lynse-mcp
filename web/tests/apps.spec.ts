import { expect, test, type Page } from '@playwright/test';

declare global {
  interface Window {
    preview: {
      messages: { content: { type: string; text: string }[] }[];
      calls: { name: string; arguments: Record<string, unknown> }[];
      initial: { structuredContent: Record<string, unknown>; content: unknown[] };
      failMessages: boolean;
      failTools: Record<string, boolean>;
      delays: Record<string, number>;
      notify: (result: unknown) => void;
      setTheme: (theme: string, accent?: string) => void;
    };
  }
}
const ui = (page: Page) => page.frameLocator('#preview-frame');
async function open(page: Page, url: string): Promise<void> {
  await page.goto(url);
  // Let the host apply the SDK's content-height notification before clicking.
  await expect(ui(page).locator('.shell')).toBeVisible();
  await expect.poll(async () => {
    const size = await ui(page).locator('.shell').evaluate(node => ({
      content: node.getBoundingClientRect().height, viewport: window.innerHeight,
    }));
    return Math.abs(size.content - size.viewport);
  }).toBeLessThan(3);
}
test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.exposeFunction('assertNoPageErrors', () => expect(errors).toEqual([]));
});
test.afterEach(async ({ page }) => {
  await page.evaluate(() => (window as unknown as { assertNoPageErrors: () => Promise<void> }).assertNoPageErrors());
});

test('meeting search, details, tabs, audio playback, speed and refreshed links', async ({ page }) => {
  await open(page, '/?view=meetings');
  const frame = ui(page);
  await expect(frame.getByRole('button', { name: /产品设计周会/ })).toBeVisible();
  await expect(frame.getByRole('searchbox')).toBeHidden();
  await expect(frame.getByRole('region', { name: '会议详情' })).toBeHidden();
  expect(await frame.locator('.shell').evaluate(node => node.getBoundingClientRect().height)).toBeLessThan(280);
  await page.locator('#preview-frame').screenshot({ path: 'web/test-results/meetings-list.png' });
  await frame.getByRole('button', { name: /产品设计周会/ }).click();
  await expect(frame.getByRole('heading', { name: '产品设计周会' })).toBeVisible();
  await expect(frame.getByRole('tabpanel')).toContainText('本次讨论围绕会议回顾');
  await frame.getByRole('tab', { name: '转写全文' }).click();
  await expect(frame.getByRole('tabpanel')).toContainText('[00:15] 陈');
  await frame.getByRole('tab', { name: '大纲', exact: true }).click();
  await expect(frame.getByRole('tabpanel')).toContainText('08:20');
  await frame.getByRole('button', { name: '播放录音', exact: true }).click();
  const audio = frame.locator('audio');
  await expect(audio).toBeVisible();
  await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).readyState)).toBeGreaterThan(0);
  await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).currentTime)).toBeGreaterThan(0);
  await frame.getByRole('combobox', { name: '播放速度' }).selectOption('1.5');
  expect(await audio.evaluate(node => (node as HTMLAudioElement).playbackRate)).toBe(1.5);
  await frame.getByRole('button', { name: '刷新链接', exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.preview.calls.filter(call => call.name === 'get_meeting_audio').length)).toBe(2);
  await audio.evaluate(node => (node as HTMLAudioElement).pause());
  await page.screenshot({ path: 'web/test-results/meetings.png', fullPage: true });
  await frame.getByRole('button', { name: '返回会议', exact: true }).click();
  await frame.getByRole('button', { name: '搜索会议', exact: true }).click();
  await frame.getByRole('searchbox', { name: '搜索会议标题' }).fill('没有这样的会议');
  await frame.getByRole('button', { name: '搜索', exact: true }).click();
  await expect(frame.getByText('没有找到会议')).toBeVisible();
  await frame.getByRole('searchbox', { name: '搜索会议标题' }).fill('产品');
  await frame.getByRole('button', { name: '搜索', exact: true }).click();
  await expect(frame.getByRole('button', { name: /产品设计周会/ })).toBeVisible();
  await expect(frame.getByText('与客户一起，梳理下一步', { exact: true })).toHaveCount(0);
});

test('todo filters, source meeting and return retain the selected filter', async ({ page }) => {
  await open(page, '/?view=todos');
  const frame = ui(page);
  await expect(frame.locator('.todo-row')).toHaveCount(4);
  await frame.getByRole('button', { name: '已完成', exact: true }).click();
  await expect(frame.locator('.todo-row')).toHaveCount(1);
  await frame.getByRole('button', { name: '灵光记 · 秋季版本评审' }).click();
  await expect(frame.getByRole('heading', { name: '灵光记 · 秋季版本评审' })).toBeVisible();
  await frame.getByRole('button', { name: '返回待办' }).click();
  await expect(frame.getByRole('button', { name: '已完成', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(frame.locator('.todo-row')).toHaveCount(1);
  await frame.getByRole('button', { name: '待完成', exact: true }).click();
  await expect(frame.locator('.todo-row')).toHaveCount(3);
  await page.screenshot({ path: 'web/test-results/todos.png', fullPage: true });
  const writes = await page.evaluate(() => window.preview.calls.filter(call => !call.name.startsWith('read_')));
  expect(writes).toEqual([]);
});

test('choice needs explicit confirmation and sends once with question and stable IDs', async ({ page }) => {
  await open(page, '/?view=confirmation');
  const frame = ui(page);
  const confirm = frame.getByRole('button', { name: '确认', exact: true });
  await expect(confirm).toBeDisabled();
  await frame.getByRole('radio', { name: /按时间整理/ }).check();
  await expect(confirm).toBeEnabled();
  expect(await page.evaluate(() => window.preview.messages)).toEqual([]);
  await page.screenshot({ path: 'web/test-results/confirmation.png', fullPage: true });
  await confirm.click();
  await expect(frame.getByText('已发送', { exact: true })).toBeVisible();
  await expect(frame.getByRole('button', { name: '已确认', exact: true })).toBeDisabled();
  const messages = await page.evaluate(() => window.preview.messages);
  expect(messages).toHaveLength(1);
  expect(messages[0].content[0].text).toContain('我确认选择 B：按时间整理');
  expect(messages[0].content[0].text).toContain('问题：这些会议，你想怎样整理？');
  expect(messages[0].content[0].text).toContain('确认编号：');
  expect(await page.evaluate(() => window.preview.calls)).toEqual([]);
});

test('cancel sends an explicit cancellation without granting consent', async ({ page }) => {
  await open(page, '/?view=confirmation');
  const frame = ui(page);
  await frame.getByRole('button', { name: '取消' }).click();
  await expect(frame.locator('.confirmation-status')).toBeVisible();
  const text = await page.evaluate(() => window.preview.messages[0].content[0].text);
  expect(text).toContain('请勿执行');
  await expect(frame.getByRole('radio').first()).toBeDisabled();
});

test('rejected choice stays retryable and is not marked confirmed', async ({ page }) => {
  await open(page, '/?view=confirmation');
  const frame = ui(page);
  await expect(frame.getByRole('radio').first()).toBeVisible();
  await page.evaluate(() => { window.preview.failMessages = true; });
  await frame.getByRole('radio', { name: /按项目归档/ }).check();
  await frame.getByRole('button', { name: '确认', exact: true }).click();
  await expect(frame.getByText(/选择未发送成功/)).toBeVisible();
  await expect(frame.getByRole('button', { name: '确认', exact: true })).toBeEnabled();
  expect(await page.evaluate(() => window.preview.messages)).toEqual([]);
  await page.evaluate(() => { window.preview.failMessages = false; });
  await frame.getByRole('button', { name: '确认', exact: true }).click();
  await expect(frame.getByText('已发送', { exact: true })).toBeVisible();
});

test('theme and emphasis colors update while retaining the current selection', async ({ page }) => {
  await open(page, '/?view=confirmation');
  const frame = ui(page);
  await frame.getByRole('radio', { name: /先看看整理建议/ }).check();
  await page.evaluate(() => window.preview.setTheme('dark', '#d4a96a'));
  await expect(frame.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(frame.locator('.choice[data-selected="true"]')).toHaveCSS('border-color', 'rgb(212, 169, 106)');
  await expect(frame.getByRole('radio', { name: /先看看整理建议/ })).toBeChecked();
  await page.screenshot({ path: 'web/test-results/confirmation-dark.png', fullPage: true });
  await page.evaluate(() => window.preview.setTheme('light', '#b14f40'));
  await expect(frame.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(frame.locator('.choice[data-selected="true"]')).toHaveCSS('border-color', 'rgb(177, 79, 64)');
});

test('errors can be retried and stale detail responses never replace a newer meeting', async ({ page }) => {
  await open(page, '/?view=meetings');
  const frame = ui(page);
  await expect(frame.getByRole('button', { name: /产品设计周会/ })).toBeVisible();
  await page.evaluate(() => { window.preview.failTools.read_meeting_view = true; });
  await frame.getByRole('button', { name: /产品设计周会/ }).click();
  await expect(frame.getByRole('alert')).toContainText('读取失败');
  await page.evaluate(() => { window.preview.failTools.read_meeting_view = false; window.preview.delays.read_meeting_view = 500; });
  await frame.getByRole('button', { name: '重试', exact: true }).click();
  await frame.getByRole('button', { name: '返回会议', exact: true }).click();
  await frame.getByRole('button', { name: /与客户一起/ }).click();
  await expect(frame.getByRole('heading', { name: '与客户一起，梳理下一步' })).toBeVisible();
  await expect(frame.getByRole('tabpanel')).toContainText('本次讨论围绕会议回顾');
  await expect(frame.locator('.meeting-row[data-id="meeting-2"]')).toHaveAttribute('aria-pressed', 'true');
});

test('empty lists and untrusted text render safely', async ({ page }) => {
  await open(page, '/?view=todos');
  const frame = ui(page);
  await expect(frame.locator('.todo-row')).toHaveCount(4);
  await page.evaluate(() => {
    const result = structuredClone(window.preview.initial);
    result.structuredContent.todos = [];
    result.structuredContent.total = 0;
    window.preview.notify(result);
  });
  await expect(frame.getByText('这里暂时没有待办')).toBeVisible();
  await page.evaluate(() => {
    const result = structuredClone(window.preview.initial);
    const todos = result.structuredContent.todos as Record<string, unknown>[];
    todos[0].content = '<img src=x onerror="alert(1)">';
    window.preview.notify(result);
  });
  await expect(frame.getByText('<img src=x onerror="alert(1)">', { exact: true })).toBeVisible();
  expect(await frame.locator('img').count()).toBe(0);
});

test('pagination reads the requested page and preserves the query', async ({ page }) => {
  await open(page, '/?view=meetings');
  const frame = ui(page);
  await expect(frame.getByRole('button', { name: /产品设计周会/ })).toBeVisible();
  await page.evaluate(() => {
    const result = structuredClone(window.preview.initial);
    result.structuredContent.query = { days: 30, keyword: '', page: 1, pageSize: 1 };
    result.structuredContent.meetings = (result.structuredContent.meetings as unknown[]).slice(0, 1);
    result.structuredContent.hasNext = true;
    window.preview.notify(result);
  });
  await frame.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(frame.getByRole('button', { name: /与客户一起/ })).toBeVisible();
  const calls = await page.evaluate(() => window.preview.calls);
  expect(calls.at(-1)?.arguments).toEqual({ days: 30, keyword: '', page: 2, page_size: 1 });
});

test('keyboard selection and narrow screens work without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, '/?view=confirmation');
  const frame = ui(page);
  const first = frame.getByRole('radio').first();
  await first.focus(); await first.press('Space'); await first.press('ArrowDown');
  await expect(frame.getByRole('radio').nth(1)).toBeChecked();
  const metrics = await frame.locator('body').evaluate(body => ({ width: body.scrollWidth, viewport: window.innerWidth }));
  expect(metrics.width).toBeLessThanOrEqual(metrics.viewport);
  await page.screenshot({ path: 'web/test-results/confirmation-mobile.png', fullPage: true });
  await open(page, '/?view=meetings');
  await frame.getByRole('button', { name: /产品设计周会/ }).click();
  await expect(frame.getByRole('tabpanel')).toContainText('本次讨论围绕会议回顾');
  const meetingMetrics = await frame.locator('body').evaluate(body => ({ width: body.scrollWidth, viewport: window.innerWidth }));
  expect(meetingMetrics.width).toBeLessThanOrEqual(meetingMetrics.viewport);
  await page.screenshot({ path: 'web/test-results/meetings-mobile.png', fullPage: true });
});
