<script lang="ts">
	// Which episodes one title holds: a sheet from the bottom on a phone, a panel at the
	// side on a wider screen. Search by name or code ("s2e5", "2x05", "205"), a season at
	// a time, the box's best guesses first. Ticking several episodes, from any seasons,
	// is a title holding more than one. An episode on another title can be taken too:
	// that title gets this one's episodes instead (a swap).
	import type { Episode, EpisodeGuess, EpisodeRef, TitleMap, TitleRole } from '../api-types';
	import { duration } from '../format';

	let {
		row,
		rows,
		episodes,
		boxName = null,
		guesses = [],
		thumb = null,
		busy = false,
		error = null,
		onsave,
		onclose
	}: {
		row: TitleMap;
		rows: TitleMap[];
		episodes: Episode[];
		boxName?: string | null;
		guesses?: EpisodeGuess[];
		thumb?: string | null;
		busy?: boolean;
		error?: string | null;
		onsave: (role: TitleRole, picked: EpisodeRef[]) => void;
		onclose: () => void;
	} = $props();

	const key = (e: EpisodeRef) => `${e.season}:${e.episode}`;
	const code = (e: EpisodeRef) => `S${String(e.season).padStart(2, '0')}E${String(e.episode).padStart(2, '0')}`;
	const rowName = (r: TitleMap) =>
		r.part_of != null ? `Title ${r.part_of}, part ${((r.title_index - 1000) % 100) + 1}` : `Title ${r.title_index}`;

	let dialog = $state<HTMLDialogElement | null>(null);
	let query = $state('');
	let season = $state<number | null>(null);
	let notEpisode = $state(false);
	let thumbFailed = $state(false);
	// The picks, in the order ticked (kept in episode order when saved).
	let picked = $state<string[]>([]);
	let seeded = '';
	$effect(() => {
		const k = `${row.title_index}`;
		if (k !== seeded) {
			seeded = k;
			picked = row.episodes.map(key);
			query = '';
			season = null;
			notEpisode = false;
		}
	});

	let search = $state<HTMLInputElement | null>(null);
	$effect(() => {
		if (dialog && !dialog.open) {
			dialog.showModal();
			// Straight into the search with a keyboard and mouse; a phone's keyboard would
			// hide half the list, so there it waits for a tap.
			if (window.matchMedia?.('(pointer: fine)').matches) search?.focus();
		}
	});

	const byKey = $derived(new Map(episodes.map((e) => [key(e), e])));
	// Which other title holds each episode now.
	const heldBy = $derived.by(() => {
		const out = new Map<string, TitleMap>();
		for (const r of rows) if (r.title_index !== row.title_index) for (const e of r.episodes) out.set(key(e), r);
		return out;
	});
	const seasons = $derived(
		[...new Set(episodes.map((e) => e.season))].sort((a, b) => (a === 0 ? 1 : b === 0 ? -1 : a - b))
	);

	// "s2e5", "S02E05", "2x05", "205" (season 2, episode 5), or words of the name.
	function parseCode(q: string): EpisodeRef | null {
		const t = q.trim().toLowerCase();
		let m = t.match(/^s?(\d{1,2})\s*[ex.]\s*(\d{1,3})$/);
		if (m) return { season: Number(m[1]), episode: Number(m[2]) };
		m = t.match(/^(\d)(\d{2})$/);
		if (m) return { season: Number(m[1]), episode: Number(m[2]) };
		return null;
	}
	const fold = (s: string) =>
		s
			.toLowerCase()
			.normalize('NFKD')
			.replace(/[̀-ͯ]/g, '')
			.replace(/[’']/g, '');
	const shown = $derived.by(() => {
		const q = query.trim();
		const c = q ? parseCode(q) : null;
		const words = fold(q).split(/\s+/).filter(Boolean);
		return episodes.filter((e) => {
			if (season !== null && e.season !== season) return false;
			if (!q) return true;
			if (c && e.season === c.season && e.episode === c.episode) return true;
			const hay = fold(`${code(e)} ${e.name}`);
			return words.every((w) => hay.includes(w));
		});
	});
	const groups = $derived.by(() => {
		const out = new Map<number, Episode[]>();
		for (const e of shown) {
			if (!out.has(e.season)) out.set(e.season, []);
			out.get(e.season)!.push(e);
		}
		return [...out.entries()].sort(([a], [b]) => (a === 0 ? 1 : b === 0 ? -1 : a - b));
	});
	const best = $derived(query.trim() || season !== null ? [] : guesses.filter((g) => byKey.has(key(g))));

	function toggle(k: string) {
		picked = picked.includes(k) ? picked.filter((x) => x !== k) : [...picked, k];
	}

	const pickedEps = $derived(
		picked
			.map((k) => byKey.get(k) ?? { season: Number(k.split(':')[0]), episode: Number(k.split(':')[1]), name: '' })
			.sort((a, b) => a.season - b.season || a.episode - b.episode)
	);
	// Titles that lose an episode to this one.
	const swaps = $derived([...new Set(picked.map((k) => heldBy.get(k)).filter((r): r is TitleMap => !!r))]);
	const useLabel = $derived(
		pickedEps.length === 0
			? 'Pick an episode'
			: pickedEps.length === 1
				? `Use ${code(pickedEps[0])}`
				: `Use ${pickedEps.length} episodes`
	);

	function save() {
		onsave(
			'episode',
			pickedEps.map((e) => ({ season: e.season, episode: e.episode }))
		);
	}

	function close() {
		dialog?.close();
	}
</script>

<dialog bind:this={dialog} class="sheet" aria-labelledby="picker-title" onclose={onclose}
	onclick={(e) => e.target === dialog && close()}>
	<div class="inner">
		<header>
			<div class="spread">
				<h3 id="picker-title">{rowName(row)} · <span class="mono">{duration(row.duration_s)}</span></h3>
				<button class="icon close" aria-label="Close" onclick={close}>✕</button>
			</div>
			<div class="about">
				{#if thumb && !thumbFailed}
					<img src={thumb} alt="A frame from the title" onerror={() => (thumbFailed = true)} />
				{/if}
				{#if boxName}<p class="small muted">Box says “{boxName}”</p>{/if}
			</div>
		</header>

		<div class="filters">
			<label class="search">
				<span class="sr-only">Search episodes by name or code, like s2e5</span>
				<input type="search" bind:this={search} bind:value={query} placeholder="Name, or a code like s2e5" autocomplete="off"
					enterkeyhint="search" />
			</label>
			{#if seasons.length > 1}
				<div class="chips" role="group" aria-label="Season">
					<button class="season" class:on={season === null} aria-pressed={season === null}
						onclick={() => (season = null)}>All seasons</button>
					{#each seasons as s (s)}
						<button class="season" class:on={season === s} aria-pressed={season === s}
							onclick={() => (season = season === s ? null : s)}>{s === 0 ? 'Specials' : `S${s}`}</button>
					{/each}
				</div>
			{/if}
		</div>

		<div class="list">
			{#if best.length}
				<h4>Best guesses: name and running time</h4>
				<ul>
					{#each best as g (key(g))}
						{@render item(byKey.get(key(g))!, true)}
					{/each}
				</ul>
			{/if}
			{#each groups as [s, eps] (s)}
				<h4>{s === 0 ? 'Specials' : `Season ${s}`}</h4>
				<ul>
					{#each eps as e (key(e))}
						{@render item(e, false)}
					{/each}
				</ul>
			{:else}
				<p class="small muted empty">No episode matches “{query}”.</p>
			{/each}
		</div>

		<footer>
			{#if error}<p class="small error-text">{error}</p>{/if}
			{#if swaps.length}
				<p class="small warn">
					{#each swaps as r, i (r.title_index)}{i ? '; ' : ''}{rowName(r)} has {r.episodes
							.filter((e) => picked.includes(key(e)))
							.map(code)
							.join(' & ')} now{/each}. It gets {row.episodes.filter((e) => !picked.includes(key(e))).map(code).join(' & ') ||
						'nothing, to pick again'} instead.
				</p>
			{:else}
				<p class="small muted">Tick more than one if this title holds several episodes, from any season.</p>
			{/if}
			{#if notEpisode}
				<div class="roles" role="group" aria-label="What it is instead">
					<button disabled={busy} onclick={() => onsave('extra', [])}>An extra</button>
					<button disabled={busy} onclick={() => onsave('play_all', [])}>Play all</button>
					<button disabled={busy} onclick={() => onsave('skip', [])}>Skip it</button>
					<button class="link small" onclick={() => (notEpisode = false)}>Back</button>
				</div>
			{:else}
				<div class="buttons">
					<button class="other" disabled={busy} onclick={() => (notEpisode = true)}>Not an episode</button>
					<button class="primary use" disabled={busy || !pickedEps.length} onclick={save}>{useLabel}</button>
				</div>
			{/if}
		</footer>
	</div>
</dialog>

{#snippet item(e: Episode, guess: boolean)}
	{@const k = key(e)}
	{@const holder = heldBy.get(k)}
	{@const g = guess ? guesses.find((x) => key(x) === k) : null}
	<li class:held={holder && !picked.includes(k)}>
		<label>
			<input type="checkbox" checked={picked.includes(k)} onchange={() => toggle(k)} />
			<span class="code">{code(e)}</span>
			<span class="name">
				{e.name || 'No name'}
				{#if g}<span class="small muted note">{e.runtime ? `${e.runtime} min · ` : ''}{g.score >= 0.8 ? 'close name' : 'like the name'}</span>{/if}
			</span>
			<span class="small muted side">{holder ? `on ${rowName(holder).toLowerCase()}` : e.runtime ? `${e.runtime} min` : ''}</span>
		</label>
	</li>
{/snippet}

<style>
	.sheet {
		padding: 0;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		max-width: none;
		max-height: none;
	}
	.sheet::backdrop {
		background: rgb(0 0 0 / 0.55);
	}
	.inner {
		display: flex;
		flex-direction: column;
		height: 100%;
		min-height: 0;
	}
	/* A sheet from the bottom on a phone. */
	@media (max-width: 759px) {
		.sheet {
			margin: auto 0 0;
			width: 100%;
			height: 90dvh;
			border-radius: 16px 16px 0 0;
			border-bottom: none;
		}
		.inner::before {
			content: '';
			align-self: center;
			width: 40px;
			height: 4px;
			border-radius: 2px;
			background: var(--border);
			margin-top: 8px;
		}
	}
	/* A panel at the side on a wider screen. */
	@media (min-width: 760px) {
		.sheet {
			margin: 0 0 0 auto;
			width: min(480px, 100%);
			height: 100dvh;
			border-radius: 0;
			border-right: none;
		}
	}
	header {
		padding: 8px 16px 10px;
		border-bottom: 1px solid var(--border);
	}
	h3 {
		margin: 0;
		font-size: 1.05rem;
		align-self: center;
	}
	.about {
		display: flex;
		gap: 10px;
		align-items: center;
	}
	.about img {
		width: 96px;
		aspect-ratio: 16 / 9;
		object-fit: cover;
		border-radius: 4px;
		background: var(--surface-2);
		flex: none;
	}
	.about p {
		margin: 0;
	}
	.close {
		width: 44px;
		height: 44px;
		justify-content: center;
		background: none;
		border: none;
		margin-right: -10px;
	}
	.filters {
		padding: 12px 16px 8px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.search input {
		width: 100%;
		min-height: 46px;
		font-size: 16px; /* no zoom on focus in iOS */
	}
	.chips {
		display: flex;
		gap: 6px;
		overflow-x: auto;
		padding-bottom: 2px;
	}
	.season {
		min-height: 36px;
		border-radius: 18px;
		white-space: nowrap;
		flex: none;
	}
	.season.on {
		background: var(--accent);
		border-color: var(--accent);
		color: var(--accent-text);
	}
	.list {
		flex: 1;
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
	}
	h4 {
		margin: 0;
		padding: 12px 16px 4px;
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--muted);
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	li label {
		display: flex;
		align-items: center;
		gap: 12px;
		min-height: 52px;
		padding: 4px 16px;
		border-bottom: 1px solid var(--surface-2);
		cursor: pointer;
	}
	li input {
		width: 22px;
		height: 22px;
		flex: none;
		accent-color: var(--accent);
	}
	li.held {
		opacity: 0.5;
	}
	.code {
		font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
		font-size: 0.78rem;
		background: var(--info-bg);
		color: var(--info);
		border-radius: 4px;
		padding: 1px 5px;
		flex: none;
	}
	.name {
		flex: 1;
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.note {
		display: block;
	}
	.side {
		flex: none;
		text-align: right;
	}
	.empty {
		padding: 16px;
	}
	footer {
		border-top: 1px solid var(--border);
		padding: 10px 16px calc(14px + env(safe-area-inset-bottom));
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	footer p {
		margin: 0;
	}
	.warn {
		color: var(--warn);
	}
	.buttons,
	.roles {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		align-items: center;
	}
	.buttons button,
	.roles button:not(.link) {
		min-height: 48px;
	}
	.use {
		flex: 1;
		justify-content: center;
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
