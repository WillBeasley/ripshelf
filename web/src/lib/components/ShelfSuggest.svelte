<script lang="ts">
	// A cover on the shelf that looks like this job's disc, offered (never used without
	// the user), and a picker of every cover on the shelf, best match first.
	import type { Job, ShelfSuggestion } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { coverFacts, coverTitle } from '../shelf';

	let { job }: { job: Job } = $props();
	const act = new Action();

	let suggestions = $state<ShelfSuggestion[]>([]);
	let onShelf = $state(0);
	let dismissed = $state<string | null>(null);
	let picks = $state<ShelfSuggestion[] | null>(null);
	let query = $state('');
	let dialog = $state<HTMLDialogElement | null>(null);

	const hasSheet = $derived(job.scans.some((s) => s.kind === 'outside_full' || s.kind === 'front'));
	const top = $derived(suggestions[0] ?? null);
	// What the suggestions depend on: what the disc may be, and what its box says.
	const key = $derived(
		[job.id, job.identity?.title, job.candidates.length, job.candidates[0]?.title, job.listing?.found?.details.barcode,
			job.contribution?.details.upc, job.scans.length].join('|')
	);

	$effect(() => {
		key;
		const t = setTimeout(async () => {
			try {
				suggestions = await api.shelfSuggestions(job.id);
			} catch {
				suggestions = [];
			}
		}, 300);
		return () => clearTimeout(t);
	});
	$effect(() => {
		job.id;
		api.shelf().then((v) => (onShelf = v.covers.length)).catch(() => {});
	});

	async function openPicker() {
		query = '';
		picks = null;
		dialog?.showModal();
		picks = (await act.run(() => api.shelfSuggestions(job.id, true))) ?? [];
	}

	async function use(id: string) {
		const out = await act.run(() => api.shelfUseInJob(id, job.id));
		if (out) {
			dialog?.close();
			suggestions = suggestions.filter((s) => s.cover.id !== id);
			onShelf = Math.max(0, onShelf - 1);
		}
	}

	const shown = $derived(
		(picks ?? []).filter((p) => {
			const q = query.trim().toLowerCase();
			if (!q) return true;
			const r = p.cover.reading;
			const hay = [r.title, r.year, r.barcode, r.barcode_name, r.catalogue_number].filter(Boolean).join(' ').toLowerCase();
			return q.split(/\s+/).every((w) => hay.includes(w));
		})
	);
</script>

{#if top && !hasSheet && dismissed !== top.cover.id}
	<div class="banner-shelf" role="status">
		{#if top.cover.outside}<img src={api.shelfSideUrl(top.cover, 'outside')} alt="" />{/if}
		<div class="text">
			<div>This cover looks like it's on the shelf: <strong>{coverTitle(top.cover)}</strong></div>
			<div class="small muted">{top.reason}</div>
		</div>
		<div class="row">
			<button class="primary" disabled={act.busy} onclick={() => use(top.cover.id)}>Use it</button>
			<button class="link small" onclick={() => (dismissed = top.cover.id)}>Not this one</button>
		</div>
	</div>
{/if}
{#if onShelf}
	<div><button class="link small" onclick={openPicker}>Pick a cover from the shelf ({onShelf})</button></div>
{/if}
{#if act.error && !dialog?.open}<p class="small error-text">{act.error}</p>{/if}

<dialog bind:this={dialog}>
	<div class="stack picker">
		<div class="spread">
			<strong>Pick a cover from the shelf</strong>
			<button class="link" onclick={() => dialog?.close()} aria-label="Close">✕</button>
		</div>
		<p class="small muted">Its sides become this disc's scans: they're read, help identify it, and are filed with it.</p>
		<input type="search" placeholder="Search by title, year or barcode" bind:value={query} />
		{#if picks === null}
			<p class="small muted"><span class="spinner" aria-hidden="true"></span>Loading…</p>
		{:else if !shown.length}
			<p class="small muted">No cover matches.</p>
		{/if}
		<ul class="picks">
			{#each shown as p (p.cover.id)}
				<li>
					<button class="pick" disabled={act.busy} onclick={() => use(p.cover.id)}>
						{#if p.cover.outside}<img src={api.shelfSideUrl(p.cover, 'outside')} alt="" loading="lazy" />{/if}
						<span class="title">{coverTitle(p.cover)}</span>
						{#if coverFacts(p.cover)}<span class="small muted">{coverFacts(p.cover)}</span>{/if}
						{#if p.score > 0}<span class="small muted">{Math.round(p.score * 100)}% · {p.reason}</span>{/if}
					</button>
				</li>
			{/each}
		</ul>
		{#if act.error}<p class="small error-text">{act.error}</p>{/if}
	</div>
</dialog>

<style>
	.banner-shelf {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		padding: 8px;
		border-radius: 8px;
		background: var(--info-bg);
	}
	.banner-shelf img {
		width: 96px;
		aspect-ratio: 16 / 9;
		object-fit: contain;
		border-radius: 4px;
		background: var(--surface-2);
	}
	.text {
		flex: 1 1 180px;
		min-width: 0;
		overflow-wrap: anywhere;
	}
	dialog {
		padding: 12px;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
		color: var(--text);
		width: min(96vw, 900px);
		max-width: 96vw;
	}
	dialog::backdrop {
		background: rgb(0 0 0 / 0.7);
	}
	.picker {
		gap: 8px;
	}
	.picker p {
		margin: 0;
	}
	.picks {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 160px), 1fr));
		gap: 8px;
		max-height: 60vh;
		overflow: auto;
	}
	.pick {
		width: 100%;
		height: 100%;
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 3px;
		padding: 6px;
		text-align: left;
	}
	.pick img {
		width: 100%;
		aspect-ratio: 16 / 9;
		object-fit: contain;
		border-radius: 4px;
		background: var(--surface-2);
	}
	.title {
		font-weight: 600;
		overflow-wrap: anywhere;
	}
</style>
