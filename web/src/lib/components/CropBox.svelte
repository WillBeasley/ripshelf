<script lang="ts">
	// A box to crop a picture with: drag its edges and corners over the picture (the
	// `children`, drawn at the box's full size), or set it with the sliders.
	import type { Snippet } from 'svelte';
	import type { Box } from '../crop';

	let { box = $bindable(), children }: { box: Box; children: Snippet } = $props();

	type Edge = 'l' | 't' | 'r' | 'b';
	const MIN = 0.05; // the server's least crop, each way

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

	const HANDLES: [Edge[], string][] = [
		[['l'], 'w'], [['r'], 'e'], [['t'], 'n'], [['b'], 's'],
		[['l', 't'], 'nw'], [['r', 't'], 'ne'], [['l', 'b'], 'sw'], [['r', 'b'], 'se']
	];
	const SLIDERS: [Edge, string][] = [['l', 'Left'], ['r', 'Right'], ['t', 'Top'], ['b', 'Bottom']];
	const cut = (edge: Edge) => Math.round((edge === 'l' || edge === 't' ? box[edge] : 1 - box[edge]) * 100);
</script>

<div class="sheet" bind:this={sheet}>
	{@render children()}
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
<div class="sliders">
	{#each SLIDERS as [edge, label] (edge)}
		<label class="small">{label}: cut {cut(edge)}%
			<input type="range" min="0" max="1" step="0.001" value={box[edge]}
				oninput={(e) => set(edge, Number((e.currentTarget as HTMLInputElement).value))} />
		</label>
	{/each}
</div>

<style>
	.sheet {
		position: relative;
		line-height: 0;
		touch-action: none;
		user-select: none;
		align-self: center;
		max-width: 100%;
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
</style>
