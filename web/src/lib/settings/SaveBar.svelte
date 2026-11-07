<script lang="ts">
	// Save or discard a section's changes. Shown only while there are some (and briefly
	// after saving), above the phone's tab bar. Other sections' unsaved changes are named.
	import { SECTIONS, type SectionInfo } from './sections.ts';
	import type { SettingsForm } from './form.svelte.ts';

	let { form, section }: { form: SettingsForm; section: SectionInfo } = $props();

	const names = $derived(form.names(section.id));
	const changed = $derived(form.changed(names));
	const elsewhere = $derived(SECTIONS.filter((s) => s.id !== section.id && form.changed(form.names(s.id)).length));
	let justSaved = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function save() {
		if (await form.save(names)) {
			justSaved = true;
			clearTimeout(timer);
			timer = setTimeout(() => (justSaved = false), 2500);
		}
	}
	// Ctrl/Cmd+S saves the section.
	function key(e: KeyboardEvent) {
		if ((e.ctrlKey || e.metaKey) && e.key === 's' && changed.length) {
			e.preventDefault();
			save();
		}
	}
</script>

<svelte:window onkeydown={key} />

{#if changed.length || form.act.error || justSaved}
	<div class="bar" role="region" aria-label="Save changes">
		<div class="text small">
			{#if changed.length}
				<strong>{changed.length} unsaved change{changed.length === 1 ? '' : 's'}</strong>
				{#if elsewhere.length}
					<span class="muted">· also in {#each elsewhere as s, i (s.id)}{i ? ', ' : ''}<a href="/settings/{s.id}">{s.title}</a>{/each}</span>
				{/if}
			{:else if justSaved && !form.act.error}
				<span class="ok">✓ Saved</span>
			{/if}
			{#if form.act.error}<span class="error-text">{form.act.error}</span>{/if}
		</div>
		{#if changed.length}
			<div class="buttons">
				<button type="button" onclick={() => form.discard(names)} disabled={form.act.busy}>Discard</button>
				<button class="primary" type="button" onclick={save} disabled={form.act.busy}>
					{form.act.busy ? 'Saving…' : 'Save'}
				</button>
			</div>
		{/if}
	</div>
{/if}

<style>
	.bar {
		position: sticky;
		bottom: calc(64px + env(safe-area-inset-bottom));
		z-index: 5;
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 8px 12px;
		padding: 10px 12px;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: 0 -4px 16px rgb(0 0 0 / 0.3);
	}
	.text {
		min-width: 0;
		flex: 1 1 200px;
	}
	.buttons {
		display: flex;
		gap: 8px;
		margin-left: auto;
	}
	.buttons button {
		min-height: 44px;
		min-width: 88px;
		justify-content: center;
	}
	.ok {
		color: var(--ok);
	}
	@media (min-width: 760px) {
		.bar {
			bottom: 12px;
		}
	}
</style>
