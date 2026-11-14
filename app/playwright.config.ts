import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8789',
    browserName: 'chromium',
    viewport: { width: 1280, height: 900 },
    launchOptions: { executablePath: process.env.PSYDCAR_TEST_BROWSER },
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'python3 ../tests/app_ui_fixture.py',
    url: 'http://127.0.0.1:8789',
    reuseExistingServer: false,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 2000 },
  },
});
