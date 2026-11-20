<script lang="ts">
	// A dialog listing the covers on the shelf, to pick one: for a job (best match first)
	// or for a drive's next disc. Thumbnails show each cover as it's turned and cropped.
	import type { ShelfSuggestion } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { coverFacts, coverTitle } from '../shelf';

	let {
		intro,
		load,
		onpick
	}: {
		intro: string;
		load: () => Promise<ShelfSuggestion[]>;
		// Use the cover; true once it's used (the dialog closes).
		onpick: (id: string) => Promise<boolean>;
	} = $props();
	const act = new Action();

	let picks = $state<ShelfSuggestion[] | null>(null);
	let query = $state('');
	let dialog = $state<HTMLDialogElement | null>(null);

	export async function show() {
		query = '';
		picks = null;
		dialog?.showModal();
		picks = (await act.run(load)) ?? [];
	}

	async function pick(id: string) {
		if (await act.run(() => onpick(id))) dialog?.close();
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

<dialog bind:this={dialog}>
	<div class="stack picker">
		<div class="spread">
			<strong>Pick a cover from the shelf</strong>
			<button class="link" onclick={() => dialog?.close()} aria-label="Close">✕</button>
		</div>
		<p class="small muted">{intro}</p>
		<input type="search" placeholder="Search by title, year or barcode" bind:value={query} />
		{#if picks === null}
			<p class="small muted"><span class="spinner" aria-hidden="true"></span>Loading…</p>
		{:else if !shown.length}
			<p class="small muted">No cover matches.</p>
		{/if}
		<ul class="picks">
			{#each shown as p (p.cover.id)}
				<li>
					<button class="pick" disabled={act.busy} onclick={() => pick(p.cover.id)}>
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
