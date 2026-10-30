<script lang="ts">
	// One kind's metadata providers. Drag (or the arrow buttons, or the arrow keys on
	// the handle) to set the order they are asked in, the checkbox to switch one off.
	import type { ProviderInfo, ProviderSlot } from '$lib/api-types';

	let {
		title,
		hint,
		rows = $bindable(),
		info,
		keyed
	}: {
		title: string;
		hint: string;
		rows: ProviderSlot[];
		info: Record<string, ProviderInfo>;
		keyed: (name: string) => boolean;
	} = $props();

	let dragging = $state<number | null>(null);
	let over = $state<number | null>(null);
	let said = $state('');

	function move(from: number, to: number) {
		if (to < 0 || to >= rows.length || from === to) return;
		const next = [...rows];
		const [r] = next.splice(from, 1);
		next.splice(to, 0, r);
		rows = next;
		said = `${info[r.name]?.label ?? r.name} moved to position ${to + 1} of ${rows.length}`;
	}

	function toggle(i: number, on: boolean) {
		rows = rows.map((r, j) => (j === i ? { ...r, enabled: on } : r));
	}

	function onKey(e: KeyboardEvent, i: number) {
		if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
			e.preventDefault();
			const to = e.key === 'ArrowUp' ? i - 1 : i + 1;
			move(i, to);
			// Keep focus on the moved row's handle.
			queueMicrotask(() =>
				(document.querySelector(`[data-handle="${title}-${to}"]`) as HTMLElement | null)?.focus()
			);
		}
	}

	function drop(e: DragEvent, i: number) {
		e.preventDefault();
		if (dragging !== null) move(dragging, i);
		dragging = over = null;
	}
</script>

<div class="stack tight">
	<div>
		<h3>{title}</h3>
		<p class="small muted">{hint}</p>
	</div>
	<ol class="list">
		{#each rows as r, i (r.name)}
			{@const p = info[r.name]}
			<li
				class:dragging={dragging === i}
				class:over={over === i && dragging !== i}
				class:off={!r.enabled}
				ondragover={(e) => {
					e.preventDefault();
					over = i;
				}}
				ondragleave={() => over === i && (over = null)}
				ondrop={(e) => drop(e, i)}
			>
				<button
					type="button"
					class="handle"
					draggable="true"
					data-handle="{title}-{i}"
					aria-label="Move {p?.label ?? r.name}: drag, or use the arrow keys"
					title="Drag to reorder (or focus and use ↑ ↓)"
					ondragstart={(e) => {
						dragging = i;
						e.dataTransfer?.setData('text/plain', r.name);
						if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
					}}
					ondragend={() => (dragging = over = null)}
					onkeydown={(e) => onKey(e, i)}>⠿</button
				>
				<span class="pos small muted">{i + 1}</span>
				<label class="check grow">
					<input type="checkbox" checked={r.enabled} onchange={(e) => toggle(i, e.currentTarget.checked)} />
					<span class="name">
						<strong>{p?.label ?? r.name}</strong>
						{#if p?.needs_key && !keyed(r.name)}<span class="chip warn">needs key</span>{/if}
						{#if p && !p.needs_key}<span class="chip">no key</span>{/if}
						<span class="small muted desc">{p?.description ?? ''}</span>
					</span>
				</label>
				<span class="arrows">
					<button type="button" aria-label="Move {p?.label ?? r.name} up" disabled={i === 0} onclick={() => move(i, i - 1)}
						>↑</button
					>
					<button
						type="button"
						aria-label="Move {p?.label ?? r.name} down"
						disabled={i === rows.length - 1}
						onclick={() => move(i, i + 1)}>↓</button
					>
				</span>
			</li>
		{/each}
	</ol>
	<p class="sr-only" aria-live="polite">{said}</p>
</div>

<style>
	h3 {
		margin: 0;
		font-size: 1rem;
	}
	p {
		margin: 0;
	}
	.tight {
		gap: 6px;
	}
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
		border: 1px solid var(--border);
		border-radius: 10px;
		overflow: hidden;
	}
	li {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 8px;
		background: var(--surface);
		border-top: 1px solid var(--border);
	}
	li:first-child {
		border-top: none;
	}
	li.dragging {
		opacity: 0.5;
	}
	li.over {
		box-shadow: inset 0 2px 0 var(--accent);
	}
	li.off .name {
		opacity: 0.6;
	}
	.handle {
		cursor: grab;
		min-height: 34px;
		padding: 4px 8px;
		font-size: 1.1rem;
		line-height: 1;
		touch-action: none;
	}
	.pos {
		width: 1.2em;
		text-align: right;
	}
	.grow {
		flex: 1;
		min-width: 0;
		align-items: flex-start;
	}
	.grow input {
		margin-top: 3px;
	}
	.name {
		display: flex;
		flex-wrap: wrap;
		gap: 2px 8px;
		align-items: baseline;
	}
	.desc {
		flex-basis: 100%;
	}
	.arrows {
		display: flex;
		gap: 4px;
	}
	.arrows button {
		min-height: 34px;
		padding: 4px 9px;
	}
	.chip.warn {
		background: var(--warn-bg);
		color: var(--warn);
		border-color: transparent;
	}
	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
	}
</style>
