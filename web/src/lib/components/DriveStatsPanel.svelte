<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '../api';
	import type { DriveStats } from '../api-types';
	import { live } from '../live.svelte';
	import { gbPerMin, kindLabel, size } from '../format';

	const KINDS = ['dvd', 'bluray', 'uhd', 'other'];

	let stats = $state<DriveStats[] | null>(null);
	let error = $state<string | null>(null);

	async function load() {
		try {
			stats = await api.driveStats();
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	onMount(load);

	// Reload when a job finishes.
	const finished = $derived(
		Object.values(live.jobs)
			.filter((j) => j.finished_at)
			.map((j) => `${j.id}:${j.state}`)
			.join(',')
	);
	let seen: string | null = null;
	$effect(() => {
		const key = finished;
		if (seen !== null && key !== seen) load();
		seen = key;
	});

	function hours(s: number): string {
		if (s < 3600) return `${Math.round(s / 60)} min`;
		return `${(s / 3600).toFixed(s < 36000 ? 1 : 0)} h`;
	}

	function since(iso: string | null): string {
		return iso ? new Date(iso).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' }) : '–';
	}

	const kinds = (s: DriveStats) => KINDS.filter((k) => s.by_kind[k]).map((k) => `${s.by_kind[k]} ${kindLabel(k)}`);
	const ripped = (s: DriveStats) =>
		[
			s.movies && `${s.movies} film${s.movies === 1 ? '' : 's'}`,
			s.tv && `${s.tv} TV disc${s.tv === 1 ? '' : 's'}`,
			s.backups && `${s.backups} backup${s.backups === 1 ? '' : 's'}`
		].filter(Boolean);
	const other = (s: DriveStats) =>
		[
			s.failed && `${s.failed} failed`,
			s.cancelled && `${s.cancelled} cancelled`,
			s.skipped && `${s.skipped} not ripped (already in library)`
		].filter(Boolean);
</script>

<section class="card stack">
	<h2>All-time statistics</h2>
	{#if error}<p class="small error-text">{error}</p>{/if}
	{#if stats && stats.length}
		<div class="drives">
			{#each stats as s (s.drive_id)}
				<div class="drive">
					<div class="spread small">
						<span>
							<strong>{s.name || s.model || s.drive_id}</strong>
							{#if s.name && s.model}<span class="muted">· {s.model}</span>{/if}
						</span>
						{#if !s.connected}<span class="chip">Not attached</span>{/if}
					</div>
					<div class="headline">
						<div><span class="big">{s.discs_ripped}</span> <span class="muted small">disc{s.discs_ripped === 1 ? '' : 's'}</span></div>
						<div><span class="big">{size(s.data_bytes)}</span> <span class="muted small">ripped</span></div>
					</div>
					{#if s.discs_ripped}
						<dl class="small">
							{#if kinds(s).length}<dt>Discs</dt><dd>{kinds(s).join(' · ')}</dd>{/if}
							{#if ripped(s).length}<dt>As</dt><dd>{ripped(s).join(' · ')}</dd>{/if}
							{#if s.rip_seconds}
								<dt>Ripping</dt>
								<dd>{hours(s.rip_seconds)} · avg {gbPerMin(s.timed_bytes / s.rip_seconds)}</dd>
							{/if}
							<dt>Since</dt><dd>{since(s.first_at)}</dd>
						</dl>
					{/if}
					{#if other(s).length}<p class="small muted">{other(s).join(' · ')}</p>{/if}
				</div>
			{/each}
		</div>
	{:else if stats}
		<p class="small muted">Nothing ripped yet.</p>
	{:else if !error}
		<p class="small muted">Loading…</p>
	{/if}
</section>

<style>
	.drives {
		display: grid;
		gap: 16px;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr));
	}
	.drive {
		display: flex;
		flex-direction: column;
		gap: 6px;
		min-width: 0;
	}
	.headline {
		display: flex;
		gap: 20px;
		flex-wrap: wrap;
		font-variant-numeric: tabular-nums;
	}
	.big {
		font-size: 1.4rem;
		font-weight: 600;
	}
	dl {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 2px 10px;
		margin: 0;
		font-variant-numeric: tabular-nums;
	}
	dt {
		color: var(--muted);
	}
	dd {
		margin: 0;
		overflow-wrap: anywhere;
	}
	p {
		margin: 0;
	}
</style>
