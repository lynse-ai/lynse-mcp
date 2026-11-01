import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests', fullyParallel: false, workers: 1, timeout: 25000,
  outputDir: './test-results', reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:8766', viewport: { width: 1100, height: 900 },
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {},
  },
  webServer: {
    command: '../.venv/bin/python ../scripts/preview_apps.py --port 8766',
    url: 'http://127.0.0.1:8766', reuseExistingServer: !process.env.CI,
  },
});
