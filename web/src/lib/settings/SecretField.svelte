<script lang="ts">
	// A secret: never shown, only whether it's set. Typing replaces it, and Clear removes it.
	import type { SecretName, SettingsForm } from './form.svelte.ts';

	let { form, name, label }: { form: SettingsForm; name: SecretName; label: string } = $props();
	const isSet = $derived(form.isSet(name));
</script>

<label class="field">
	{label}
	<div class="row nowrap">
		<input
			type="password"
			autocomplete="off"
			placeholder={isSet ? '•••••••• (set; type to replace)' : 'not set'}
			bind:value={form.secrets[name]}
		/>
		{#if isSet}<button type="button" onclick={() => form.clearSecret(name)}>Clear</button>{/if}
	</div>
</label>

<style>
	.nowrap {
		flex-wrap: nowrap;
	}
</style>
