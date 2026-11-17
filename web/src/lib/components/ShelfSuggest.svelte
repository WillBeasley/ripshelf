<script lang="ts">
	// A cover on the shelf that looks like this job's disc, offered (never used without
	// the user), and a picker of every cover on the shelf, best match first.
	import type { Job, ShelfSuggestion } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { live } from '../live.svelte';
	import { coverTitle } from '../shelf';
	import ShelfPicker from './ShelfPicker.svelte';

	let { job }: { job: Job } = $props();
	const act = new Action();

	let suggestions = $state<ShelfSuggestion[]>([]);
	let onShelf = $state(0);
	let dismissed = $state<string | null>(null);
	let picker = $state<ShelfPicker | null>(null);

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

	function taken(id: string) {
		suggestions = suggestions.filter((s) => s.cover.id !== id);
		onShelf = Math.max(0, onShelf - 1);
	}

	async function use(id: string) {
		if (await act.run(() => api.shelfUseInJob(id, job.id))) taken(id);
	}

	// From the picker, which shows its own errors.
	async function pick(id: string): Promise<boolean> {
		live.upsertJob(await api.shelfUseInJob(id, job.id));
		taken(id);
		return true;
	}
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
	<div><button class="link small" onclick={() => picker?.show()}>Pick a cover from the shelf ({onShelf})</button></div>
{/if}
{#if act.error}<p class="small error-text">{act.error}</p>{/if}

<ShelfPicker
	bind:this={picker}
	intro="Its sides become this disc's scans: they're read, help identify it, and are filed with it."
	load={() => api.shelfSuggestions(job.id, true)}
	onpick={pick}
/>

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
</style>
