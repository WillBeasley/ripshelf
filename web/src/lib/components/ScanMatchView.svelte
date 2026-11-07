<script lang="ts">
	// "Matched from the cover scan": each disc title with the listing line it was matched
	// to, how sure, and why; then the lines and titles nothing matched. A film's extras
	// the box names can be ripped from here while the disc is still in the drive.
	import type { Job, TitleMatch } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { duration } from '../format';

	let { job }: { job: Job } = $props();
	const act = new Action();

	const m = $derived(job.scan_match);
	const listing = $derived(job.listing?.edited ?? job.listing?.found ?? null);
	const code = (e: { season: number; episode: number }) =>
		`S${String(e.season).padStart(2, '0')}E${String(e.episode).padStart(2, '0')}`;
	const TYPE: Record<string, string> = {
		Extra: 'extra', Featurette: 'featurette', DeletedScene: 'deleted scenes', Trailer: 'trailer',
		Interview: 'interview', Scene: 'scene', Music: 'music', Short: 'short', Other: 'other'
	};

	// Made again when what it's compared with changes (the TV mapping, the titles).
	let lastKey = '';
	$effect(() => {
		const key = JSON.stringify([job.id, job.tv?.mapping ?? null, job.disc.title_count ?? 0, job.main_title?.index]);
		if (key !== lastKey && job.listing) {
			lastKey = key;
			api.scanMatch(job.id).catch(() => {});
		}
	});

	function what(x: TitleMatch): string {
		if (x.kind === 'feature') return `the film${x.name ? `, ${x.name}` : ''}`;
		if (x.kind === 'episode') return `${x.episodes.map(code).join(' & ')}${x.episodes.length ? ' ' : ''}“${x.name}”`;
		return `“${x.name}” (${TYPE[x.extra_type ?? 'Extra']})`;
	}

	let open = $state<number | null>(null);
	const toRip = $derived(
		job.media === 'movie' && job.rip.state === 'done' && job.mode !== 'backup'
			? (m?.matches ?? []).filter((x) => x.kind === 'extra' && x.confidence !== 'low' && !(x.title_index in (job.rip.files ?? {})))
			: []
	);
</script>

{#if m && (m.matches.length || m.unmatched_items.length)}
	<div class="match stack">
		<div class="spread">
			<h4>Matched from the cover scan</h4>
			{#if m.confidence}<span class="pill {m.confidence}">{m.confidence}</span>{/if}
		</div>
		<ul>
			{#each m.matches as x (x.title_index)}
				<li class:disagree={x.agrees === false}>
					<button class="item" aria-expanded={open === x.title_index}
						onclick={() => (open = open === x.title_index ? null : x.title_index)} title={x.reason}>
						<span class="t small">Title {x.title_index} <span class="muted">· {duration(x.duration_s)}</span></span>
						<span class="w">{what(x)}</span>
						<span class="pill {x.confidence}">{x.confidence}</span>
					</button>
					{#if x.agrees === false}<div class="small warn-text">ripshelf has it differently: {x.current}</div>{/if}
					{#if open === x.title_index}<div class="small muted why">{x.reason}{x.agrees ? '. ripshelf agrees.' : ''}</div>{/if}
				</li>
			{/each}
		</ul>
		{#if m.unmatched_items.length && listing}
			<p class="small muted">
				No title fits: {m.unmatched_items.map((i) => listing.items[i]?.title).filter(Boolean).join(', ')}
			</p>
		{/if}
		{#if m.unmatched_titles.length}
			<p class="small muted">Titles the box doesn't list: {m.unmatched_titles.join(', ')}</p>
		{/if}
		{#if m.claimed.length && listing}
			<p class="small muted">On another disc of the set: {m.claimed.map((i) => listing.items[i]?.title).filter(Boolean).join(', ')}</p>
		{/if}
		{#each m.notes as n, i (i)}<p class="small muted">{n}</p>{/each}
		{#if toRip.length}
			<div class="row">
				<button disabled={act.busy} onclick={() => act.run(() => api.ripExtras(job.id))}>
					Rip the {toRip.length} extra{toRip.length === 1 ? '' : 's'} it names
				</button>
				<span class="small muted">Needs the disc in its drive. They're filed with the film.</span>
			</div>
		{/if}
		{#if act.error}<p class="small error-text">{act.error}</p>{/if}
	</div>
{/if}

<style>
	.match {
		gap: 6px;
		padding-top: 6px;
		border-top: 1px solid var(--border);
	}
	h4 {
		margin: 0;
		font-size: 0.85rem;
	}
	p {
		margin: 0;
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.item {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 8px;
		width: 100%;
		text-align: left;
		padding: 6px 8px;
		background: var(--surface-2);
		border-radius: 6px;
	}
	.t {
		flex: 0 0 auto;
	}
	.w {
		flex: 1 1 12em;
		min-width: 0;
		overflow-wrap: anywhere;
	}
	li.disagree .item {
		outline: 1px solid var(--warn);
	}
	.why {
		padding: 2px 8px;
	}
	.warn-text {
		color: var(--warn);
		padding: 0 8px;
	}
	.pill {
		font-size: 0.72rem;
		padding: 1px 8px;
		border-radius: 999px;
		background: var(--surface);
		border: 1px solid var(--border);
	}
	.pill.high {
		color: var(--ok);
		background: var(--ok-bg);
		border-color: transparent;
	}
	.pill.medium {
		color: var(--warn);
		background: var(--warn-bg);
		border-color: transparent;
	}
	.pill.low {
		color: var(--muted);
	}
</style>
