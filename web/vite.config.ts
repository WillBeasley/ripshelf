import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// `npm run dev` proxies the API to a running backend (default the fake-hardware port).
const backend = process.env.RIPSHELF_BACKEND ?? 'http://127.0.0.1:8381';

export default defineConfig({
	plugins: [sveltekit()],
	server: { proxy: { '/api': { target: backend, changeOrigin: true } } }
});
