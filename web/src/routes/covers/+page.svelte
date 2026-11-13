<script lang="ts">
	// The covers shelf: case sheets scanned ahead of their discs, read in the background,
	// and used when their disc goes in (from the job, or for a drive's next disc here). Press
	// and hold a cover (or Ctrl-click it) to pick several, to delete them together.
	import type { ScannerInfo, ShelfCover, ShelfView, VisionModels } from '#lib/api-types.ts';
	import { api } from '#lib/api.ts';
	import { Action } from '#lib/actions.svelte.ts';
	import { live } from '#lib/live.svelte.ts';
	import { when } from '#lib/format.ts';
	import { coverFacts, coverTitle, READ_STATE, VISION_STATE } from '#lib/shelf.ts';
	import ShelfStack from '#lib/components/ShelfStack.svelte';
	import ShelfEdit from '#lib/components/ShelfEdit.svelte';
	import { untrack } from 'svelte';

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

	// The vision model to read the open cover with, this once ('' is Settings').
	let models = $state<VisionModels | null>(null);
	let model = $state('');
	let ownModel = $state(false); // typing one in
	const OTHER = '\u0000other';

	// Picking covers to delete together.
	const HOLD_MS = 500;
	let picked = $state<string[]>([]);
	let picking = $state(false);
	let lastPicked: string | null = null;
	let held: ReturnType<typeof setTimeout> | null = null;
	let heldAt: [number, number] | null = null;
	let justHeld = false; // the click after a long press doesn't open the cover

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
		if (scanner?.vision && !models) {
			api.visionModels().then((m) => (models = m)).catch(() => {});
		}
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

	// Turn the side shown a quarter (or half) as it shows, saved at once: mirrored, the
	// turn underneath goes the other way. The crop, kept on the original, stays.
	function quickTurn(by: 90 | -90 | 180) {
		const c = open;
		const s = c?.[side];
		if (!c || !s) return;
		const rotate = ((((s.rotate + (s.flip ? -by : by)) % 360) + 360) % 360) as typeof s.rotate;
		doThen(() => api.shelfEdit(c.id, side, { rotate }));
	}

	function pickModel(e: Event) {
		const v = (e.currentTarget as HTMLSelectElement).value;
		ownModel = v === OTHER;
		model = ownModel ? '' : v;
	}

	// --- picking several -------------------------------------------------------------------

	function toggle(id: string) {
		picked = picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id];
		lastPicked = id;
	}

	// Shift-click: everything between the last one picked and this one, as listed.
	function pickRange(id: string) {
		const ids = (view?.covers ?? []).map((c) => c.id);
		const a = lastPicked ? ids.indexOf(lastPicked) : -1;
		const b = ids.indexOf(id);
		if (a < 0 || b < 0) return toggle(id);
		const between = ids.slice(Math.min(a, b), Math.max(a, b) + 1);
		picked = [...new Set([...picked, ...between])];
		lastPicked = id;
	}

	function stopPicking() {
		picking = false;
		picked = [];
		lastPicked = null;
	}

	function press(e: PointerEvent, c: ShelfCover) {
		if (e.button !== 0) return;
		release();
		heldAt = [e.clientX, e.clientY];
		held = setTimeout(() => {
			held = null;
			justHeld = true;
			picking = true;
			if (!picked.includes(c.id)) toggle(c.id);
			navigator.vibrate?.(20);
		}, HOLD_MS);
	}

	function release() {
		if (held) clearTimeout(held);
		held = null;
	}

	// Scrolling the page isn't holding a cover.
	function moved(e: PointerEvent) {
		if (held && heldAt && Math.hypot(e.clientX - heldAt[0], e.clientY - heldAt[1]) > 10) release();
	}

	function clicked(e: MouseEvent, c: ShelfCover) {
		if (justHeld) {
			justHeld = false;
			return;
		}
		if (e.shiftKey && (picking || picked.length)) {
			picking = true;
			pickRange(c.id);
		} else if (e.ctrlKey || e.metaKey) {
			picking = true;
			toggle(c.id);
		} else if (picking) {
			toggle(c.id);
		} else {
			show(c);
		}
	}

	async function deletePicked() {
		const n = picked.length;
		if (!n || !confirm(`Delete ${n} cover${n === 1 ? '' : 's'} from the shelf? Their pictures and readings go too.`)) return;
		const out = await act.run(() => api.shelfDeleteMany(picked));
		if (out) stopPicking();
		await load();
	}

	// Covers no longer listed (deleted, used, or searched away) aren't picked.
	$effect(() => {
		const ids = new Set((view?.covers ?? []).map((c) => c.id));
		const was = untrack(() => picked);
		const kept = was.filter((id) => ids.has(id));
		if (kept.length !== was.length) picked = kept;
	});
</script>

<svelte:head><title>Covers · ripshelf</title></svelte:head>
<svelte:window onkeydown={(e) => e.key === 'Escape' && picking && !dialog?.open && stopPicking()} />

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

	{#if picking}
		<div class="picking card row" role="toolbar" aria-label="Covers picked">
			<strong>{picked.length} selected</strong>
			<button class="link small" onclick={() => ((picked = (view?.covers ?? []).map((c) => c.id)), (lastPicked = null))}>Select all</button>
			<span class="grow"></span>
			<button class="danger" disabled={act.busy || !picked.length} onclick={deletePicked}>Delete selected</button>
			<button disabled={act.busy} onclick={stopPicking}>Cancel</button>
		</div>
	{/if}

	<ul class="covers">
		{#each view?.covers ?? [] as c (c.id)}
			<li>
				<button class="cover" class:picked={picked.includes(c.id)} aria-pressed={picking ? picked.includes(c.id) : undefined}
					onclick={(e) => clicked(e, c)} onpointerdown={(e) => press(e, c)} onpointerup={release}
					onpointercancel={release} onpointerleave={release} onpointermove={moved}
					oncontextmenu={(e) => (held || picking) && e.preventDefault()}
					title={picking ? 'Tap to pick it or leave it out' : 'Press and hold (or Ctrl-click) to pick several'}>
					{#if picking}<span class="tick" aria-hidden="true">{picked.includes(c.id) ? '✓' : ''}</span>{/if}
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
				<div class="row turns">
					<button class="icon" disabled={act.busy} title="Turn a quarter anticlockwise (saved straight away)" aria-label="Turn left"
						onclick={() => quickTurn(-90)}>⟲</button>
					<button class="icon" disabled={act.busy} title="Turn upside down (saved straight away)" aria-label="Turn upside down"
						onclick={() => quickTurn(180)}>180°</button>
					<button class="icon" disabled={act.busy} title="Turn a quarter clockwise (saved straight away)" aria-label="Turn right"
						onclick={() => quickTurn(90)}>⟳</button>
				</div>
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
					<span class="row vision">
						<button disabled={act.busy || open.reading.vision_state === 'queued' || open.reading.vision_state === 'reading' || (ownModel && !model.trim())}
							onclick={() => doThen(() => api.shelfVision(open!.id, model.trim() || undefined))}>Read with the vision model</button>
						<label class="small">
							<span class="sr-only">Vision model</span>
							<select value={ownModel ? OTHER : model} onchange={pickModel} title="The model to read it with, this once (Settings aren't changed)">
								<option value="">{models?.current ? `${models.current} (Settings)` : 'As Settings say'}</option>
								{#each (models?.models ?? []).filter((m) => m !== models?.current) as m (m)}<option value={m}>{m}</option>{/each}
								<option value={OTHER}>Another…</option>
							</select>
						</label>
						{#if ownModel}
							<input class="small own" placeholder="Model name" aria-label="Model name" bind:value={model} />
						{/if}
						{#if models?.message}<span class="small muted" title={models.message}>Couldn't list the models</span>{/if}
					</span>
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
	.cover {
		position: relative;
		user-select: none;
		-webkit-user-select: none;
		-webkit-touch-callout: none;
	}
	.cover.picked {
		outline: 3px solid var(--accent);
		outline-offset: -3px;
	}
	.tick {
		position: absolute;
		top: 12px;
		left: 12px;
		width: 24px;
		height: 24px;
		border-radius: 50%;
		display: grid;
		place-items: center;
		font-weight: 700;
		background: var(--surface);
		border: 2px solid var(--border);
	}
	.picked .tick {
		background: var(--accent);
		border-color: var(--accent);
		color: var(--accent-text);
	}
	.picking {
		position: sticky;
		top: 0;
		z-index: 5;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
		padding: 8px 12px;
	}
	.grow {
		flex: 1;
	}
	.turns {
		justify-content: center;
		gap: 6px;
	}
	.icon {
		padding: 2px 10px;
	}
	.vision {
		gap: 6px;
		align-items: center;
		flex-wrap: wrap;
	}
	.big .own {
		width: 14em;
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
