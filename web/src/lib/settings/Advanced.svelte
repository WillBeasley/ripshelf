<script lang="ts">
	// The settings most people leave alone, folded away at the end of a card. Settings
	// search opens it to show a setting inside.
	import type { Snippet } from 'svelte';

	let { children, label = 'Advanced' }: { children: Snippet; label?: string } = $props();
</script>

<details class="advanced">
	<summary>{label}</summary>
	<div class="stack body">
		{@render children()}
	</div>
</details>

<style>
	.advanced {
		border-top: 1px solid var(--border);
		padding-top: 10px;
	}
	summary {
		cursor: pointer;
		color: var(--muted);
		font-size: 0.9rem;
		min-height: 32px;
		display: flex;
		align-items: center;
		gap: 6px;
	}
	summary::-webkit-details-marker {
		display: none;
	}
	summary::before {
		content: '▸';
		transition: transform 0.15s;
	}
	details[open] summary::before {
		transform: rotate(90deg);
	}
	.body {
		padding-top: 10px;
	}
</style>
