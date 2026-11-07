<script lang="ts">
	// Search every setting by its label (and help and keywords), and jump to it.
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { tick } from 'svelte';
	import type { SettingsForm } from './form.svelte.ts';
	import { reveal } from './reveal.ts';
	import { search, type Hit } from './search.ts';

	let { form }: { form: SettingsForm } = $props();

	let query = $state('');
	let active = $state(0);
	let open = $state(false);
	const hits = $derived(search(form, query));
	const id = 'settings-search';

	async function jump(h: Hit) {
		query = '';
		open = false;
		const path = `/settings/${h.section}`;
		if (page.url.pathname !== path) await goto(`${path}#setting-${h.name}`);
		else history.replaceState(history.state, '', `#setting-${h.name}`);
		await tick();
		reveal(h.name);
	}

	function key(e: KeyboardEvent) {
		if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
			e.preventDefault();
			if (!hits.length) return;
			active = (active + (e.key === 'ArrowDown' ? 1 : hits.length - 1)) % hits.length;
		} else if (e.key === 'Enter' && hits[active]) {
			e.preventDefault();
			jump(hits[active]);
		} else if (e.key === 'Escape') {
			query = '';
		}
	}
</script>

<div class="search">
	<input
		type="search"
		placeholder="Search settings"
		aria-label="Search settings"
		role="combobox"
		aria-expanded={open && hits.length > 0}
		aria-controls="{id}-list"
		aria-activedescendant={hits.length ? `${id}-${active}` : undefined}
		autocomplete="off"
		bind:value={query}
		oninput={() => {
			active = 0;
			open = true;
		}}
		onfocus={() => (open = true)}
		onblur={() => setTimeout(() => (open = false), 150)}
		onkeydown={key}
	/>
	{#if open && query.trim()}
		<ul class="hits" id="{id}-list" role="listbox">
			{#each hits as h, i (h.name)}
				<li id="{id}-{i}" role="option" aria-selected={i === active}>
					<button type="button" tabindex="-1" class:active={i === active} onmousedown={(e) => e.preventDefault()}
						onclick={() => jump(h)} onmouseenter={() => (active = i)}>
						<span>{h.title}</span>
						<span class="small muted">{h.sectionTitle}</span>
					</button>
				</li>
			{:else}
				<li class="small muted none">No setting matches “{query.trim()}”</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.search {
		position: relative;
	}
	.hits {
		position: absolute;
		z-index: 20;
		top: calc(100% + 4px);
		left: 0;
		right: 0;
		min-width: min(320px, calc(100vw - 32px));
		margin: 0;
		padding: 4px;
		list-style: none;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 10px;
		box-shadow: 0 8px 24px rgb(0 0 0 / 0.35);
		max-height: 60vh;
		overflow-y: auto;
	}
	.hits button {
		width: 100%;
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 1px;
		background: none;
		border: none;
		text-align: left;
		min-height: 44px;
		padding: 6px 10px;
	}
	.hits button.active {
		background: var(--surface-2);
	}
	.none {
		padding: 10px;
	}
</style>
