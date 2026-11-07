<script lang="ts">
	// Settings: a sidebar of sections with search (on a phone, the section list on /settings),
	// and the section's page. One form for every section, so typing in one and moving to
	// another keeps what's typed (the sidebar marks it).
	import { page } from '$app/state';
	import { live } from '#lib/live.svelte.ts';
	import { SettingsForm } from '#lib/settings/form.svelte.ts';
	import { setForm } from '#lib/settings/context.ts';
	import { SECTIONS, type SectionInfo } from '#lib/settings/sections.ts';
	import SettingsSearch from '#lib/settings/SettingsSearch.svelte';

	let { children } = $props();

	const form = new SettingsForm();
	setForm(form);
	$effect(() => {
		form.load();
	});

	const current = $derived(page.params.section);
	const problemCodes = $derived(new Set((live.status?.problems ?? []).map((p) => p.code)));
	const hasProblem = (s: SectionInfo) => (s.problems ?? []).some((c) => problemCodes.has(c));
	const unsaved = (s: SectionInfo) => form.ready && form.changed(form.names(s.id)).length > 0;
</script>

<div class="settings" class:at-index={!current}>
	<aside class="side" aria-label="Settings sections">
		<h1>Settings</h1>
		{#if form.ready}<SettingsSearch {form} />{/if}
		<nav>
			{#each SECTIONS as s (s.id)}
				<a href="/settings/{s.id}" class:active={current === s.id} aria-current={current === s.id ? 'page' : undefined}>
					<span>{s.title}</span>
					{#if hasProblem(s)}<span class="problem" title="Needs setting up">!</span>{/if}
					{#if unsaved(s)}<span class="unsaved" title="Not saved yet">●</span>{/if}
				</a>
			{/each}
		</nav>
	</aside>
	<div class="content">
		{#if form.ready}
			{@render children()}
		{:else if form.act.error}
			<p class="error-text">{form.act.error}</p>
		{:else}
			<p class="muted">Loading…</p>
		{/if}
	</div>
</div>

<style>
	.settings {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 24px;
		max-width: 980px;
	}
	.side {
		display: none;
	}
	.content {
		min-width: 0;
		max-width: 680px;
	}
	h1 {
		margin: 0;
	}
	nav {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	nav a {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 8px 10px;
		border-radius: 8px;
		color: var(--text);
		text-decoration: none;
	}
	nav a span:first-child {
		flex: 1;
	}
	nav a:hover {
		background: var(--surface);
	}
	nav a.active {
		background: var(--surface-2);
		font-weight: 600;
	}
	.unsaved {
		color: var(--warn);
		font-size: 0.7rem;
	}
	.problem {
		display: inline-grid;
		place-items: center;
		width: 18px;
		height: 18px;
		border-radius: 50%;
		background: var(--warn);
		color: var(--bg);
		font-size: 0.75rem;
		font-weight: 700;
	}
	@media (min-width: 760px) {
		.settings {
			grid-template-columns: 220px minmax(0, 1fr);
		}
		.side {
			display: flex;
			flex-direction: column;
			gap: 12px;
			position: sticky;
			top: 16px;
			align-self: start;
		}
	}
</style>
