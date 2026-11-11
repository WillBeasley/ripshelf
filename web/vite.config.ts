import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

// `npm run dev` proxies the API to a running backend (default the fake-hardware port).
const backend = process.env.RIPSHELF_BACKEND ?? 'http://127.0.0.1:8381';

export default defineConfig({
	plugins: [
		sveltekit({
			preprocess: vitePreprocess(),
			// Single-page app: FastAPI serves index.html for every non-API path.
			adapter: adapter({ fallback: 'index.html' })
		})
	],
	// The backend refuses changes from pages on other addresses (origins.py), so the
	// proxied requests say they come from the backend's own.
	server: {
		proxy: {
			'/api': {
				target: backend,
				changeOrigin: true,
				configure: (proxy) => proxy.on('proxyReq', (req) => req.setHeader('origin', backend))
			}
		}
	},
	// `npm test`: the UI's logic, without a browser. The browser tests are test/e2e (Playwright).
	test: { include: ['test/unit/**/*.test.ts'] },
	// Svelte's browser build in tests, so $state and $derived react as they do in the page.
	resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined
});
