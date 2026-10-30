<script lang="ts">
	import type { Candidate, Job } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { duration, kindLabel, percent, size, via } from '../format';
	import { live } from '../live.svelte';
	import { openJob } from '../nav';
	import Evidence from './Evidence.svelte';
	import Poster from './Poster.svelte';
	import ScoreMeter from './ScoreMeter.svelte';

	let { job }: { job: Job } = $props();
	const act = new Action();

	// null means the best candidate that is not an extra (none if all are).
	let picked = $state<number | null>(null);
	let searching = $state(false);
	let query = $state('');
	let year = $state('');

	const candidates = $derived(job.candidates);
	const bestIndex = $derived(picked ?? candidates.findIndex((c) => !c.excluded));
	const best = $derived<Candidate | null>(bestIndex >= 0 ? (candidates[bestIndex] ?? null) : null);
	const others = $derived(candidates.filter((c) => c !== best).slice(0, 6));
	const progress = $derived(live.progressFor(job));
	const mt = $derived(job.main_title);

	function startSearch() {
		searching = true;
		query = job.disc.name ?? (best?.title || job.disc.label?.replace(/_/g, ' ') || '');
		year = best?.year ? String(best.year) : '';
	}

	async function search(e: SubmitEvent) {
		e.preventDefault();
		const y = /^\d{4}$/.test(year.trim()) ? Number(year.trim()) : null;
		const found = await act.run(() => api.search(job.id, query.trim(), y));
		if (found) {
			picked = null;
			searching = false;
		}
	}

	async function confirm(c: Candidate) {
		await act.run(() => api.confirm(job.id, c));
	}
</script>

<article class="card review">
	<header class="spread">
		<div class="facts">
			<h2 class="mono label">{job.disc.label ?? 'No volume label'}</h2>
			<div class="small muted">
				{kindLabel(job.disc.kind)}{#if job.disc.name && job.disc.name !== job.disc.label}{` · disc title “${job.disc.name}”`}{/if}
			</div>
			{#if mt}
				<div class="small muted">
					{[
						`Main feature ${duration(mt.duration_s)}`,
						size(mt.size_bytes),
						mt.resolution && (mt.framerate ? `${mt.resolution} @ ${mt.framerate} fps` : mt.resolution),
						`${job.disc.title_count} titles`
					]
						.filter(Boolean)
						.join(' · ')}
				</div>
			{/if}
			<div class="small muted">
				{#if job.rip.state === 'running' && progress}
					Ripping meanwhile, {percent(progress.fraction)} done
				{:else if job.rip.state === 'done'}
					Rip finished, waiting for you
				{:else if job.rip.state === 'failed'}
					<span class="error-text">Rip failed</span>
				{/if}
			</div>
		</div>
		<button class="link small" onclick={() => openJob(job.id)}>Details</button>
	</header>

	{#if best && !searching}
		<section class="candidate">
			<Poster url={best.poster_url} alt={best.title} width={84} />
			<div class="stack cand-body">
				<div>
					<h3>{best.title}{best.year ? ` (${best.year})` : ''}</h3>
					<div class="small muted">
						{best.runtime ? `${best.runtime} min` : 'runtime unknown'}{#if best.original_title && best.original_title !== best.title}{` · ${best.original_title}`}{/if}{#if best.providers?.length}{` · ${via(best.providers)}`}{/if}
					</div>
				</div>
				<ScoreMeter score={best.score} threshold={live.threshold} />
			</div>
		</section>
		<Evidence items={best.evidence} />
	{:else if !searching}
		<p class="muted small">
			{candidates.length
				? 'The searches only found extras (making-ofs, shorts). Search by title, or pick one below.'
				: 'No candidates. Search by title instead.'}
		</p>
	{/if}

	{#if searching}
		<form class="stack" onsubmit={search}>
			<div class="search-row">
				<label class="field grow">Title <input bind:value={query} required /></label>
				<label class="field yr">Year <input bind:value={year} inputmode="numeric" maxlength="4" /></label>
			</div>
			<div class="row">
				<button class="primary" type="submit" disabled={act.busy || !query.trim()}>Search TMDB</button>
				<button type="button" onclick={() => (searching = false)}>Back</button>
			</div>
		</form>
	{/if}

	{#if !searching}
		<div class="actions">
			{#if best}
				<button class="primary confirm" disabled={act.busy} onclick={() => confirm(best)}>
					Confirm {best.title}{best.year ? ` (${best.year})` : ''}
				</button>
			{/if}
			<div class="row">
				<button disabled={act.busy} onclick={startSearch}>Search instead</button>
			</div>
			<button class="link small" disabled={act.busy} onclick={() => act.run(() => api.setMedia(job.id, 'tv'))}>
				This is a TV disc
			</button>
		</div>
	{/if}
	{#if act.error}<p class="small error-text">{act.error}</p>{/if}

	{#if others.length && !searching}
		<div>
			<p class="small muted">Other candidates ({others.length})</p>
			<ul class="others">
				{#each others as c (c.tmdb_id ?? c.imdb_id ?? c.title)}
					<li>
						<button class="other" onclick={() => (picked = candidates.indexOf(c))}>
							<Poster url={c.poster_url} alt={c.title} width={36} />
							<span class="grow">
								{c.title}{c.year ? ` (${c.year})` : ''}
								<span class="muted small">{c.runtime ? ` · ${c.runtime} min` : ''}{c.providers?.length ? ` · ${via(c.providers)}` : ''}</span>
								{#if c.excluded}
									<span class="small error-text excl">{c.evidence[0]?.text ?? 'Excluded'}</span>
								{/if}
							</span>
							<span class="small muted">{c.score.toFixed(2)}</span>
						</button>
					</li>
				{/each}
			</ul>
		</div>
	{/if}
</article>

<style>
	.review {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.facts {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.label {
		font-size: 1rem;
	}
	.candidate {
		display: flex;
		gap: 12px;
		align-items: flex-start;
	}
	.cand-body {
		flex: 1;
		min-width: 0;
		gap: 10px;
	}
	.actions {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.confirm {
		width: 100%;
		justify-content: center;
		min-height: 46px;
		font-size: 1rem;
	}
	.search-row {
		display: flex;
		gap: 8px;
	}
	.grow {
		flex: 1;
		min-width: 0;
	}
	.yr {
		width: 5.5em;
	}
	.others {
		list-style: none;
		margin: 8px 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.other {
		width: 100%;
		text-align: left;
		background: none;
	}
	.excl {
		display: block;
	}
	p {
		margin: 0;
	}
</style>
