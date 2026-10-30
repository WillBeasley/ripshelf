<script lang="ts">
	let { score, threshold }: { score: number; threshold: number } = $props();
	const pass = $derived(score >= threshold);
</script>

<div class="meter" title="Auto-confirm at {threshold.toFixed(2)}">
	<div class="track">
		<div class="fill" class:pass style:width="{Math.max(2, score * 100)}%"></div>
		<div class="line" style:left="{threshold * 100}%"></div>
	</div>
	<div class="spread small">
		<span><strong>{score.toFixed(2)}</strong> <span class="muted">score</span></span>
		<span class="muted">auto-confirm {threshold.toFixed(2)}</span>
	</div>
</div>

<style>
	.meter {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.track {
		position: relative;
		height: 10px;
		border-radius: 999px;
		background: var(--track);
	}
	.fill {
		height: 100%;
		border-radius: 999px;
		background: var(--warn);
	}
	.fill.pass {
		background: var(--ok);
	}
	.line {
		position: absolute;
		top: -4px;
		bottom: -4px;
		width: 2px;
		background: var(--text);
		transform: translateX(-1px);
	}
</style>
