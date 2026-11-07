<script lang="ts">
	// /settings: on a phone, the list of sections with what each is set to; wider, the first
	// section (the sidebar is the list).
	import { goto } from '$app/navigation';
	import { live } from '#lib/live.svelte.ts';
	import SetupProblems from '#lib/components/SetupProblems.svelte';
	import { getForm } from '#lib/settings/context.ts';
	import { SECTIONS } from '#lib/settings/sections.ts';
	import SettingsSearch from '#lib/settings/SettingsSearch.svelte';

	const form = getForm();
	const wide = typeof window !== 'undefined' && window.matchMedia('(min-width: 760px)').matches;
	if (wide) goto(`/settings/${SECTIONS[0].id}${location.hash}`, { replaceState: true });

	const problemCodes = $derived(new Set((live.status?.problems ?? []).map((p) => p.code)));
</script>

<svelte:head><title>Settings · ripshelf</title></svelte:head>

{#if !wide}
	<div class="stack">
		<h1>Settings</h1>
		<SettingsSearch {form} />
		<SetupProblems link={false} />
		<ul class="list">
			{#each SECTIONS as s (s.id)}
				{@const unsaved = form.changed(form.names(s.id)).length > 0}
				<li>
					<a href="/settings/{s.id}">
						<span class="text">
							<span class="title">
								{s.title}
								{#if (s.problems ?? []).some((c) => problemCodes.has(c))}<span class="problem" title="Needs setting up">!</span>{/if}
								{#if unsaved}<span class="unsaved">● not saved</span>{/if}
							</span>
							<span class="small muted summary">{s.summary(form)}</span>
						</span>
						<span class="chev" aria-hidden="true">›</span>
					</a>
				</li>
			{/each}
		</ul>
	</div>
{/if}

<style>
	h1 {
		margin: 0;
	}
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		overflow: hidden;
	}
	.list li + li {
		border-top: 1px solid var(--border);
	}
	.list a {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 12px 14px;
		min-height: 56px;
		color: var(--text);
		text-decoration: none;
	}
	.text {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.title {
		font-weight: 600;
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.summary {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.chev {
		color: var(--muted);
		font-size: 1.4rem;
	}
	.unsaved {
		color: var(--warn);
		font-size: 0.75rem;
		font-weight: 400;
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
</style>
