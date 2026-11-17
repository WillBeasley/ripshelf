<script lang="ts">
	import type { Job, ScanClock } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { duration, eta, scanTimeLeft } from '../format';

	// How long the scan under way has before it's stopped, and buttons to give it longer.
	let { job, clock }: { job: Job; clock: ScanClock } = $props();
	const act = new Action();

	let nowMs = $state(Date.now());
	$effect(() => {
		const t = setInterval(() => (nowMs = Date.now()), 1000);
		return () => clearInterval(t);
	});
	const time = $derived(scanTimeLeft(clock, nowMs));
	const EXTRA: [number, string][] = [
		[5, '+5 min'],
		[15, '+15 min'],
		[60, '+1 h']
	];
</script>

<div class="clock small">
	<p class="muted">MakeMKV is scanning the disc…</p>
	<p class="muted">
		{#if time.by === 'quiet'}
			<span title="Stopped once MakeMKV says nothing for this long. The clock restarts whenever it reports."
				>Stops in <span class="left">{duration(time.left)}</span> if it stays quiet</span
			>
		{:else}
			Stops in <span class="left">{duration(time.left)}</span>, at its {eta(clock.max_s + clock.extended_s)} limit
		{/if}
		{#if clock.extended_s}· given {eta(clock.extended_s)} more{/if}
	</p>
	<div class="row">
		<span class="muted">Give it longer:</span>
		{#each EXTRA as [minutes, label] (minutes)}
			<button class="link small" disabled={act.busy} onclick={() => act.run(() => api.extendScan(job.id, minutes))}
				>{label}</button
			>
		{/each}
	</div>
	{#if act.error}<p class="error-text">{act.error}</p>{/if}
</div>

<style>
	.clock {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.clock p {
		margin: 0;
	}
	.left {
		font-variant-numeric: tabular-nums;
		color: var(--text);
	}
	.row {
		gap: 10px;
		align-items: baseline;
	}
</style>
