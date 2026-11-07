<script lang="ts">
	// Scans and photos of the disc's box: from the network scanner (Settings → Scanner),
	// an uploaded picture, or a phone's camera. Front and back scans become TheDiscDB's
	// covers, and a whole outer sheet can be cut into both.
	import type { CoverScan, Job, ScannerInfo, ScanSplit } from '../api-types';
	import { api, type ScanKind } from '../api';
	import { Action } from '../actions.svelte';
	import ScanListing from './ScanListing.svelte';

	let { job, open = false }: { job: Job; open?: boolean } = $props();
	const act = new Action();

	const KINDS: [ScanKind, string][] = [
		['front', 'Front cover'],
		['back', 'Back cover'],
		['outside_full', 'Whole outside'],
		['inside_full', 'Inside of the cover'],
		['insert', 'Insert page'],
		['other', 'Other']
	];
	const kindName = Object.fromEntries(KINDS) as Record<ScanKind, string>;
	const SOURCE: Record<CoverScan['source'], string> = { scanner: 'scanned', upload: 'uploaded', camera: 'photo' };

	let shown = $state(false);
	let scanner = $state<ScannerInfo | null>(null);
	let checking = $state(false);
	let duplex = $state(false);
	let uploadKind = $state<ScanKind>('front');
	let splitting = $state<string | null>(null);
	let split = $state<ScanSplit>({ position: 0.5, spine: 0.05, swap: false });
	let note = $state<string | null>(null);
	let big = $state<CoverScan | null>(null);
	let dialog = $state<HTMLDialogElement | null>(null);
	// Phones and tablets get a button that opens the camera straight away.
	const touch = typeof window !== 'undefined' && window.matchMedia?.('(pointer: coarse)').matches;

	const scans = $derived(job.scans ?? []);
	const run = $derived(job.scanner);
	const scanning = $derived(run?.state === 'scanning');
	const canScan = $derived(!!scanner?.ok && !scanning && !(scanner.busy_job && scanner.busy_job !== job.id));
	const feeder = $derived(scanner?.sources.includes('feeder') ?? false);

	$effect(() => {
		if (open || (job.scans ?? []).length) shown = true;
	});

	$effect(() => {
		if (shown && !scanner && !checking) checkScanner();
	});

	async function checkScanner() {
		checking = true;
		try {
			scanner = await api.scanner();
		} catch (e) {
			scanner = null;
			note = e instanceof Error ? e.message : String(e);
		} finally {
			checking = false;
		}
	}

	async function scan(kind: ScanKind, source: 'platen' | 'feeder' = 'platen') {
		note = null;
		await act.run(() => api.scanFromScanner(job.id, { kind, source, duplex: source === 'feeder' && duplex }));
	}

	async function upload(e: Event, source: 'upload' | 'camera') {
		const input = e.currentTarget as HTMLInputElement;
		const files = [...(input.files ?? [])];
		input.value = '';
		note = null;
		for (const f of files) {
			if (!(await act.run(() => api.uploadScan(job.id, f, uploadKind, source)))) break;
		}
	}

	async function covers(sc: CoverScan, body?: ScanSplit) {
		note = null;
		const v = await act.run(() => api.scanCovers(job.id, sc.id, body));
		if (v) {
			note = sc.kind === 'outside_full' ? 'Both covers set from this sheet' : `The ${kindName[sc.kind].toLowerCase()} is set`;
			splitting = null;
		}
	}

	function startSplit(sc: CoverScan) {
		split = { ...(sc.split ?? { position: 0.5, spine: 0.05, swap: false }) };
		splitting = sc.id;
	}

	const turn = (sc: CoverScan) => act.run(() => api.updateScan(job.id, sc.id, { rotate: ((sc.rotate + 90) % 360) as CoverScan['rotate'] }));

	function zoom(sc: CoverScan) {
		big = sc;
		dialog?.showModal();
	}

	const left = $derived(`${(split.position - split.spine / 2) * 100}%`);
	const right = $derived(`${(1 - split.position - split.spine / 2) * 100}%`);
</script>

<details bind:open={shown}>
	<summary class="small">Cover scans{scans.length ? ` (${scans.length})` : ''}</summary>
	<div class="stack body">
		<p class="small muted">
			Pictures of the box: the covers, the whole sheet unfolded, its inside (where the contents are often printed) and
			any insert. Front and back scans also become TheDiscDB's covers when the disc is being sent.
		</p>

		<div class="scanner small">
			{#if checking}
				<span class="muted">Asking the scanner…</span>
			{:else if scanner?.ok}
				<span>{scanner.model ?? 'Scanner'} · {scanner.state ?? 'ready'}</span>
				{#if scanner.busy_job && scanner.busy_job !== job.id}<span class="muted"> · scanning for job {scanner.busy_job}</span>{/if}
				<button class="link small" onclick={checkScanner}>Check again</button>
			{:else if scanner && !scanner.enabled}
				<span class="muted">No scanner set up. Add one in <a href="/settings#scanner">Settings → Scanner</a>, or add pictures below.</span>
			{:else if scanner}
				<span class="error-text">{scanner.message}</span>
				<button class="link small" onclick={checkScanner}>Try again</button>
			{/if}
		</div>

		{#if scanning && run}
			<div class="status small" role="status">
				<span class="spinner" aria-hidden="true"></span>
				Scanning the {kindName[run.kind].toLowerCase()}{run.source === 'feeder' ? ' from the feeder' : ''}…
				{#if run.pages}{run.pages} page{run.pages === 1 ? '' : 's'} so far{/if}
				<button class="link small" onclick={() => act.run(() => api.cancelScanner(job.id))}>Stop</button>
			</div>
		{:else if run?.state === 'failed'}
			<div class="status bad small" role="alert">
				{run.message}
				<button class="link small" onclick={() => act.run(() => api.cancelScanner(job.id))}>Dismiss</button>
			</div>
		{/if}

		{#if scanner?.ok}
			<div class="buttons">
				<button disabled={!canScan || act.busy} onclick={() => scan('front')}>Scan front</button>
				<button disabled={!canScan || act.busy} onclick={() => scan('back')}>Scan back</button>
				<button disabled={!canScan || act.busy} onclick={() => scan('outside_full')}>Scan whole outside</button>
				<button disabled={!canScan || act.busy} onclick={() => scan('inside_full')}>Scan inside</button>
				<button disabled={!canScan || act.busy} onclick={() => scan('insert', feeder ? 'feeder' : 'platen')}
					>Scan insert pages{feeder ? ' (feeder)' : ''}</button
				>
				{#if feeder && scanner.duplex}
					<label class="check small"><input type="checkbox" bind:checked={duplex} /> Both sides</label>
				{/if}
			</div>
			<p class="small muted">
				Lay it face down on the glass. An unfolded sheet fits sideways; turn the scan afterwards with ⟳.
			</p>
		{/if}

		<div class="buttons add">
			<label class="small">
				<span class="sr-only">What the picture is of</span>
				<select bind:value={uploadKind}>
					{#each KINDS as [k, label] (k)}<option value={k}>{label}</option>{/each}
				</select>
			</label>
			<label class="button" class:disabled={act.busy}>
				Upload image
				<input type="file" accept="image/jpeg,image/png,image/webp" multiple disabled={act.busy}
					onchange={(e) => upload(e, 'upload')} />
			</label>
			{#if touch}
				<label class="button" class:disabled={act.busy}>
					Take photo
					<input type="file" accept="image/*" capture="environment" disabled={act.busy}
						onchange={(e) => upload(e, 'camera')} />
				</label>
			{/if}
		</div>

		{#if note}<p class="small ok-text">{note}</p>{/if}
		{#if act.error}<p class="small error-text">{act.error}</p>{/if}

		{#if scans.length}
			<ul class="scans">
				{#each scans as sc (sc.id)}
					<li class="scan" class:wide={splitting === sc.id}>
						<button class="thumb" title="Show it larger" onclick={() => zoom(sc)}>
							<img src={api.scanThumbUrl(job.id, sc)} alt={kindName[sc.kind]} loading="lazy" />
						</button>
						<div class="what small">
							<strong>{kindName[sc.kind]}{sc.page && (sc.kind === 'insert' || sc.kind === 'other') ? ` ${sc.page}` : ''}</strong>
							<span class="muted">{SOURCE[sc.source]}{sc.library_path ? ' · in the library' : ''}</span>
						</div>
						<div class="tools">
							<select aria-label="What it is of" value={sc.kind} disabled={act.busy}
								onchange={(e) => act.run(() => api.updateScan(job.id, sc.id, { kind: (e.currentTarget as HTMLSelectElement).value as ScanKind }))}>
								{#each KINDS as [k, label] (k)}<option value={k}>{label}</option>{/each}
							</select>
							<button class="icon" title="Turn a quarter clockwise" aria-label="Turn" disabled={act.busy} onclick={() => turn(sc)}>⟳</button>
							{#if sc.source === 'scanner'}
								<button class="link small" disabled={act.busy}
									onclick={() => act.run(() => api.updateScan(job.id, sc.id, { trim: !sc.crop }))}>{sc.crop ? 'Untrim' : 'Trim'}</button>
							{/if}
							<button class="link small danger" disabled={act.busy}
								onclick={() => act.run(() => api.deleteScan(job.id, sc.id))}>Remove</button>
						</div>
						{#if sc.kind === 'front' || sc.kind === 'back'}
							<button class="link small" disabled={act.busy} onclick={() => covers(sc)}>Use as the {sc.kind} cover</button>
						{:else if sc.kind === 'outside_full'}
							{#if splitting === sc.id}
								<div class="split">
									<div class="sheet">
										<img src={api.scanThumbUrl(job.id, sc)} alt="The sheet, with where it is cut" />
										<div class="half l" style:width={left}><span>{split.swap ? 'Front' : 'Back'}</span></div>
										<div class="half r" style:width={right}><span>{split.swap ? 'Back' : 'Front'}</span></div>
									</div>
									<label class="small">Spine at {Math.round(split.position * 100)}%
										<input type="range" min="0.2" max="0.8" step="0.005" bind:value={split.position} /></label>
									<label class="small">Spine width {Math.round(split.spine * 100)}%
										<input type="range" min="0" max="0.2" step="0.005" bind:value={split.spine} /></label>
									<label class="check small"><input type="checkbox" bind:checked={split.swap} /> The front is on the left</label>
									<div class="row">
										<button class="primary" disabled={act.busy} onclick={() => covers(sc, split)}>Use as both covers</button>
										<button class="link small" onclick={() => (splitting = null)}>Cancel</button>
									</div>
								</div>
							{:else}
								<button class="link small" disabled={act.busy} onclick={() => startSplit(sc)}>Cut into front and back covers…</button>
							{/if}
						{/if}
					</li>
				{/each}
			</ul>
		{/if}

		<ScanListing {job} />
	</div>
</details>

<dialog bind:this={dialog} onclick={() => dialog?.close()} onclose={() => (big = null)}>
	{#if big}
		<img src={api.scanThumbUrl(job.id, big, true)} alt={kindName[big.kind]} />
		<p class="small muted">
			{kindName[big.kind]} · {big.width} × {big.height} px, {SOURCE[big.source]} ·
			<a href={api.scanUrl(job.id, big.id)} target="_blank" rel="noreferrer" onclick={(e) => e.stopPropagation()}>the original</a>
		</p>
	{/if}
</dialog>

<style>
	.body {
		gap: 10px;
		margin-top: 8px;
	}
	p {
		margin: 0;
	}
	.buttons {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		align-items: center;
	}
	.check {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}
	.status {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
		padding: 8px 10px;
		border-radius: 8px;
		background: var(--info-bg);
		color: var(--info);
	}
	.status.bad {
		background: var(--bad-bg);
		color: var(--bad);
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
	.ok-text {
		color: var(--ok);
	}
	.scans {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 10px;
	}
	.scan {
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 0;
	}
	.thumb {
		padding: 0;
		border: 1px solid var(--border);
		border-radius: 6px;
		background: var(--surface-2);
		aspect-ratio: 4 / 3;
		overflow: hidden;
		cursor: zoom-in;
	}
	.thumb img {
		width: 100%;
		height: 100%;
		object-fit: contain;
		display: block;
	}
	.what {
		display: flex;
		flex-direction: column;
	}
	.tools {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px;
	}
	.tools select {
		flex: 1 1 100%;
		min-width: 0;
	}
	.icon {
		padding: 2px 8px;
	}
	.scan.wide {
		grid-column: 1 / -1;
	}
	.split {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 8px;
		border-radius: 8px;
		background: var(--surface-2);
	}
	.split input[type='range'] {
		width: 100%;
	}
	.sheet {
		position: relative;
		line-height: 0;
	}
	.sheet img {
		width: 100%;
		height: auto;
		border-radius: 4px;
	}
	.half {
		position: absolute;
		top: 0;
		bottom: 0;
		border: 2px solid var(--accent);
		background: color-mix(in srgb, var(--accent) 12%, transparent);
		display: flex;
		align-items: flex-end;
		justify-content: center;
		line-height: normal;
	}
	.half.l {
		left: 0;
	}
	.half.r {
		right: 0;
	}
	.half span {
		margin-bottom: 4px;
		padding: 0 6px;
		border-radius: 4px;
		background: rgb(0 0 0 / 0.65);
		color: #fff;
		font-size: 0.75rem;
	}
	dialog {
		padding: 8px;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
		color: var(--text);
		max-width: min(96vw, 1000px);
	}
	dialog::backdrop {
		background: rgb(0 0 0 / 0.7);
	}
	dialog img {
		display: block;
		max-width: 100%;
		max-height: 80vh;
		margin: auto;
		border-radius: 6px;
	}
	dialog p {
		margin: 6px 0 0;
	}
</style>
