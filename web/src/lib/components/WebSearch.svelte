<script lang="ts">
	// Search the web (the SearXNG in Settings) by hand, for a disc nothing identified.
	// Each result's page title gives a name to search TMDB for.
	import type { Job, WebResult } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { live } from '../live.svelte';

	let {
		job,
		onUse,
		useLabel = 'Search TMDB for'
	}: { job: Job; onUse: (name: string) => void; useLabel?: string } = $props();
	const act = new Action();

	function firstQuery(): string {
		const name = job.disc.name ?? job.disc.label?.replace(/_/g, ' ') ?? '';
		const kind = job.disc.kind === 'bluray' || job.disc.kind === 'uhd' ? 'blu-ray' : 'dvd';
		return name ? `"${name}" ${kind}` : '';
	}

	let query = $state(firstQuery());
	let results = $state<WebResult[] | null>(null);
	const enabled = $derived(live.status?.web_search ?? false);

	function host(url: string): string {
		try {
			return new URL(url).hostname.replace(/^www\./, '');
		} catch {
			return url;
		}
	}

	async function search(e: SubmitEvent) {
		e.preventDefault();
		const found = await act.run(() => api.webSearch(query.trim()));
		if (found) results = found;
	}
</script>

<div class="stack tight">
	{#if enabled}
		<form class="search-row" onsubmit={search}>
			<input aria-label="Web search" bind:value={query} placeholder="Label, catalogue number or barcode" />
			<button type="submit" disabled={act.busy || !query.trim()}>{act.busy ? 'Searching…' : 'Search the web'}</button>
		</form>
		{#if act.error}<p class="small error-text">{act.error}</p>{/if}
		{#if results}
			<ul class="results">
				{#each results as r (r.url)}
					<li>
						<a href={r.url} target="_blank" rel="noreferrer noopener">{r.title}</a>
						<span class="small muted host">{host(r.url)}</span>
						{#if r.snippet}<p class="small muted snippet">{r.snippet}</p>{/if}
						{#if r.suggestion}
							<button class="small" onclick={() => onUse(r.suggestion!)}>{useLabel} “{r.suggestion}”</button>
						{/if}
					</li>
				{:else}
					<li class="small muted">No pages found. Try fewer words, or the barcode from the case.</li>
				{/each}
			</ul>
		{/if}
	{:else}
		<p class="small muted">
			To search the web from here, set a SearXNG address in <a href="/settings#web-search">Settings</a>.
		</p>
	{/if}
</div>

<style>
	.tight {
		gap: 8px;
	}
	.search-row {
		display: flex;
		gap: 8px;
	}
	.search-row input {
		flex: 1;
		min-width: 0;
	}
	.results {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.results li {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 3px;
		min-width: 0;
	}
	.results a {
		overflow-wrap: anywhere;
	}
	.snippet {
		margin: 0;
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.host {
		overflow-wrap: anywhere;
	}
</style>
