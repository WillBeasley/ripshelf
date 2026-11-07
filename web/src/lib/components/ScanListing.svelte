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

	let view = $state<ListingView | null>(null);
	let draft = $state<Listing | null>(null);
	let active = $state<number | null>(null);

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
			box: null
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
			country: null, locale: null, aspect_ratio: null, audio: [], subtitles: [], video: []
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

	const num = (v: string) => (v.trim() === '' || isNaN(Number(v)) ? null : Number(v));

	// The scan the rows were read on, shown all the time (so the list doesn't move under
	// the pointer), with the pointed-at row's box marked on it.
	const focus = $derived.by(() => {
		const items = shown?.items ?? [];
		const it = (active !== null ? items[active] : null) ?? items.find((x) => x.scan_id && x.box);
		const sc = it?.scan_id ? (job.scans ?? []).find((s) => s.id === it.scan_id) : null;
		return sc ? { sc, box: active !== null && it?.box ? it.box : null } : null;
	});

	const d = $derived(shown?.details);
	const facts = $derived.by(() => {
		if (!d) return [];
		const out: [string, string][] = [];
		const add = (k: string, v: unknown) => {
			if (v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && !v.length)) out.push([k, String(v)]);
		};
		add('Title', d.title);
		add('Year', d.year);
		add('Starring', d.cast.join(', '));
		add('Running time', d.feature_runtime_min ? `${d.feature_runtime_min} min` : null);
		add('Episodes', d.episode_count);
		add('Barcode', d.barcode ? `${d.barcode}${d.barcode_source === 'text' ? ' (read from its digits)' : ''}` : null);
		add('Catalogue number', d.catalogue_number);
		add('Region', d.region ? `${d.region}${d.region_guessed ? ` (guessed from ${d.video_system} and ${d.country})` : ''}` : null);
		add('Video', [d.video_system, d.aspect_ratio, ...d.video].filter(Boolean).join(' · '));
		add('Country', d.country);
		add('Audio', d.audio.map((a: Listing['details']['audio'][number]) => [a.language, a.format, a.channels, a.note].filter(Boolean).join(' ')).join(', '));
		add(
			'Subtitles',
			d.subtitles.map((s: Listing['details']['subtitles'][number]) => `${s.language}${s.hard_of_hearing ? ' (hard of hearing)' : ''}${s.note ? ` (${s.note})` : ''}`).join(', ')
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
		<div class="where" aria-hidden="true">
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
		</div>
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
					<input
						class="title"
						aria-label="Title"
						placeholder="Title"
						value={it.title}
						oninput={(e) => (edit().items[i].title = (e.currentTarget as HTMLInputElement).value)}
					/>
					{#if it.kind === 'extra'}
						<select
							aria-label="Type"
							value={it.extra_type ?? 'Extra'}
							onchange={(e) => (edit().items[i].extra_type = (e.currentTarget as HTMLSelectElement).value as ExtraType)}
						>
							{#each TYPES as [k, label] (k)}<option value={k}>{label}</option>{/each}
						</select>
					{:else}
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
					<input
						class="n"
						aria-label="Disc"
						placeholder="Disc"
						inputmode="numeric"
						value={it.disc ?? ''}
						oninput={(e) => (edit().items[i].disc = num((e.currentTarget as HTMLInputElement).value))}
					/>
					<button class="icon" aria-label="Remove" title="Remove" onclick={() => edit().items.splice(i, 1)}>✕</button>
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
			{#each facts as [k, v] (k)}<dt>{k}</dt><dd>{v}</dd>{/each}
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
	.where {
		position: relative;
		line-height: 0;
		border-radius: 6px;
		border: 1px solid var(--border);
	}
	.where img {
		width: 100%;
		height: auto;
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
	}
	.items .n {
		width: 4.2em;
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
