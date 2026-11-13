<script lang="ts">
	// The covers shelf: case sheets scanned ahead of their discs, read in the background,
	// and used when their disc goes in (from the job, or for a drive's next disc here).
	import type { ScannerInfo, ShelfCover, ShelfView } from '#lib/api-types.ts';
	import { api } from '#lib/api.ts';
	import { Action } from '#lib/actions.svelte.ts';
	import { live } from '#lib/live.svelte.ts';
	import { when } from '#lib/format.ts';
	import { coverFacts, coverTitle, READ_STATE, VISION_STATE } from '#lib/shelf.ts';
	import ShelfStack from '#lib/components/ShelfStack.svelte';
	import ShelfEdit from '#lib/components/ShelfEdit.svelte';

	const act = new Action();
	let view = $state<ShelfView | null>(null);
	let scanner = $state<ScannerInfo | null>(null);
	let query = $state('');
	let error = $state<string | null>(null);
	let notes = $state<string[]>([]);

	// Upload choices.
	let bothSides = $state(true);
	let layout = $state<'alternate' | 'passes'>('alternate');
	let insides = $state<File[]>([]);

	let dialog = $state<HTMLDialogElement | null>(null);
	let openId = $state<string | null>(null);
	const open = $derived(view?.covers.find((c) => c.id === openId) ?? null);
	let side = $state<'outside' | 'inside'>('outside');
	let editing = $state(false);
	let drive = $state('');

	async function load() {
		try {
			view = await api.shelf(query);
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	$effect(() => {
		query;
		const t = setTimeout(load, 200);
		return () => clearTimeout(t);
	});
	$effect(() => {
		api.scanner().then((s) => (scanner = s)).catch(() => {});
	});

	// Look again while something changes on the server: a stack being scanned, covers being read.
	const busy = $derived(
		view?.stack?.state === 'scanning' ||
			(view?.covers ?? []).some(
				(c) =>
					c.reading.state === 'waiting' ||
					c.reading.state === 'reading' ||
					c.reading.vision_state === 'queued' ||
					c.reading.vision_state === 'reading'
			)
	);
	$effect(() => {
		if (!busy) return;
		const t = setInterval(load, 2000);
		return () => clearInterval(t);
	});

	async function upload(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const files = [...(input.files ?? [])];
		input.value = '';
		if (!files.length) return;
		const out = await act.run(() => api.shelfUpload(files, bothSides ? insides : [], bothSides, layout));
		insides = [];
		notes = out?.notes ?? [];
		if (out?.stack) notes = [...notes, 'The two passes are held below until they pair up.'];
		await load();
	}

	function pickInsides(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		insides = [...(input.files ?? [])];
		input.value = '';
	}

	function show(c: ShelfCover) {
		openId = c.id;
		side = c.outside ? 'outside' : 'inside';
		editing = false;
		drive = live.drives[0]?.id ?? '';
		dialog?.showModal();
	}

	async function doThen(fn: () => Promise<unknown>, close = false) {
		const ok = await act.run(fn);
		if (ok !== undefined && close) dialog?.close();
		await load();
	}
</script>

<svelte:head><title>Covers · ripshelf</title></svelte:head>

<div class="stack">
	<div class="spread">
		<h1>Covers</h1>
		{#if view}<span class="small muted count">{view.covers.length}{query.trim() ? ' found' : ` of ${view.limit}`}</span>{/if}
	</div>
	<p class="muted small intro">
		Scan the sheets from your cases ahead of their discs. Each one is read, and when its disc goes in the job offers it: its
		pictures then help identify the disc and are filed with it.
	</p>

	<section class="card stack add">
		<ShelfStack stack={view?.stack ?? null} {scanner} onchange={load} />
		<div class="upload stack">
			<label class="check small"><input type="checkbox" bind:checked={bothSides} /> Both sides of each sheet</label>
			{#if bothSides}
				<div class="row small">
					<label class="check"><input type="radio" name="layout" value="alternate" bind:group={layout} /> Each sheet's sides one after the other</label>
					<label class="check"><input type="radio" name="layout" value="passes" bind:group={layout} /> All the outsides, then all the insides (turned stack)</label>
				</div>
				{#if layout === 'passes'}
					<div class="row small">
						<label class="button" class:disabled={act.busy}>
							Insides in a separate file…
							<input type="file" accept="image/*,application/pdf" multiple onchange={pickInsides} />
						</label>
						<span class="muted">
							{insides.length ? `${insides.length} file${insides.length === 1 ? '' : 's'} of insides, in the order the turned stack came out` : 'Or leave this, if one PDF holds both passes back to back.'}
						</span>
					</div>
				{/if}
			{/if}
			<div class="row">
				<label class="button" class:disabled={act.busy}>
					{act.busy ? 'Adding…' : 'Upload pictures or PDFs'}
					<input type="file" accept="image/*,application/pdf" multiple disabled={act.busy} onchange={upload} />
				</label>
			</div>
		</div>
		{#each notes as n (n)}<p class="small muted">{n}</p>{/each}
		{#if act.error}<p class="small error-text">{act.error}</p>{/if}
	</section>

	<label class="search">
		<span class="sr-only">Search the covers</span>
		<input type="search" placeholder="Search by title, year or barcode" bind:value={query} />
	</label>
	{#if error}<p class="error-text">{error}</p>{/if}
	{#if view && !view.covers.length}
		<p class="muted">{query.trim() ? 'No cover matches.' : 'The shelf is empty.'}</p>
	{/if}

	<ul class="covers">
		{#each view?.covers ?? [] as c (c.id)}
			<li>
				<button class="cover" onclick={() => show(c)}>
					{#if c.outside}
						<img src={api.shelfSideUrl(c, 'outside')} alt="" loading="lazy" />
					{:else if c.inside}
						<img src={api.shelfSideUrl(c, 'inside')} alt="" loading="lazy" />
					{/if}
					<span class="title">{coverTitle(c)}</span>
					{#if coverFacts(c)}<span class="small muted mono-ish">{coverFacts(c)}</span>{/if}
					{#if c.reading.state !== 'done'}
						<span class="small muted">
							{#if c.reading.state === 'reading' || c.reading.state === 'waiting'}<span class="spinner" aria-hidden="true"></span>{/if}
							{READ_STATE[c.reading.state]}
						</span>
					{/if}
					{#if !c.inside}<span class="chip">Outside only</span>{/if}
				</button>
			</li>
		{/each}
	</ul>
</div>

<dialog bind:this={dialog} onclose={() => ((openId = null), (editing = false))}>
	{#if open}
		<div class="stack big">
			<div class="spread">
				<div>
					<strong>{coverTitle(open)}</strong>
					<div class="small muted">{coverFacts(open)}</div>
				</div>
				<button class="link" onclick={() => dialog?.close()} aria-label="Close">✕</button>
			</div>
			{#if open.outside && open.inside}
				<div class="row" role="tablist">
					<button class:primary={side === 'outside'} role="tab" aria-selected={side === 'outside'} onclick={() => ((side = 'outside'), (editing = false))}>Outside</button>
					<button class:primary={side === 'inside'} role="tab" aria-selected={side === 'inside'} onclick={() => ((side = 'inside'), (editing = false))}>Inside</button>
				</div>
			{/if}
			{#if open[side] && editing}
				{#key `${open.id}-${side}`}
					<ShelfEdit cover={open} {side} {act} onchange={load} onclose={() => (editing = false)} />
				{/key}
			{:else if open[side]}
				<img src={api.shelfSideUrl(open, side, true)} alt={side === 'outside' ? 'The outside of the sheet' : 'The inside of the sheet'} />
			{/if}
			<div class="small muted">
				{READ_STATE[open.reading.state]}{open.reading.message ? `: ${open.reading.message}` : ''}
				{#if open.reading.vision_state} · {VISION_STATE[open.reading.vision_state]}{open.reading.vision_message ? ` (${open.reading.vision_message})` : ''}{/if}
				{#if open.reading.barcode_name && open.reading.title} · The barcode is “{open.reading.barcode_name}”{/if}
				· Added {when(open.created_at)}{open.source === 'scanner' ? ' from the scanner' : ''}
			</div>
			{#if open.note}<p class="small muted">{open.note}</p>{/if}
			<div class="row">
				{#if !editing && open[side]}
					<button disabled={act.busy} onclick={() => (editing = true)}
						title="Crop, turn, flip or straighten this side (the original is kept)">Edit…</button>
				{/if}
				<button disabled={act.busy || editing || !open.inside} onclick={() => doThen(() => api.shelfSwap(open!.id))}
					title="The outside and the inside were taken the wrong way round">Swap sides</button>
				<button disabled={act.busy} onclick={() => doThen(() => api.shelfReadAgain(open!.id))}>Read again</button>
				{#if scanner?.vision}
					<button disabled={act.busy || open.reading.vision_state === 'queued' || open.reading.vision_state === 'reading'}
						onclick={() => doThen(() => api.shelfVision(open!.id))}>Read with the vision model</button>
				{/if}
				<button class="danger" disabled={act.busy} onclick={() => confirm('Delete this cover from the shelf?') && doThen(() => api.shelfDelete(open!.id), true)}>Delete</button>
			</div>
			{#if live.drives.length}
				<div class="row small">
					<label>
						<span class="sr-only">Drive</span>
						<select bind:value={drive}>
							{#each live.drives as d (d.id)}<option value={d.id}>{d.display_name || d.id}</option>{/each}
						</select>
					</label>
					<button disabled={act.busy || !drive} onclick={() => doThen(() => api.shelfForDrive(open!.id, drive), true)}>Use for this drive's next disc</button>
				</div>
			{/if}
			{#if act.error}<p class="small error-text">{act.error}</p>{/if}
		</div>
	{/if}
</dialog>

<style>
	h1 {
		margin: 0;
	}
	.count {
		align-self: center;
	}
	.intro {
		margin: -4px 0 0;
	}
	.add p {
		margin: 0;
	}
	.upload {
		gap: 8px;
		border-top: 1px solid var(--border);
		padding-top: 10px;
	}
	label.button {
		position: relative;
		cursor: pointer;
	}
	label.button.disabled {
		opacity: 0.5;
		pointer-events: none;
	}
	label.button input[type='file'] {
		position: absolute;
		inset: 0;
		opacity: 0;
		width: 100%;
		cursor: pointer;
	}
	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
	}
	.covers {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 170px), 1fr));
		gap: 10px;
	}
	.cover {
		width: 100%;
		height: 100%;
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 3px;
		padding: 8px;
		text-align: left;
		background: var(--surface);
	}
	.cover img {
		width: 100%;
		aspect-ratio: 16 / 9;
		object-fit: contain;
		background: var(--surface-2);
		border-radius: 6px;
	}
	.title {
		font-weight: 600;
		overflow-wrap: anywhere;
	}
	.mono-ish {
		overflow-wrap: anywhere;
	}
	dialog {
		padding: 12px;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
		color: var(--text);
		width: min(96vw, 1000px);
		max-width: 96vw;
	}
	dialog::backdrop {
		background: rgb(0 0 0 / 0.7);
	}
	.big {
		gap: 8px;
	}
	.big img {
		display: block;
		max-width: 100%;
		max-height: 65vh;
		margin: auto;
		border-radius: 6px;
	}
	.big p {
		margin: 0;
	}
	.big select {
		width: auto;
	}
</style>
