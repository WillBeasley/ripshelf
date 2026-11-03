import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

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
	server: { proxy: { '/api': { target: backend, changeOrigin: true } } }
});
