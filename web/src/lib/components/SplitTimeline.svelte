<script lang="ts">
	import type { Job, TitleSplit } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { duration, percent } from '../format';

	// A long title holding several of the disc's episodes: the marks it can be cut at (its
	// chapters, or breaks found in it, or cuts put in by hand), and where it's cut. ``split``
	// says which (else the disc's first); ``brief`` leaves out the heading the Review card has.
	let { job, split = null, brief = false }: { job: Job; split?: TitleSplit | null; brief?: boolean } = $props();
	const act = new Action();

	const sp = $derived(split ?? job.tv?.splits?.[0] ?? null);
	const which = $derived(sp?.title_index ?? null);
	const changeable = $derived(['review', 'identifying', 'ripping', 'failed', 'checking', 'clash'].includes(job.state));
	const editable = $derived(changeable && sp?.state === 'ready');

	// Local copy of the cuts, reset whenever the server's change.
	let cuts = $state<number[]>([]);
	let serverKey = '';
	$effect(() => {
		const key = JSON.stringify(sp?.cuts ?? []);
		if (key !== serverKey) {
			serverKey = key;
			cuts = JSON.parse(key);
		}
	});
	const dirty = $derived(JSON.stringify(cuts) !== serverKey);
	const proposed = $derived(JSON.stringify(cuts) === JSON.stringify(sp?.proposed ?? []));
	const total = $derived(sp?.duration_s || 1);
	const pos = (t: number) => `${(100 * t) / total}%`;
	const parts = $derived.by(() => {
		if (!sp) return [];
		const starts = [0, ...cuts];
		return starts.map((c, k) => ({
			k,
			first: c,
			start: c === 0 ? 0 : sp.chapters[c],
			end: k + 1 < starts.length ? sp.chapters[starts[k + 1]] : total
		}));
	});

	function toggle(k: number) {
		if (!editable || k === 0) return;
		cuts = cuts.includes(k) ? cuts.filter((c) => c !== k) : [...cuts, k].sort((a, b) => a - b);
	}

	const breakLabel = (b: number | null | undefined) =>
		b == null ? 'not checked' : b >= 1 ? 'black and silence' : b > 0 ? 'black or silence' : 'no break seen';
	const scanned = $derived(sp?.marks_from === 'scan');
	const kind = (k: number) => sp?.kinds[k] ?? 'chapter';
	// "chapter 3", "break 2", "your cut", numbered among their own kind.
	function markName(k: number): string {
		const own = sp ? sp.chapters.slice(0, k + 1).filter((_, i) => kind(i) === kind(k)).length : 0;
		return kind(k) === 'manual' ? 'your cut' : kind(k) === 'break' ? `break ${own}` : `chapter ${own}`;
	}
	let thumbFailed = $state<Record<number, boolean>>({});

	// A cut anywhere: clicked on the bar or typed. The server moves it to a break within 10 s.
	let typed = $state('');
	function seconds(text: string): number | null {
		const bits = text.trim().split(':');
		if (!bits.every((b) => /^\d+(\.\d+)?$/.test(b)) || bits.length > 3) return null;
		return bits.reduce((acc, b) => acc * 60 + Number(b), 0);
	}
	const typedAt = $derived(seconds(typed));
	async function cutAt(at: number) {
		if (!editable || !(at > 0 && at < total)) return;
		const done = await act.run(() => api.tvSplit(job.id, { cuts, off: null, cut_at: at, title_index: which }));
		if (done) typed = '';
	}
	function clickBar(e: MouseEvent) {
		if (!e.detail) return; // Enter or Space, which has no position
		const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
		cutAt(((e.clientX - box.left) / box.width) * total);
	}
</script>

{#snippet thumb(mark: number)}
	{#if sp && !thumbFailed[sp.chapters[mark]]}
		<img
			src={api.splitThumbUrl(job.id, sp.title_index, mark, sp.chapters[mark])}
			alt=""
			loading="lazy"
			onerror={() => (thumbFailed[sp.chapters[mark]] = true)}
		/>
	{:else}
		<div class="nothumb"></div>
	{/if}
{/snippet}

{#if sp}
	<section class="split">
		{#if !brief}
			<div>
				<h3>Title {sp.title_index} holds about {sp.expected} episodes</h3>
				<p class="small muted">
					{sp.reason.charAt(0).toUpperCase()}{sp.reason.slice(1)}. It's split {scanned
						? 'at black frames and silence found in it'
						: 'at chapter marks'}, without re-encoding.
				</p>
			</div>
		{/if}

		{#if sp.state === 'waiting' && !brief}
			<p class="small muted">
				Its chapters are read once it's ripped, and the cuts are proposed then. With too few chapters, it's
				searched for black frames and silence instead, which takes a few minutes.
			</p>
		{:else if sp.state === 'waiting'}
			<!-- said by the Review card -->
		{:else if sp.state === 'failed'}
			<p class="small error-text">It can't be split: {sp.error}.</p>
		{:else}
			<p class="small">
				{sp.state === 'split' ? `Split into ${parts.length} files` : `${parts.length} episodes`} from
				{scanned ? `${sp.chapters.length - 1} breaks found` : `${sp.chapters.length} chapters`}{sp.confidence != null && !sp.edited
					? ` · ${percent(sp.confidence)} confident`
					: ''}{sp.note && !sp.edited ? ` · ${sp.note}` : ''}{sp.edited ? ' · cuts changed by hand' : ''}
			</p>

			<div class="timeline" role="group" aria-label="Marks of title {sp.title_index}">
				{#if editable}
					<button
						class="bar"
						tabindex="-1"
						disabled={act.busy}
						aria-label="Cut where you click"
						title="Cut here (moved to a break within 10 s)"
						onclick={clickBar}
					></button>
				{/if}
				{#each parts as p (p.k)}
					<div class="part" class:alt={p.k % 2 === 1} style:left={pos(p.start)} style:width={pos(p.end - p.start)}>
						<span>{p.k + 1}</span>
					</div>
				{/each}
				{#each sp.chapters as t, k (k)}
					{#if k > 0}
						<button
							class="tick"
							class:cut={cuts.includes(k)}
							class:brk={(sp.breaks[k] ?? 0) >= 1}
							class:manual={kind(k) === 'manual'}
							style:left={pos(t)}
							disabled={!editable || act.busy}
							aria-pressed={cuts.includes(k)}
							aria-label="Cut at {markName(k)} ({duration(t)})"
							title="{markName(k)} at {duration(t)}: {breakLabel(sp.breaks[k])}"
							onclick={() => toggle(k)}
						></button>
					{/if}
				{/each}
			</div>
			<div class="scale small muted"><span>0:00</span><span>{duration(total)}</span></div>

			<ol class="cuts">
				{#each parts as p (p.k)}
					<li>
						{@render thumb(p.first)}
						<div class="small">
							<strong>Part {p.k + 1}</strong> · {duration(p.end - p.start)}
							<div class="muted">
								{p.first === 0 ? 'from the start' : `at ${markName(p.first)}, ${duration(p.start)}`}{p.first
									? ` · ${breakLabel(sp.breaks[p.first])}`
									: ''}
							</div>
						</div>
					</li>
				{/each}
			</ol>

			{#if editable}
				<details>
					<summary class="small">
						{scanned ? `Every break found (${sp.chapters.length - 1})` : `Every chapter (${sp.chapters.length})`}, to cut
						somewhere else
					</summary>
					<ul class="marks">
						{#each sp.chapters as t, k (k)}
							{#if k > 0}
								<li class:on={cuts.includes(k)}>
									<label>
										{@render thumb(k)}
										<span class="small">
											<input type="checkbox" checked={cuts.includes(k)} disabled={act.busy} onchange={() => toggle(k)} />
											{markName(k).charAt(0).toUpperCase()}{markName(k).slice(1)} · {duration(t)}
										</span>
										<span class="small muted">{breakLabel(sp.breaks[k])}</span>
									</label>
								</li>
							{/if}
						{/each}
					</ul>
				</details>
				<form class="row" onsubmit={(e) => (e.preventDefault(), typedAt != null && cutAt(typedAt))}>
					<label class="field tiny">Cut at <input bind:value={typed} placeholder="25:10" inputmode="decimal" /></label>
					<button type="submit" disabled={act.busy || typedAt == null || !(typedAt > 0 && typedAt < total)}>Add cut</button>
				</form>
				<div class="row">
					<button class="primary" disabled={!dirty || act.busy} onclick={() => act.run(() => api.tvSplit(job.id, { cuts, off: null, cut_at: null, title_index: which }))}>
						Use these cuts
					</button>
					<button disabled={act.busy || proposed} onclick={() => (cuts = [...(sp?.proposed ?? [])])}>
						Back to the proposal
					</button>
				</div>
				<p class="small muted">
					Click a mark to cut there or not, or click anywhere else on the bar (or type a time) to cut there. A new
					cut moves to black frames or silence within 10 s, and the file is cut at the next keyframe. New cuts
					propose the episodes again, so pick episodes once the cuts are right.
				</p>
			{/if}
		{/if}

		{#if changeable && sp.state !== 'split'}
			<button class="link small keep" disabled={act.busy} onclick={() => act.run(() => api.tvSplit(job.id, { cuts: null, off: true, cut_at: null, title_index: which }))}>
				Keep it as one title
			</button>
		{/if}
		{#if act.error}<p class="small error-text">{act.error}</p>{/if}
	</section>
{:else if (job.tv?.split_off || job.tv?.whole?.length) && changeable}
	<button class="link small" disabled={act.busy} onclick={() => act.run(() => api.tvSplit(job.id, { cuts: null, off: false, cut_at: null, title_index: null }))}>
		Split the long title into episodes after all
	</button>
	{#if act.error}<p class="small error-text">{act.error}</p>{/if}
{/if}

<style>
	.split {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	h3 {
		margin: 0;
		font-size: 0.95rem;
	}
	p {
		margin: 0;
	}
	.timeline {
		position: relative;
		height: 34px;
		border-radius: 6px;
		background: var(--track);
		overflow: hidden;
		margin-top: 4px;
	}
	.part {
		position: absolute;
		top: 0;
		bottom: 0;
		background: var(--info-bg);
		display: flex;
		align-items: flex-start;
		padding: 2px 0 0 6px;
		font-size: 0.7rem;
		color: var(--info);
		pointer-events: none;
	}
	.part.alt {
		background: var(--surface-2);
	}
	.tick {
		position: absolute;
		top: 0;
		bottom: 0;
		width: 14px;
		margin-left: -7px;
		padding: 0;
		border: none;
		border-radius: 0;
		background: transparent;
		cursor: pointer;
		min-height: 0;
	}
	.tick::after {
		content: '';
		position: absolute;
		left: 6px;
		top: 9px;
		bottom: 9px;
		width: 2px;
		background: var(--muted);
		opacity: 0.6;
	}
	.tick.brk::after {
		top: 5px;
		bottom: 5px;
	}
	.tick.manual::after {
		background: var(--warn);
		opacity: 1;
	}
	.bar {
		position: absolute;
		inset: 0;
		padding: 0;
		border: none;
		border-radius: 0;
		background: transparent;
		cursor: crosshair;
		min-height: 0;
	}
	.tick.cut::after {
		top: 0;
		bottom: 0;
		width: 3px;
		left: 5.5px;
		background: var(--accent);
		opacity: 1;
	}
	.tick:disabled {
		cursor: default;
	}
	.tick:not(:disabled):hover::after {
		background: var(--text);
		opacity: 1;
	}
	.scale {
		display: flex;
		justify-content: space-between;
		margin-top: -4px;
	}
	.cuts {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 8px;
	}
	.cuts li {
		display: flex;
		gap: 8px;
		align-items: flex-start;
	}
	.cuts img,
	.cuts .nothumb {
		width: 64px;
		flex: none;
	}
	img,
	.nothumb {
		aspect-ratio: 16 / 9;
		object-fit: cover;
		border-radius: 4px;
		background: var(--surface-2);
		display: block;
	}
	.marks {
		list-style: none;
		margin: 8px 0 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
		gap: 8px;
	}
	.marks label {
		display: flex;
		flex-direction: column;
		gap: 2px;
		cursor: pointer;
	}
	.marks img,
	.marks .nothumb {
		width: 100%;
	}
	.marks input[type='checkbox'] {
		width: auto;
		height: auto;
		margin: 0 4px 0 0;
		vertical-align: middle;
	}
	.marks li.on img {
		outline: 2px solid var(--accent);
	}
	.row {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}
	.tiny {
		width: 7em;
	}
	.row {
		align-items: flex-end;
	}
	.keep {
		align-self: flex-start;
	}
	summary {
		cursor: pointer;
		color: var(--muted);
	}
</style>
