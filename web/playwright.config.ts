import { defineConfig } from '@playwright/test';

// `npm run test:e2e`: the built UI in a browser, against the backend on the simulated
// drives (test/e2e/backend.sh), started fresh for the run. The tests share that backend, so
// they run one at a time, the setup first.
const port = Number(process.env.RIPSHELF_E2E_PORT ?? 8391);

export default defineConfig({
	testDir: 'test/e2e',
	workers: 1,
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
	expect: { timeout: 15_000 },
	use: { baseURL: `http://127.0.0.1:${port}`, trace: 'retain-on-failure' },
	projects: [
		{ name: 'setup', testMatch: 'setup.spec.ts' },
		{ name: 'flows', testIgnore: 'setup.spec.ts', dependencies: ['setup'] }
	],
	webServer: {
		command: 'npm run build && test/e2e/backend.sh',
		url: `http://127.0.0.1:${port}/api/status`,
		env: { RIPSHELF_PORT: String(port) },
		timeout: 120_000,
		reuseExistingServer: false
	}
});
