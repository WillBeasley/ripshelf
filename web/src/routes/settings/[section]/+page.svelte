<script lang="ts">
	// /settings/<section>: one section of Settings. A #setting-<name> in the address (from
	// settings search, or a link elsewhere) shows that setting.
	import { afterNavigate } from '$app/navigation';
	import { page } from '$app/state';
	import { tick } from 'svelte';
	import SetupProblems from '#lib/components/SetupProblems.svelte';
	import { getForm } from '#lib/settings/context.ts';
	import { reveal } from '#lib/settings/reveal.ts';
	import { sectionById } from '#lib/settings/sections.ts';

	const form = getForm();
	const section = $derived(sectionById(page.params.section ?? ''));

	afterNavigate(async () => {
		const m = location.hash.match(/^#setting-(.+)$/);
		await tick();
		if (m) reveal(decodeURIComponent(m[1]));
		else if (!location.hash) window.scrollTo({ top: 0 });
	});
</script>

<svelte:head><title>{section?.title ?? 'Settings'} · Settings · ripshelf</title></svelte:head>

{#if section}
	<div class="stack">
		<div class="head">
			<a class="back small" href="/settings">‹ Settings</a>
			<h2 class="title">{section.title}</h2>
		</div>
		{#if section.problems}<SetupProblems codes={section.problems} />{/if}
		<section.component {form} />
		<div class="row save">
			<button class="primary" type="button" onclick={() => form.save(form.names(section.id))} disabled={form.act.busy}>
				Save {section.title.toLowerCase()}
			</button>
			{#if form.saved}<span class="small muted">Saved</span>{/if}
			{#if form.act.error}<span class="small error-text">{form.act.error}</span>{/if}
		</div>
	</div>
{:else}
	<p>There's no section of Settings called “{page.params.section}”. <a href="/settings">All settings</a></p>
{/if}

<style>
	.head {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.title {
		font-size: 1.4rem;
		margin: 0;
	}
	.save {
		position: sticky;
		bottom: calc(64px + env(safe-area-inset-bottom));
		background: var(--bg);
		padding: 8px 0;
	}
	@media (min-width: 760px) {
		.back {
			display: none;
		}
		.save {
			bottom: 0;
		}
	}
</style>
