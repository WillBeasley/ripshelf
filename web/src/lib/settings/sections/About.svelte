<script lang="ts">
	// Settings: The version, and credit to the data sources in use.
	import { live } from '#lib/live.svelte.ts';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const view = $derived(form.view!);

	// Data sources in use, as saved: ticked in a list with their keys set. TMDB's key is used for
	// every TV disc and every poster, whatever the lists say. Their terms ask for credit.
	const inUse = $derived.by(() => {
		const used = new Set<string>();
		for (const rows of Object.values(view.metadata_providers ?? {}))
			for (const r of rows ?? []) if (r.enabled && form.providerInfo[r.name]?.configured) used.add(r.name);
		if (view.tmdb_api_key_set) used.add('tmdb');
		return used;
	});
</script>

<section class="card stack setting" id="setting-about">
<h2>About</h2>
<p class="small muted">
	ripshelf {live.status?.version ?? ''}{#if live.status?.makemkv_version}
		{` · MakeMKV ${live.status.makemkv_version}`}{/if}
</p>
{#if inUse.has('tmdb')}
	<p class="small">
		<a href="https://www.themoviedb.org" target="_blank" rel="noopener"><img class="tmdb" src="/credits/tmdb.svg" alt="TMDB" /></a>
		This product uses the TMDB API but is not endorsed or certified by TMDB.
	</p>
{/if}
{#if inUse.has('tvdb')}
	<p class="small">
		Metadata provided by <a href="https://thetvdb.com" target="_blank" rel="noopener">TheTVDB</a>. Please consider
		adding missing information or <a href="https://thetvdb.com/subscribe" target="_blank" rel="noopener">subscribing</a>.
	</p>
{/if}
{#if inUse.has('tvmaze')}
	<p class="small">TV data from <a href="https://www.tvmaze.com" target="_blank" rel="noopener">TVmaze</a>, licensed CC BY-SA 4.0.</p>
{/if}
{#if inUse.has('omdb')}
	<p class="small">Film data from <a href="https://www.omdbapi.com" target="_blank" rel="noopener">OMDb</a>, licensed CC BY-NC 4.0.</p>
{/if}
{#if inUse.has('thediscdb')}
	<p class="small">Disc contents from <a href="https://thediscdb.com" target="_blank" rel="noopener">TheDiscDB</a>.</p>
{/if}
<p class="small muted">
	Not affiliated with MakeMKV or any of these services. The software ripshelf includes and its licences are
	listed in THIRD-PARTY-NOTICES.md.
</p>
</section>

<style>
	p {
		margin: 0;
	}
	.tmdb {
		height: 10px;
		width: auto;
		margin-right: 6px;
		vertical-align: baseline;
	}
</style>
