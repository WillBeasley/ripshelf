<script lang="ts">
	// Editing a shelf cover's side by hand: turn it, mirror it, level a sheet laid askew,
	// and crop it. Nothing is saved until "Save" (turns and mirrors show here first,
	// drawn from the side as it was), so "Cancel" leaves the side as it was. The original
	// is kept, so "As it came" undoes every edit ever made (that one saves at once).
	import { untrack } from 'svelte';
	import type { ShelfCover, ShelfSide } from '../api-types';
	import { api } from '../api';
	import type { Action } from '../actions.svelte';
	import {
		applyView,
		flipDownView,
		flipView,
		mirror,
		mirrorDown,
		NO_CHANGE,
		shownBox,
		turnLeft,
		turnRight,
		turnView,
		type Box,
		type View
	} from '../crop';
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
	let view = $state<View>(NO_CHANGE); // turns and mirrors not saved yet

	const crop = (x: Box): [number, number, number, number] => [x.l, x.t, x.r, x.b];
	const changed = $derived(view.turn !== 0 || view.flip);

	// The whole side as saved, and drawn turned and mirrored as the view says.
	const whole = $derived(api.shelfWholeUrl(cover, side));
	let base = $state<HTMLImageElement | null>(null);
	let drawn = $state<string | null>(null);
	$effect(() => {
		const img = new Image();
		img.onload = () => (base = img);
		img.src = whole;
		return () => (img.onload = null);
	});
	$effect(() => {
		if (!base || !changed) {
			drawn = null;
			return;
		}
		const c = document.createElement('canvas');
		const sideways = view.turn % 180 !== 0;
		c.width = sideways ? base.naturalHeight : base.naturalWidth;
		c.height = sideways ? base.naturalWidth : base.naturalHeight;
		const ctx = c.getContext('2d');
		if (!ctx) return;
		if (view.flip) {
			ctx.translate(c.width, 0);
			ctx.scale(-1, 1);
		}
		ctx.translate(c.width / 2, c.height / 2);
		ctx.rotate((view.turn * Math.PI) / 180);
		ctx.drawImage(base, -base.naturalWidth / 2, -base.naturalHeight / 2);
		let url: string | null = null;
		let gone = false;
		c.toBlob((b) => {
			if (!b || gone) return;
			url = URL.createObjectURL(b);
			drawn = url;
		}, 'image/jpeg', 0.9);
		return () => {
			gone = true;
			if (url) URL.revokeObjectURL(url);
		};
	});

	const turn = (by: 90 | -90) => {
		box = by === 90 ? turnRight(box) : turnLeft(box);
		view = turnView(view, by);
	};
	// Mirrored, a lean shows the other way.
	const flipAcross = () => {
		box = mirror(box);
		view = flipView(view);
		angle = -angle;
	};
	// Upside down and mirrored is mirrored top to bottom.
	const flipDown = () => {
		box = mirrorDown(box);
		view = flipDownView(view);
		angle = -angle;
	};

	async function save() {
		const out = await act.run(() => api.shelfEdit(cover.id, side, {
			...(changed ? applyView(s.rotate, s.flip, view) : {}),
			crop: crop(box),
			straighten: angle
		}));
		if (!out) return;
		await onchange();
		onclose();
	}

	async function reset() {
		const out = await act.run(() => api.shelfEdit(cover.id, side, { reset: true }));
		if (!out) return;
		const x = out[side] as ShelfSide;
		box = shownBox(x.crop, x.width, x.height, x.rotate, x.flip);
		angle = shownAngle(x);
		view = NO_CHANGE;
		await onchange();
	}
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
			<img src={drawn ?? whole} alt="The whole side, with the crop" draggable="false"
				style:transform={angle ? `rotate(${angle}deg)` : undefined} />
		</div>
	</CropBox>
	<p class="small muted">
		{s.by_hand ? 'Edited by hand.' : s.crop ? 'Trimmed automatically.' : 'The whole picture.'}
		Drag the box's edges or corners, or use the sliders. Nothing is kept until you save; the original is kept
		too, and the cover's text is read again once it's saved.
	</p>
	<div class="row">
		<button class="primary" disabled={act.busy} onclick={save}>Save</button>
		<button disabled={act.busy || !(s.by_hand || s.flip || s.straighten || s.rotate)}
			title="Undo every edit, saved straight away: trimmed as it was scanned, not turned, flipped or straightened"
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
