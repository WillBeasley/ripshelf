<script lang="ts">
	// A secret: never shown, only whether it's set. Typing replaces it, and Clear removes it
	// when the section is saved.
	import type { SecretName, SettingsForm } from './form.svelte.ts';

	let { form, name, label }: { form: SettingsForm; name: SecretName; label: string } = $props();
	const isSet = $derived(form.isSet(name));
	const clearing = $derived(form.cleared.includes(name));
</script>

<label class="field">
	<span>{label} {#if form.dirty(name)}<span class="unsaved" title="Not saved yet">●</span>{/if}</span>
	<div class="row nowrap">
		<input
			type="password"
			autocomplete="off"
			placeholder={clearing ? 'cleared when you save' : isSet ? '•••••••• (set; type to replace)' : 'not set'}
			bind:value={form.secrets[name]}
			disabled={clearing}
		/>
		{#if clearing}
			<button type="button" onclick={() => form.keepSecret(name)}>Keep</button>
		{:else if isSet}
			<button type="button" onclick={() => form.clearSecret(name)}>Clear</button>
		{/if}
	</div>
</label>

<style>
	.nowrap {
		flex-wrap: nowrap;
	}
	.unsaved {
		color: var(--warn);
		font-size: 0.7rem;
		vertical-align: middle;
	}
</style>
