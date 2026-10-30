<script lang="ts">
	import { page } from '$app/state';
	import type { CollectionDetail, SeasonCell } from '$lib/api-types';
	import { api } from '$lib/api';
	import { openJob } from '$lib/nav';
	import Poster from '$lib/components/Poster.svelte';

	let c = $state<CollectionDetail | null>(null);
	let error = $state<string | null>(null);
	let picked = $state<SeasonCell | null>(null);
	const id = $derived(Number(page.params.id));
	$effect(() => {
		api.collection(id).then((d) => (c = d)).catch((e) => (error = String(e.message ?? e)));
	});
	const code = (s: number, e: number) => `S${String(s).padStart(2, '0')}E${String(e).padStart(2, '0')}`;
</script>

<svelte:head><title>{c?.show.name ?? 'Collection'} · ripshelf</title></svelte:head>

<div class="stack">
	<a href="/collections" class="small">← Collections</a>
	{#if error}<p class="error-text">{error}</p>{/if}
	{#if c}
		<header class="head">
			<Poster url={c.show.poster_url} alt={c.show.name} width={64} />
			<div>
				<h1>{c.show.name}{c.show.year ? ` (${c.show.year})` : ''}</h1>
				<div class="small muted">{c.discs.length} disc{c.discs.length === 1 ? '' : 's'}</div>
			</div>
		</header>

		<div class="legend small">
			<span class="sq present"></span> ripped <span class="sq missing"></span> missing
			<span class="sq duplicate"></span> on two discs
		</div>

		{#each c.seasons as g (g.season)}
			<section class="card stack tight">
				<h2>{g.season === 0 ? 'Specials' : `Season ${g.season}`} <span class="muted small">{g.present} / {g.total}</span></h2>
				<div class="cells">
					{#each g.cells as cell (cell.episode.episode)}
						<button
							class="cell {cell.status}"
							title="{code(cell.episode.season, cell.episode.episode)} {cell.episode.name}"
							onclick={() => (picked = cell)}
						>
							{cell.episode.episode}
						</button>
					{/each}
				</div>
			</section>
		{/each}

		{#if picked}
			<section class="card small">
				<strong>{code(picked.episode.season, picked.episode.episode)} · {picked.episode.name || 'untitled'}</strong>
				<div class="muted">
					{picked.status === 'missing' ? 'Not ripped yet' : picked.status === 'duplicate' ? 'On more than one disc' : 'Ripped'}
					{#each picked.jobs as j (j)}
						· <button class="link small" onclick={() => openJob(j)}>job {j}</button>
					{/each}
				</div>
			</section>
		{/if}

		<section class="card stack tight">
			<h2>Discs</h2>
			<ul class="discs small">
				{#each c.discs as d (d.job_id)}
					<li>
						<button class="link" onclick={() => openJob(d.job_id)}>{d.label ?? `job ${d.job_id}`}</button>
						<span class="muted">
							{d.compilation ? 'compilation' : `season ${d.season ?? '?'}${d.disc_number ? `, disc ${d.disc_number}` : ''}`}
							· {d.episodes.length} episodes</span
						>
					</li>
				{/each}
			</ul>
		</section>
	{/if}
</div>

<style>
	.head {
		display: flex;
		gap: 12px;
		align-items: center;
	}
	.tight {
		gap: 8px;
	}
	.cells {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(38px, 1fr));
		gap: 5px;
	}
	.cell {
		min-height: 34px;
		padding: 0;
		justify-content: center;
		font-variant-numeric: tabular-nums;
		font-size: 0.85rem;
	}
	.cell.present,
	.sq.present {
		background: var(--ok-bg);
		color: var(--ok);
		border-color: transparent;
	}
	.cell.missing,
	.sq.missing {
		background: var(--surface-2);
		color: var(--muted);
	}
	.cell.duplicate,
	.sq.duplicate {
		background: var(--warn-bg);
		color: var(--warn);
		border-color: var(--warn);
	}
	.legend {
		display: flex;
		align-items: center;
		gap: 6px;
		flex-wrap: wrap;
		color: var(--muted);
	}
	.sq {
		display: inline-block;
		width: 14px;
		height: 14px;
		border-radius: 3px;
		border: 1px solid var(--border);
		margin-left: 6px;
	}
	.discs {
		margin: 0;
		padding-left: 18px;
	}
</style>
