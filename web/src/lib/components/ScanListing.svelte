<script lang="ts">
	// "What's on the disc": the listing read from the job's scans, editable. What the
	// user saves is kept apart from what reading finds, and reading again never replaces
	// it. Pointing at a row shows where on the scan it was read.
	import type { Job, Listing, ListingItem, ListingView } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';

	let { job }: { job: Job } = $props();
	const act = new Action();

	type ExtraType = NonNullable<ListingItem['extra_type']>;
	const TYPES: [ExtraType, string][] = [
		['Extra', 'Extra'],
		['Featurette', 'Featurette'],
		['DeletedScene', 'Deleted scene'],
		['Trailer', 'Trailer'],
		['Interview', 'Interview'],
		['Scene', 'Scene'],
		['Music', 'Music'],
		['Short', 'Short'],
		['Other', 'Other']
	];

	const SOURCE_LABEL: Record<string, string> = { tesseract: 'Tesseract', vision: 'vision model', both: 'both', you: 'you' };
	const SOURCE_HINT: Record<string, string> = {
		tesseract: 'Read by Tesseract',
		vision: 'Read only by the vision model: unconfirmed',
		both: 'Read by Tesseract and the vision model',
		you: 'Your edit'
	};

	let view = $state<ListingView | null>(null);
	let draft = $state<Listing | null>(null);
	let active = $state<number | null>(null);
	let big = $state(false);

	const sl = $derived(job.listing);
	const phase = $derived(sl?.state ?? 'idle');
	const shown = $derived(draft ?? view?.current ?? null);
	const dirty = $derived(!!draft && JSON.stringify(draft) !== JSON.stringify(view?.current ?? null));

	async function load() {
		try {
			view = await api.listing(job.id);
		} catch {
			view = null;
		}
	}

	// Again whenever a reading finishes or the user's version changes.
	$effect(() => {
		job.id;
		sl?.read_at;
		sl?.state;
		sl?.edited_from;
		JSON.stringify(sl?.edited ?? null);
		load();
	});

	function edit() {
		if (!draft && view?.current) draft = structuredClone($state.snapshot(view.current));
		return draft!;
	}

	function blankItem(kind: ListingItem['kind']): ListingItem {
		return {
			kind,
			title: '',
			number: null,
			runtime_min: null,
			extra_type: kind === 'extra' ? 'Extra' : null,
			disc: null,
			text: null,
			scan_id: null,
			box: null,
			source: 'you',
			show: null
		};
	}

	function add(kind: ListingItem['kind']) {
		if (!draft) draft = view?.current ? structuredClone($state.snapshot(view.current)) : { items: [], details: blankDetails(), notes: [] };
		draft.items.push(blankItem(kind));
	}

	function blankDetails(): Listing['details'] {
		return {
			title: null, year: null, cast: [], feature_runtime_min: null, episode_count: null, barcode: null,
			barcode_source: null, catalogue_number: null, region: null, region_guessed: false, video_system: null,
			country: null, locale: null, aspect_ratio: null, audio: [], subtitles: [], video: [], sources: {}
		};
	}

	async function save() {
		if (!draft) return;
		const clean = { ...draft, items: draft.items.filter((i: ListingItem) => i.title.trim()) };
		const v = await act.run(() => api.saveListing(job.id, clean));
		if (v) {
			view = v;
			draft = null;
		}
	}

	async function useReading() {
		const v = await act.run(() => api.dropListingEdits(job.id));
		if (v) {
			view = v;
			draft = null;
		}
	}

	async function keepMine() {
		if (view?.listing.edited) {
			const v = await act.run(() => api.saveListing(job.id, view!.listing.edited!));
			if (v) view = v;
		}
	}

	// A title box as tall as its title, where the browser can't size it by itself.
	function grow(el: HTMLTextAreaElement) {
		if (CSS.supports('field-sizing', 'content')) return;
		const fit = () => {
			el.style.height = 'auto';
			el.style.height = `${el.scrollHeight + 2}px`;
		};
		fit();
		el.addEventListener('input', fit);
		const seen = new ResizeObserver(fit);
		seen.observe(el);
		return { destroy: () => seen.disconnect() };
	}

	const num = (v: string) => (v.trim() === '' || isNaN(Number(v)) ? null : Number(v));

	// The scan the rows were read on, shown all the time (so the list doesn't move under
	// the pointer), with the pointed-at row's box marked on it.
	const focus = $derived.by(() => {
		const items = shown?.items ?? [];
		const it = (active !== null ? items[active] : null) ?? items.find((x) => x.scan_id && x.box);
		const sc = it?.scan_id ? (job.scans ?? []).find((s) => s.id === it.scan_id) : null;
		return sc ? { sc, box: active !== null && it?.box ? it.box : null } : null;
	});

	// What each line was matched to (a disc title, and the episode when its name is known).
	const code = (e: { season: number; episode: number }) =>
		`S${String(e.season).padStart(2, '0')}E${String(e.episode).padStart(2, '0')}`;
	const matched = $derived.by(() => {
		const names = new Map((job.tv?.episodes ?? []).map((e) => [`${e.season}:${e.episode}`, e.name]));
		const out = new Map<number, string>();
		if (draft) return out; // the match is of the saved listing
		for (const x of job.scan_match?.matches ?? []) {
			if (x.item === null) continue;
			const ep = x.bonus
				? `${x.bonus.show.name} ${code(x.bonus.episode)} ${x.bonus.episode.name}`
				: x.episodes.map(code).join(' & ');
			const name = !x.bonus && x.episodes.length === 1 ? names.get(`${x.episodes[0].season}:${x.episodes[0].episode}`) : null;
			out.set(x.item, [ep && `${ep}${name ? ` ${name}` : ''}`, `title ${x.title_index}`].filter(Boolean).join(' · '));
		}
		return out;
	});
	// Disc numbers only matter on a box of several discs.
	const discs = $derived((shown?.items ?? []).some((x) => x.disc !== null));

	const d = $derived(shown?.details);
	const facts = $derived.by(() => {
		if (!d) return [];
		const out: [string, string, string | null][] = [];
		// Each fact with who read it, when that was the vision model (or the barcode's bars).
		const add = (k: string, v: unknown, ...fields: string[]) => {
			if (v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)) return;
			const by = fields.map((f) => d.sources?.[f]).find((x) => x === 'vision' || x === 'barcode') ?? null;
			out.push([k, String(v), by]);
		};
		add('Title', d.title, 'title');
		add('Year', d.year, 'year');
		add('Starring', d.cast.join(', '), 'cast');
		add('Running time', d.feature_runtime_min ? `${d.feature_runtime_min} min` : null, 'feature_runtime_min');
		add('Episodes', d.episode_count, 'episode_count');
		add('Barcode', d.barcode ? `${d.barcode}${d.barcode_source === 'text' ? ' (read from its digits)' : ''}` : null, 'barcode');
		add('Catalogue number', d.catalogue_number, 'catalogue_number');
		add('Region', d.region ? `${d.region}${d.region_guessed ? ` (guessed from ${d.video_system} and ${d.country})` : ''}` : null, 'region');
		add('Video', [d.video_system, d.aspect_ratio, ...d.video].filter(Boolean).join(' · '), 'video_system', 'aspect_ratio');
		add('Country', d.country, 'country');
		add('Audio', d.audio.map((a: Listing['details']['audio'][number]) => [a.language, a.format, a.channels, a.note].filter(Boolean).join(' ')).join(', '), 'audio');
		add(
			'Subtitles',
			d.subtitles.map((s: Listing['details']['subtitles'][number]) => `${s.language}${s.hard_of_hearing ? ' (hard of hearing)' : ''}${s.note ? ` (${s.note})` : ''}`).join(', '),
			'subtitles'
		);
		return out;
	});
</script>

<div class="listing stack">
	<div class="spread">
		<h4>What's on the disc</h4>
		{#if phase !== 'reading' && phase !== 'unavailable' && (job.scans ?? []).length}
			<button class="link small" disabled={act.busy} onclick={() => act.run(() => api.readAgain(job.id))}>Read again</button>
		{/if}
	</div>

	{#if phase === 'reading'}
		<p class="small status" role="status"><span class="spinner" aria-hidden="true"></span>{sl?.progress ?? 'Reading the scans…'}</p>
	{:else if phase === 'unavailable'}
		<p class="small muted">Text reading isn't available here: {sl?.message}. Install Tesseract (see the cover scans guide), or type the listing in below.</p>
	{:else if phase === 'failed'}
		<p class="small error-text">{sl?.message}</p>
	{:else if !shown && !(job.scans ?? []).length}
		<p class="small muted">Add a scan of the back or inside of the box, and what it lists shows here.</p>
	{/if}

	{#if view?.newer_reading && !draft}
		<div class="newer small" role="alert">
			The scans were read again after you edited this, and the reading differs.
			<div class="row">
				<button class="link small" disabled={act.busy} onclick={useReading}>Use the new reading</button>
				<button class="link small" disabled={act.busy} onclick={keepMine}>Keep mine</button>
			</div>
		</div>
	{/if}

	{#if focus}
		<button class="where" class:big aria-label={big ? 'Show the scan smaller' : 'Show the scan larger'}
			onclick={() => (big = !big)}>
			<img src={api.scanThumbUrl(job.id, focus.sc, true)} alt="" />
			{#if focus.box}
				<div
					class="mark"
					style:left="{focus.box[0] * 100}%"
					style:top="{focus.box[1] * 100}%"
					style:width="{(focus.box[2] - focus.box[0]) * 100}%"
					style:height="{(focus.box[3] - focus.box[1]) * 100}%"
				></div>
			{/if}
		</button>
	{/if}

	{#if shown && (shown.items.length || draft)}
		<ul class="items">
			{#each shown.items as it, i (i)}
				<!-- Pointing at a row, or tapping into it, shows where it was read. -->
				<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
				<li
					class:active={active === i}
					onmouseenter={() => (active = i)}
					onmouseleave={() => (active = null)}
					onfocusin={() => (active = i)}
				>
					<select
						aria-label="Episode or extra"
						value={it.kind}
						onchange={(e) => {
							const x = edit().items[i];
							x.kind = (e.currentTarget as HTMLSelectElement).value as ListingItem['kind'];
							x.extra_type = x.kind === 'extra' ? (x.extra_type ?? 'Extra') : null;
						}}
					>
						<option value="episode">Episode</option>
						<option value="extra">Extra</option>
					</select>
					<textarea
						class="title"
						aria-label="Title"
						placeholder="Title"
						rows="1"
						use:grow
						value={it.title}
						oninput={(e) => (edit().items[i].title = (e.currentTarget as HTMLTextAreaElement).value.replace(/\n/g, ' '))}
					></textarea>
					{#if it.kind === 'extra'}
						<select
							aria-label="Type"
							value={it.extra_type ?? 'Extra'}
							onchange={(e) => (edit().items[i].extra_type = (e.currentTarget as HTMLSelectElement).value as ExtraType)}
						>
							{#each TYPES as [k, label] (k)}<option value={k}>{label}</option>{/each}
						</select>
					{:else if it.number !== null || !matched.has(i)}
						<input
							class="n"
							aria-label="Episode number"
							placeholder="Ep."
							inputmode="numeric"
							value={it.number ?? ''}
							oninput={(e) => (edit().items[i].number = num((e.currentTarget as HTMLInputElement).value))}
						/>
					{/if}
					<input
						class="n"
						aria-label="Minutes"
						placeholder="Min"
						inputmode="decimal"
						value={it.runtime_min ?? ''}
						oninput={(e) => (edit().items[i].runtime_min = num((e.currentTarget as HTMLInputElement).value))}
					/>
					{#if discs}
						<input
							class="n"
							aria-label="Disc"
							placeholder="Disc"
							inputmode="numeric"
							value={it.disc ?? ''}
							oninput={(e) => (edit().items[i].disc = num((e.currentTarget as HTMLInputElement).value))}
						/>
					{/if}
					<button class="icon" aria-label="Remove" title="Remove" onclick={() => edit().items.splice(i, 1)}>✕</button>
					{#if it.source}
						<span class="src {it.source}" title={SOURCE_HINT[it.source]}>{SOURCE_LABEL[it.source]}</span>
					{/if}
					{#if it.show}<div class="read small muted">A bonus episode of another show: {it.show}</div>{/if}
					{#if matched.has(i)}<div class="read small matched">→ {matched.get(i)}</div>{/if}
					{#if it.source === 'vision'}<div class="read small warn">Only the vision model read this: check it against the box.</div>{/if}
					{#if it.text && it.text !== it.title}<div class="read small muted">Read as “{it.text}”</div>{/if}
				</li>
			{/each}
		</ul>
	{/if}

	{#if shown || phase === 'done' || phase === 'unavailable'}
		<div class="row">
			<button class="small" onclick={() => add('episode')}>Add episode</button>
			<button class="small" onclick={() => add('extra')}>Add extra</button>
			{#if dirty}
				<button class="primary small" disabled={act.busy} onclick={save}>Save listing</button>
				<button class="link small" onclick={() => (draft = null)}>Undo changes</button>
			{:else if view?.listing.edited && !view.newer_reading}
				<span class="small muted">Your edits</span>
				<button class="link small" disabled={act.busy} onclick={useReading}>Back to what was read</button>
			{/if}
		</div>
	{/if}

	{#if facts.length}
		<dl class="facts small">
			{#each facts as [k, v, by] (k)}<dt>{k}</dt><dd>{v}{#if by}<span class="src {by}">{by === 'vision' ? 'vision model' : 'from the bars'}</span>{/if}</dd>{/each}
		</dl>
	{/if}
	{#each shown?.notes ?? [] as n, i (i)}<p class="small muted">{n}</p>{/each}
	{#if act.error}<p class="small error-text">{act.error}</p>{/if}
</div>

<style>
	.listing {
		gap: 8px;
		padding-top: 6px;
		border-top: 1px solid var(--border);
	}
	h4 {
		margin: 0;
		font-size: 0.85rem;
	}
	p {
		margin: 0;
	}
	.status {
		display: flex;
		align-items: center;
		gap: 6px;
		color: var(--info);
	}
	.spinner {
		width: 12px;
		height: 12px;
		border: 2px solid currentColor;
		border-right-color: transparent;
		border-radius: 50%;
		animation: spin 0.9s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	.newer {
		padding: 8px 10px;
		border-radius: 8px;
		background: var(--warn-bg);
		color: var(--warn);
	}
	/* Small until tapped, so the rows stay in view on a phone. */
	.where {
		position: relative;
		align-self: flex-start;
		display: inline-block;
		padding: 0;
		line-height: 0;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: none;
		cursor: zoom-in;
	}
	.where img {
		display: block;
		max-width: 100%;
		max-height: 180px;
		width: auto;
		height: auto;
	}
	.where.big {
		cursor: zoom-out;
	}
	.where.big img {
		max-height: none;
		width: 100%;
	}
	.mark {
		position: absolute;
		border: 2px solid var(--warn);
		background: color-mix(in srgb, var(--warn) 25%, transparent);
		border-radius: 3px;
		margin: -3px;
		padding: 3px;
		box-sizing: content-box;
	}
	.items {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.items li {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		align-items: center;
		padding: 4px;
		border-radius: 6px;
	}
	.items li.active {
		background: var(--surface-2);
	}
	.items .title {
		flex: 1 1 160px;
		min-width: 0;
		resize: none;
		field-sizing: content;
		font: inherit;
		overflow-wrap: anywhere;
	}
	.items .n {
		width: 3.6em;
	}
	.matched {
		color: var(--ok);
	}
	@media (max-width: 560px) {
		/* The title on a line of its own, the small fields under it. */
		.items .title {
			flex-basis: 100%;
			order: -1;
		}
		.items .n {
			width: 3.2em;
		}
	}
	.items select {
		max-width: 9em;
	}
	.read {
		flex-basis: 100%;
		overflow-wrap: anywhere;
	}
	.icon {
		padding: 2px 8px;
	}
	.src {
		font-size: 0.7rem;
		padding: 0 6px;
		margin-left: 4px;
		border-radius: 999px;
		border: 1px solid var(--border);
		color: var(--muted);
		white-space: nowrap;
	}
	.src.both {
		color: var(--ok);
		border-color: color-mix(in srgb, var(--ok) 40%, transparent);
	}
	.src.vision {
		color: var(--warn);
		border-color: color-mix(in srgb, var(--warn) 50%, transparent);
	}
	.src.you {
		color: var(--info);
		border-color: color-mix(in srgb, var(--info) 50%, transparent);
	}
	.warn {
		color: var(--warn);
	}
	.facts {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 2px 10px;
		margin: 0;
	}
	.facts dt {
		color: var(--muted);
	}
	.facts dd {
		margin: 0;
		overflow-wrap: anywhere;
	}
</style>
