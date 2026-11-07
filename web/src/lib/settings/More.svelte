<script lang="ts">
	// A longer explanation behind "More", after a setting's one-line help.
	import type { Snippet } from 'svelte';

	let { children }: { children: Snippet } = $props();
	let open = $state(false);
</script>

{#if open}{' '}{@render children()}{/if}
<button type="button" class="link more" aria-expanded={open} onclick={() => (open = !open)}>
	{open ? 'Less' : 'More'}
</button>

<style>
	.more {
		font-size: inherit;
	}
</style>
