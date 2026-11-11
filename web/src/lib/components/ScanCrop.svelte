<script lang="ts">
	// Cropping a scan by hand: the whole scan (turned, not trimmed) with a box to drag by
	// its edges and corners, or set with the sliders. Kept as the scan's crop, so the
	// thumbnails, covers and readings use only what's inside it.
	import { untrack } from 'svelte';
	import type { CoverScan, Job } from '../api-types';
	import { api } from '../api';
	import type { Action } from '../actions.svelte';

	let { job, sc, act, onclose }: { job: Job; sc: CoverScan; act: Action; onclose: () => void } = $props();

	type Box = { l: number; t: number; r: number; b: number };
	type Edge = 'l' | 't' | 'r' | 'b';
	const MIN = 0.05; // the server's least crop, each way

	// The scan's crop (in the original's pixels, before the turn) as fractions of the
	// scan as shown.
	function shown(sc: CoverScan): Box {
		if (!sc.crop) return { l: 0, t: 0, r: 1, b: 1 };
		const [L, T, R, B] = [sc.crop[0] / sc.width, sc.crop[1] / sc.height, sc.crop[2] / sc.width, sc.crop[3] / sc.height];
		switch (sc.rotate) {
			case 90:
				return { l: 1 - B, t: L, r: 1 - T, b: R };
			case 180:
				return { l: 1 - R, t: 1 - B, r: 1 - L, b: 1 - T };
			case 270:
				return { l: T, t: 1 - R, r: B, b: 1 - L };
			default:
				return { l: L, t: T, r: R, b: B };
		}
	}

	let box = $state<Box>(untrack(() => shown(sc))); // from where it's cropped when opened
	let sheet = $state<HTMLDivElement | null>(null);
	let dragging = $state<Edge[] | null>(null);

	const pct = (v: number) => `${(v * 100).toFixed(2)}%`;
	const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

	function set(edge: Edge, v: number) {
		if (edge === 'l') box.l = clamp(v, 0, box.r - MIN);
		else if (edge === 'r') box.r = clamp(v, box.l + MIN, 1);
		else if (edge === 't') box.t = clamp(v, 0, box.b - MIN);
		else box.b = clamp(v, box.t + MIN, 1);
	}

	function grab(e: PointerEvent, edges: Edge[]) {
		e.preventDefault();
		(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
		dragging = edges;
	}

	function drag(e: PointerEvent) {
		if (!dragging || !sheet) return;
		const rect = sheet.getBoundingClientRect();
		const x = (e.clientX - rect.left) / rect.width;
		const y = (e.clientY - rect.top) / rect.height;
		for (const edge of dragging) set(edge, edge === 'l' || edge === 'r' ? x : y);
	}

	const save = () => act.run(() => api.updateScan(job.id, sc.id, { crop: [box.l, box.t, box.r, box.b] })).then((v) => v && onclose());
	const trim = (on: boolean) => act.run(() => api.updateScan(job.id, sc.id, { trim: on })).then((v) => v && onclose());

	const HANDLES: [Edge[], string][] = [
		[['l'], 'w'], [['r'], 'e'], [['t'], 'n'], [['b'], 's'],
		[['l', 't'], 'nw'], [['r', 't'], 'ne'], [['l', 'b'], 'sw'], [['r', 'b'], 'se']
	];
	const SLIDERS: [Edge, string][] = [['l', 'Left'], ['r', 'Right'], ['t', 'Top'], ['b', 'Bottom']];
	const cut = (edge: Edge) => Math.round((edge === 'l' || edge === 't' ? box[edge] : 1 - box[edge]) * 100);
</script>

<div class="crop">
	<div class="sheet" bind:this={sheet}>
		<img src={api.scanWholeUrl(job.id, sc)} alt="The whole scan, with the crop" draggable="false" />
		<div class="shade" style:left="0" style:top="0" style:right="0" style:height={pct(box.t)}></div>
		<div class="shade" style:left="0" style:bottom="0" style:right="0" style:height={pct(1 - box.b)}></div>
		<div class="shade" style:left="0" style:top={pct(box.t)} style:width={pct(box.l)} style:height={pct(box.b - box.t)}></div>
		<div class="shade" style:right="0" style:top={pct(box.t)} style:width={pct(1 - box.r)} style:height={pct(box.b - box.t)}></div>
		<div class="box" class:moving={dragging} style:left={pct(box.l)} style:top={pct(box.t)}
			style:width={pct(box.r - box.l)} style:height={pct(box.b - box.t)}>
			{#each HANDLES as [edges, at] (at)}
				<span class="handle {at}" role="presentation" onpointerdown={(e) => grab(e, edges)} onpointermove={drag}
					onpointerup={() => (dragging = null)} onpointercancel={() => (dragging = null)}></span>
			{/each}
		</div>
	</div>
	<p class="small muted">
		{#if sc.crop_by_hand}Cropped by hand.{:else if sc.crop}Trimmed automatically.{:else}The whole scan.{/if}
		Drag the box's edges or corners, or use the sliders.
	</p>
	<div class="sliders">
		{#each SLIDERS as [edge, label] (edge)}
			<label class="small">{label}: cut {cut(edge)}%
				<input type="range" min="0" max="1" step="0.001" value={box[edge]}
					oninput={(e) => set(edge, Number((e.currentTarget as HTMLInputElement).value))} />
			</label>
		{/each}
	</div>
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
	.sheet {
		position: relative;
		line-height: 0;
		touch-action: none;
		user-select: none;
		align-self: center;
		max-width: 100%;
	}
	.sheet img {
		max-width: 100%;
		max-height: 70vh;
		height: auto;
		border-radius: 4px;
	}
	.shade {
		position: absolute;
		background: rgb(0 0 0 / 0.55);
		pointer-events: none;
	}
	.box {
		position: absolute;
		border: 2px solid var(--accent);
		box-sizing: border-box;
	}
	.handle {
		position: absolute;
		width: 22px;
		height: 22px;
		margin: -11px;
		touch-action: none;
	}
	.handle::after {
		content: '';
		position: absolute;
		inset: 6px;
		border-radius: 3px;
		background: var(--accent);
		border: 1px solid #fff;
	}
	.handle.w,
	.handle.e {
		top: 50%;
		cursor: ew-resize;
	}
	.handle.n,
	.handle.s {
		left: 50%;
		cursor: ns-resize;
	}
	.handle.w,
	.handle.nw,
	.handle.sw {
		left: 0;
	}
	.handle.e,
	.handle.ne,
	.handle.se {
		left: 100%;
	}
	.handle.n,
	.handle.nw,
	.handle.ne {
		top: 0;
	}
	.handle.s,
	.handle.sw,
	.handle.se {
		top: 100%;
	}
	.handle.nw,
	.handle.se {
		cursor: nwse-resize;
	}
	.handle.ne,
	.handle.sw {
		cursor: nesw-resize;
	}
	.sliders {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
		gap: 4px 12px;
	}
	.sliders label {
		display: flex;
		flex-direction: column;
	}
	.sliders input {
		width: 100%;
	}
	.row {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		align-items: center;
	}
</style>
