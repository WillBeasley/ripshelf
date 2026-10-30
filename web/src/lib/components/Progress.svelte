<script lang="ts">
	import type { RipProgress } from '../api-types';
	import { eta, gb, gbPerMin, percent } from '../format';

	let { progress, compact = false }: { progress: RipProgress; compact?: boolean } = $props();
</script>

<div class="progress">
	<div class="spread small">
		<span>{progress.phase}</span>
		<strong>{percent(progress.fraction)}</strong>
	</div>
	<div
		class="track"
		role="progressbar"
		aria-valuemin="0"
		aria-valuemax="100"
		aria-valuenow={Math.floor(progress.fraction * 100)}
	>
		<div class="bar" style:width="{Math.min(100, progress.fraction * 100)}%"></div>
	</div>
	{#if !compact}
		<div class="facts small muted">
			<span>{gb(progress.bytes_done)} / {gb(progress.bytes_total)}</span>
			<span>{gbPerMin(progress.bytes_per_s)}</span>
			<span>ETA {eta(progress.eta_s)}</span>
		</div>
	{/if}
</div>

<style>
	.progress {
		display: flex;
		flex-direction: column;
		gap: 5px;
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
		transition: width 0.6s ease;
	}
	.facts {
		display: flex;
		justify-content: space-between;
		gap: 8px;
		font-variant-numeric: tabular-nums;
	}
</style>
