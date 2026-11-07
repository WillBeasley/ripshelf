<script lang="ts">
	import SetupProblems from '#lib/components/SetupProblems.svelte';
	import { SettingsForm } from '#lib/settings/form.svelte.ts';
	import { SECTIONS } from '#lib/settings/sections.ts';

	const form = new SettingsForm();
	$effect(() => {
		form.load();
	});
</script>

<svelte:head><title>Settings · ripshelf</title></svelte:head>

<div class="stack page">
	<h1>Settings</h1>
	<SetupProblems />

	{#if form.ready}
		{#each SECTIONS as s (s.id)}
			<div class="stack" id={s.id}>
				<h2 class="section-title">{s.title}</h2>
				<s.component {form} />
			</div>
		{/each}

		<div class="row save">
			<button class="primary" type="button" onclick={() => form.save()} disabled={form.act.busy}>Save settings</button>
			{#if form.saved}<span class="small muted">Saved</span>{/if}
			{#if form.act.error}<span class="small error-text">{form.act.error}</span>{/if}
		</div>
	{:else if form.act.error}
		<p class="error-text">{form.act.error}</p>
	{/if}
</div>

<style>
	.page {
		max-width: 680px;
	}
	.section-title {
		font-size: 1.3rem;
		margin: 16px 0 0;
	}
	.save {
		position: sticky;
		bottom: calc(64px + env(safe-area-inset-bottom));
		background: var(--bg);
		padding: 8px 0;
	}
	@media (min-width: 760px) {
		.save {
			bottom: 0;
		}
	}
</style>
