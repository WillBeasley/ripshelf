<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '../api';
	import type { Filesystem } from '../api-types';
	import { size } from '../format';

	const REFRESH_MS = 60_000;

	let disks = $state<Filesystem[] | null>(null);
	let error = $state<string | null>(null);

	async function load() {
		try {
			disks = await api.storage();
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	onMount(() => {
		load();
		const t = setInterval(load, REFRESH_MS);
		return () => clearInterval(t);
	});

	const usedFraction = (d: Filesystem) => (d.total ? (d.used ?? 0) / d.total : 0);
</script>

<section class="card stack">
	<h2>Storage</h2>
	{#if error}<p class="small error-text">{error}</p>{/if}
	{#if disks}
		<div class="disks">
			{#each disks as d, i (i)}
				<div class="disk">
					{#if d.total != null}
						<div class="spread small">
							<span><strong>{size(d.free)}</strong> free of {size(d.total)}</span>
							<span class="muted">{Math.round(usedFraction(d) * 100)}% used</span>
						</div>
						<div
							class="track"
							role="meter"
							aria-label="Disk used"
							aria-valuemin="0"
							aria-valuemax="100"
							aria-valuenow={Math.round(usedFraction(d) * 100)}
						>
							<div class="bar" class:warn={d.warning} style:width="{Math.min(100, usedFraction(d) * 100)}%"></div>
						</div>
					{/if}
					{#if d.warning}<p class="small warn-text">{d.warning}</p>{/if}
					<ul class="small">
						{#each d.folders as f (f.path)}
							<li>
								{f.name} <code class="muted">{f.path}</code>
								{#if !f.exists && d.total != null}<span class="muted">(not made yet)</span>{/if}
							</li>
						{/each}
					</ul>
				</div>
			{/each}
		</div>
	{:else if !error}
		<p class="small muted">Loading…</p>
	{/if}
</section>

<style>
	.disks {
		display: grid;
		gap: 16px;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr));
	}
	.disk {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.track {
		height: 8px;
		border-radius: 999px;
		background: var(--track);
		overflow: hidden;
	}
	.bar {
		height: 100%;
		background: var(--accent);
		border-radius: 999px;
	}
	.bar.warn {
		background: var(--warn);
	}
	.warn-text {
		color: var(--warn);
		margin: 0;
	}
	ul {
		margin: 0;
		padding-left: 18px;
		font-variant-numeric: tabular-nums;
	}
	code {
		overflow-wrap: anywhere;
	}
</style>
