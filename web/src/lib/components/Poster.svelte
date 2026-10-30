<script lang="ts">
	let { url, alt, width = 72 }: { url: string | null; alt: string; width?: number } = $props();
	let failed = $state(false);
</script>

{#if url && !failed}
	<img src={url} {alt} style:width="{width}px" onerror={() => (failed = true)} loading="lazy" />
{:else}
	<div class="none" style:width="{width}px" aria-hidden="true">🎬</div>
{/if}

<style>
	img,
	.none {
		aspect-ratio: 2 / 3;
		border-radius: 6px;
		flex: none;
		object-fit: cover;
		background: var(--surface-2);
	}
	.none {
		display: grid;
		place-items: center;
		font-size: 1.4rem;
	}
</style>
