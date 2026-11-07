<script lang="ts">
	// Where to get a provider's key, or its website.
	import type { SettingsForm } from './form.svelte.ts';

	let { form, name }: { form: SettingsForm; name: string } = $props();
	const p = $derived(form.providerInfo[name]);
</script>

{#if p?.site}
	<a class="small" href={p.site} target="_blank" rel="noreferrer">{p.needs_key ? 'Get a key' : 'Website'} →</a>
{/if}
