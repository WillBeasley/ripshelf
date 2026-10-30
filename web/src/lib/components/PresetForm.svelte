<script lang="ts">
	// Say what a disc is before its scan is done, and the job uses it instead of identifying.
	import { untrack } from 'svelte';
	import type { Candidate, Job, MediaKind, Show } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import Poster from './Poster.svelte';

	let { job, onclose }: { job: Job; onclose: () => void } = $props();
	const act = new Action();

	// The form starts from what was said before (it is edited, not kept in sync).
	const was = untrack(() => ({ preset: job.preset, media: job.media, disc: job.disc }));
	let media = $state<MediaKind>(was.preset?.media ?? was.media);
	let query = $state(was.disc.name ?? was.disc.label?.replace(/_/g, ' ') ?? '');
	let year = $state('');
	let films = $state<Candidate[] | null>(null);
	let shows = $state<Show[] | null>(null);
	let show = $state<Show | null>(was.preset?.show ?? null);
	let season = $state(was.preset?.season != null ? String(was.preset.season) : '');
	let disc = $state(was.preset?.disc_number ? String(was.preset.disc_number) : '');
	let start = $state(was.preset?.start_episode ? String(was.preset.start_episode) : '');

	const num = (v: string) => (/^\d+$/.test(v.trim()) ? Number(v.trim()) : null);

	async function search(e: SubmitEvent) {
		e.preventDefault();
		if (media === 'movie') {
			films = (await act.run(() => api.movieSearch(query.trim(), num(year)))) ?? null;
		} else {
			shows = (await act.run(() => api.tvSearch(query.trim()))) ?? null;
			show = null;
		}
	}

	async function save(candidate: Candidate | null) {
		const body = {
			media,
			candidate,
			show: media === 'tv' ? show : null,
			season: media === 'tv' ? num(season) : null,
			disc_number: media === 'tv' ? num(disc) : null,
			start_episode: media === 'tv' ? num(start) : null
		};
		if (await act.run(() => api.setPreset(job.id, body))) onclose();
	}
</script>

<div class="preset">
	<div class="row">
		<label class="check"><input type="radio" bind:group={media} value="movie" /> Film</label>
		<label class="check"><input type="radio" bind:group={media} value="tv" /> TV show</label>
	</div>
	<form class="search-row" onsubmit={search}>
		<input placeholder={media === 'movie' ? 'Film title' : 'Show name'} bind:value={query} />
		{#if media === 'movie'}
			<input class="yr" placeholder="Year" bind:value={year} inputmode="numeric" maxlength="4" />
		{/if}
		<button type="submit" disabled={act.busy || !query.trim()}>Search</button>
	</form>

	{#if media === 'movie' && films}
		<ul class="list">
			{#each films.slice(0, 6) as c (c.tmdb_id ?? c.title)}
				<li>
					<button class="other" disabled={act.busy} onclick={() => save(c)}>
						<Poster url={c.poster_url} alt={c.title} width={32} />
						<span class="grow">
							{c.title}{c.year ? ` (${c.year})` : ''}
							<span class="muted small">{c.runtime ? ` · ${c.runtime} min` : ''}</span>
						</span>
					</button>
				</li>
			{:else}
				<li class="small muted">No films found.</li>
			{/each}
		</ul>
	{:else if media === 'tv' && show}
		<div class="row">
			<Poster url={show.poster_url} alt={show.name} width={32} />
			<strong class="grow">{show.name}{show.year ? ` (${show.year})` : ''}</strong>
			<button class="link small" onclick={() => (show = null)}>Change</button>
		</div>
		<div class="search-row">
			<label class="field tiny">Season <input bind:value={season} inputmode="numeric" /></label>
			<label class="field tiny">Disc <input bind:value={disc} inputmode="numeric" /></label>
			<label class="field tiny">From ep. <input bind:value={start} inputmode="numeric" /></label>
			<button class="primary" disabled={act.busy} onclick={() => save(null)}>Use this</button>
		</div>
		<p class="small muted">Leave season and disc empty to carry on from the discs already in the collection.</p>
	{:else if media === 'tv' && shows}
		<ul class="list">
			{#each shows.slice(0, 6) as s (s.tmdb_id)}
				<li>
					<button class="other" onclick={() => (show = s)}>
						<Poster url={s.poster_url} alt={s.name} width={32} />
						<span class="grow">{s.name}{s.year ? ` (${s.year})` : ''}</span>
					</button>
				</li>
			{:else}
				<li class="small muted">No shows found.</li>
			{/each}
		</ul>
	{/if}

	<div class="row">
		<button class="link small" onclick={onclose}>Close</button>
	</div>
	{#if act.error}<p class="small error-text">{act.error}</p>{/if}
</div>

<style>
	.preset {
		background: var(--surface-2);
		border-radius: 9px;
		padding: 10px 12px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.search-row {
		display: flex;
		gap: 8px;
		align-items: flex-end;
		flex-wrap: wrap;
	}
	.search-row > input {
		flex: 1;
		min-width: 0;
	}
	.search-row > input.yr {
		flex: none;
		width: 5em;
	}
	.tiny {
		width: 5.2em;
	}
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.other {
		width: 100%;
		text-align: left;
		background: none;
	}
	.grow {
		flex: 1;
		min-width: 0;
	}
	p {
		margin: 0;
	}
</style>
