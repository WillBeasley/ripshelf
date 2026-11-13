<script lang="ts">
	// Editing a shelf cover's side by hand: turn it, mirror it, level a sheet laid askew,
	// and crop it. The original is kept, so "As it came" undoes it all. Turns and mirrors
	// are saved as they're made (carrying the crop and level along); the crop and the
	// level are saved with "Save".
	import { untrack } from 'svelte';
	import type { ShelfCover, ShelfSide } from '../api-types';
	import { api } from '../api';
	import type { Action } from '../actions.svelte';
	import { mirror, mirrorDown, shownBox, turnLeft, turnRight, type Box } from '../crop';
	import CropBox from './CropBox.svelte';

	let { cover, side, act, onchange, onclose }: {
		cover: ShelfCover;
		side: 'outside' | 'inside';
		act: Action;
		onchange: () => Promise<unknown>;
		onclose: () => void;
	} = $props();

	const MAX = 15; // degrees either way, as the server allows
	const s = $derived(cover[side] as ShelfSide);
	const shownAngle = (x: ShelfSide) => (x.flip ? -x.straighten : x.straighten);

	let box = $state<Box>(untrack(() => shownBox(s.crop, s.width, s.height, s.rotate, s.flip)));
	let angle = $state(untrack(() => shownAngle(s)));

	const crop = (x: Box): [number, number, number, number] => [x.l, x.t, x.r, x.b];
	const quarter = (r: number) => (((r % 360) + 360) % 360) as ShelfSide['rotate'];

	// Save the side, then take the box and level from what came back.
	async function send(body: Parameters<typeof api.shelfEdit>[2]) {
		const out = await act.run(() => api.shelfEdit(cover.id, side, body));
		if (!out) return;
		const x = out[side] as ShelfSide;
		box = shownBox(x.crop, x.width, x.height, x.rotate, x.flip);
		angle = shownAngle(x);
		await onchange();
		return out;
	}

	const turn = (by: 90 | -90) =>
		send({ rotate: quarter(s.rotate + by), crop: crop(by === 90 ? turnRight(box) : turnLeft(box)), straighten: angle });
	// Mirrored, a lean shows the other way.
	const flipAcross = () => send({ flip: !s.flip, crop: crop(mirror(box)), straighten: -angle });
	// Upside down and mirrored is mirrored top to bottom.
	const flipDown = () =>
		send({ flip: !s.flip, rotate: quarter(s.rotate + 180), crop: crop(mirrorDown(box)), straighten: -angle });
	const save = () => send({ crop: crop(box), straighten: angle }).then((v) => v && onclose());
	const reset = () => send({ reset: true });
</script>

<div class="edit">
	<div class="row tools">
		<button class="icon" title="Turn a quarter anticlockwise" aria-label="Turn left" disabled={act.busy} onclick={() => turn(-90)}>⟲</button>
		<button class="icon" title="Turn a quarter clockwise" aria-label="Turn right" disabled={act.busy} onclick={() => turn(90)}>⟳</button>
		<button disabled={act.busy} title="Mirror it left to right" onclick={flipAcross}>Flip ↔</button>
		<button disabled={act.busy} title="Mirror it top to bottom" onclick={flipDown}>Flip ↕</button>
		<label class="small level">
			Straighten: {angle > 0 ? '+' : ''}{angle.toFixed(1)}°
			<input type="range" min={-MAX} max={MAX} step="0.1" bind:value={angle} aria-label="Straighten" />
		</label>
		<button class="link small" disabled={act.busy || angle === 0} onclick={() => (angle = 0)}>Level</button>
	</div>
	<CropBox bind:box>
		<div class="frame">
			<img src={api.shelfWholeUrl(cover, side)} alt="The whole side, with the crop" draggable="false"
				style:transform={angle ? `rotate(${angle}deg)` : undefined} />
		</div>
	</CropBox>
	<p class="small muted">
		{s.by_hand ? 'Edited by hand.' : s.crop ? 'Trimmed automatically.' : 'The whole picture.'}
		Drag the box's edges or corners, or use the sliders. The original is kept, and the cover is read again once it's saved.
	</p>
	<div class="row">
		<button class="primary" disabled={act.busy} onclick={save}>Save</button>
		<button disabled={act.busy || !(s.by_hand || s.flip || s.straighten || s.rotate)} title="Undo every edit: trimmed as it was scanned, not turned, flipped or straightened"
			onclick={reset}>As it came</button>
		<button class="link small" onclick={onclose}>Cancel</button>
	</div>
</div>

<style>
	.edit {
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
	/* The level turns the picture within its own frame, as the server does: the corners it
	   turns out of are white, and what it turns past the frame is cut. */
	.frame {
		overflow: hidden;
		background: #fff;
		border-radius: 4px;
	}
	img {
		display: block;
		max-width: 100%;
		max-height: 60vh;
		height: auto;
	}
	.icon {
		padding: 2px 8px;
	}
	.row {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		align-items: center;
	}
	.level {
		display: flex;
		flex-direction: column;
		min-width: 180px;
		flex: 1;
	}
	.level input {
		width: 100%;
	}
</style>
