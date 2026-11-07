<script lang="ts">
	// Pictures of a box added before its disc goes in. The drive keeps them for a day;
	// the next disc put in it takes them, and they're read as it's identified.
	import type { PendingScans, ScannerInfo } from '../api-types';
	import { api, type ScanKind } from '../api';
	import { Action } from '../actions.svelte';
	import { when } from '../format';

	let { driveId, onchange }: { driveId: string; onchange?: (n: number) => void } = $props();
	const act = new Action();

	const KINDS: [ScanKind, string][] = [
		['back', 'Back cover'],
		['front', 'Front cover'],
		['outside_full', 'Whole outside'],
		['inside_full', 'Inside of the cover'],
		['insert', 'Insert page'],
		['other', 'Other']
	];
	const kindName = Object.fromEntries(KINDS) as Record<ScanKind, string>;

	let data = $state<PendingScans | null>(null);
	let scanner = $state<ScannerInfo | null>(null);
	let kind = $state<ScanKind>('back');
	const touch = typeof window !== 'undefined' && window.matchMedia?.('(pointer: coarse)').matches;
	const scanning = $derived(data?.scanner?.state === 'scanning');

	async function load() {
		try {
			data = await api.pendingScans(driveId);
			onchange?.(data.scans.length);
		} catch {
			/* keep what's shown */
		}
	}

	$effect(() => {
		driveId;
		load();
		api.scanner().then((s) => (scanner = s)).catch(() => {});
	});

	// While the scanner works the list changes on the server; look again until it's done.
	$effect(() => {
		if (!scanning) return;
		const t = setInterval(load, 2000);
		return () => clearInterval(t);
	});

	async function upload(e: Event, source: 'upload' | 'camera') {
		const input = e.currentTarget as HTMLInputElement;
		const files = [...(input.files ?? [])];
		input.value = '';
		for (const f of files) if (!(await act.run(() => api.addPendingScan(driveId, f, kind, source)))) break;
		await load();
	}

	async function scan(k: ScanKind, source: 'platen' | 'feeder' = 'platen') {
		if (await act.run(() => api.scanPending(driveId, { kind: k, source, duplex: false }))) await load();
	}
</script>

<div class="pending stack">
	<p class="small muted">
		For the disc you'll put in this drive next: its pictures are read while MakeMKV scans it, and help identify it.
		They're kept for a day.
	</p>
	{#if scanner?.ok}
		<div class="buttons">
			<button disabled={act.busy || scanning} onclick={() => scan('back')}>Scan back</button>
			<button disabled={act.busy || scanning} onclick={() => scan('outside_full')}>Scan whole outside</button>
			<button disabled={act.busy || scanning} onclick={() => scan('inside_full')}>Scan inside</button>
		</div>
	{/if}
	<div class="buttons">
		<label class="small">
			<span class="sr-only">What the picture is of</span>
			<select bind:value={kind}>{#each KINDS as [k, label] (k)}<option value={k}>{label}</option>{/each}</select>
		</label>
		<label class="button" class:disabled={act.busy}>
			Upload image
			<input type="file" accept="image/jpeg,image/png,image/webp" multiple disabled={act.busy} onchange={(e) => upload(e, 'upload')} />
		</label>
		{#if touch}
			<label class="button" class:disabled={act.busy}>
				Take photo
				<input type="file" accept="image/*" capture="environment" disabled={act.busy} onchange={(e) => upload(e, 'camera')} />
			</label>
		{/if}
	</div>
	{#if data?.scanner}
		<div class="small status" class:bad={data.scanner.state === 'failed'} role="status">
			{#if scanning}<span class="spinner" aria-hidden="true"></span>Scanning the {kindName[data.scanner.kind].toLowerCase()}…
			{:else}{data.scanner.message}{/if}
			<button class="link small" onclick={async () => { await act.run(() => api.clearPendingScanner(driveId)); await load(); }}
				>{scanning ? 'Stop' : 'Dismiss'}</button>
		</div>
	{/if}
	{#if data?.scans.length}
		<ul class="thumbs">
			{#each data.scans as sc (sc.id)}
				<li>
					<img src={api.pendingScanUrl(sc.id, true)} alt={kindName[sc.kind]} loading="lazy" />
					<div class="small"><strong>{kindName[sc.kind]}</strong></div>
					<div class="small muted">kept until {when(sc.expires_at)}</div>
					<button class="link small danger" disabled={act.busy}
						onclick={async () => { await act.run(() => api.deletePendingScan(driveId, sc.id)); await load(); }}>Remove</button>
				</li>
			{/each}
		</ul>
	{/if}
	{#if act.error}<p class="small error-text">{act.error}</p>{/if}
</div>

<style>
	.pending {
		gap: 8px;
		padding: 8px;
		border-radius: 8px;
		background: var(--surface-2);
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
	.status {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
		color: var(--info);
	}
	.status.bad {
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
	.thumbs {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
		gap: 8px;
	}
	.thumbs img {
		width: 100%;
		aspect-ratio: 4 / 3;
		object-fit: contain;
		background: var(--bg);
		border-radius: 6px;
		border: 1px solid var(--border);
	}
	.danger {
		color: var(--bad);
	}
</style>
