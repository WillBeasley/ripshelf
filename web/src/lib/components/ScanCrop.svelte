<script lang="ts">
	// Cropping a scan by hand: the whole scan (turned, not trimmed) with a box to drag by
	// its edges and corners, or set with the sliders. Kept as the scan's crop, so the
	// thumbnails, covers and readings use only what's inside it.
	import { untrack } from 'svelte';
	import type { CoverScan, Job } from '../api-types';
	import { api } from '../api';
	import type { Action } from '../actions.svelte';
	import { shownBox, type Box } from '../crop';
	import CropBox from './CropBox.svelte';

	let { job, sc, act, onclose }: { job: Job; sc: CoverScan; act: Action; onclose: () => void } = $props();

	// From where it's cropped when opened.
	let box = $state<Box>(untrack(() => shownBox(sc.crop, sc.width, sc.height, sc.rotate)));

	const save = () => act.run(() => api.updateScan(job.id, sc.id, { crop: [box.l, box.t, box.r, box.b] })).then((v) => v && onclose());
	const trim = (on: boolean) => act.run(() => api.updateScan(job.id, sc.id, { trim: on })).then((v) => v && onclose());
</script>

<div class="crop">
	<CropBox bind:box>
		<img src={api.scanWholeUrl(job.id, sc)} alt="The whole scan, with the crop" draggable="false" />
	</CropBox>
	<p class="small muted">
		{#if sc.crop_by_hand}Cropped by hand.{:else if sc.crop}Trimmed automatically.{:else}The whole scan.{/if}
		Drag the box's edges or corners, or use the sliders.
	</p>
	<div class="row">
		<button class="primary" disabled={act.busy} onclick={save}>Use this crop</button>
		{#if sc.source !== 'camera'}
			<button disabled={act.busy} title="Find the picture on the glass again" onclick={() => trim(true)}>Trim automatically</button>
		{/if}
		<button disabled={act.busy || !sc.crop} onclick={() => trim(false)}>Whole scan</button>
		<button class="link small" onclick={onclose}>Cancel</button>
	</div>
</div>

<style>
	.crop {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 8px;
		border-radius: 8px;
		background: var(--surface-2);
	}
	p {
		margin: 0;
	}
	img {
		max-width: 100%;
		max-height: 70vh;
		height: auto;
		border-radius: 4px;
	}
	.row {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		align-items: center;
	}
</style>
