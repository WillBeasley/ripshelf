<script lang="ts">
	import type { Collection } from '$lib/api-types';
	import { api } from '$lib/api';
	import Poster from '$lib/components/Poster.svelte';

	let items = $state<Collection[] | null>(null);
	let error = $state<string | null>(null);
	$effect(() => {
		api.collections().then((c) => (items = c)).catch((e) => (error = String(e.message ?? e)));
	});
	const episodes = (c: Collection) => c.discs.reduce((n, d) => n + d.episodes.length, 0);
</script>

<svelte:head><title>Collections · ripshelf</title></svelte:head>

<div class="stack">
	<h1>Collections</h1>
	{#if error}<p class="error-text">{error}</p>{/if}
	{#if items && !items.length}
		<p class="muted">No TV shows yet. Confirming a TV disc's episodes starts a collection for its show.</p>
	{/if}
	<ul class="list">
		{#each items ?? [] as c (c.id)}
			<li>
				<a class="card item" href="/collections/{c.id}">
					<Poster url={c.show.poster_url} alt={c.show.name} width={48} />
					<div>
						<strong>{c.show.name}{c.show.year ? ` (${c.show.year})` : ''}</strong>
						<div class="small muted">{c.discs.length} disc{c.discs.length === 1 ? '' : 's'} · {episodes(c)} episodes</div>
					</div>
				</a>
			</li>
		{/each}
	</ul>
</div>

<style>
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.item {
		display: flex;
		gap: 12px;
		align-items: center;
		color: inherit;
		text-decoration: none;
	}
</style>
