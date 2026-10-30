import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

export default {
	preprocess: vitePreprocess(),
	kit: {
		// Single-page app: FastAPI serves index.html for every non-API path.
		adapter: adapter({ fallback: 'index.html' })
	}
};
