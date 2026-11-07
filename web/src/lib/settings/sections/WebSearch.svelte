<script lang="ts">
	// Settings: Web searches (SearXNG), and looking catalogue numbers up.
	import TestButton from '#lib/components/TestButton.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const view = $derived(form.view!);
</script>

<section class="card stack" id="web-search">
	<h2>Web search and catalogue numbers</h2>
	<p class="small muted">
		With a self-hosted SearXNG, Review can search the web for a disc nothing identified, and give the names
		it finds to TMDB.
	</p>
	<label class="field">SearXNG address <input bind:value={f.searxng_url} placeholder="http://searxng:8080" /></label>
	<p class="small muted">JSON output must be on. Add <code>json</code> to <code>search.formats</code> in its settings.yml.</p>
	<p class="small muted">
		Some DVDs are labelled with the publisher's catalogue number. A web search for it finds shop listings;
		the title several of them agree on is searched on TMDB, and its runtime still has to fit.
	</p>
	<label class="field">
		Look catalogue numbers up with
		<select bind:value={f.catalogue_search}>
			<option value="off">Off</option>
			<option value="searxng">SearXNG (self-hosted)</option>
		</select>
	</label>
	<TestButton check="catalogue" values={form.body} label="Test (looks up EO51710)" />
</section>

<style>
	p {
		margin: 0;
	}
</style>
