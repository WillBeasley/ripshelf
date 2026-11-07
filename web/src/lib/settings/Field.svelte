<script lang="ts">
	// One setting, drawn from its description (config.Settings via /settings/fields): its
	// label, widget, limits and one-line help. Settings search jumps to it by its id.
	import type { Snippet } from 'svelte';
	import type { SecretName, SettingsForm } from './form.svelte.ts';
	import ListInput from './ListInput.svelte';
	import More from './More.svelte';
	import SecretField from './SecretField.svelte';

	let {
		form,
		name,
		label,
		help,
		placeholder,
		disabled = false,
		multiline = false,
		more,
		children
	}: {
		form: SettingsForm;
		name: string;
		label?: string;
		help?: string;
		placeholder?: string;
		disabled?: boolean;
		multiline?: boolean;
		more?: Snippet; // a longer explanation, behind "More"
		children?: Snippet; // anything shown under it (a status, a note)
	} = $props();

	const meta = $derived(form.fields[name]);
	const title = $derived(label ?? meta?.title ?? name);
	const hint = $derived(help ?? meta?.help ?? '');
	const hold = $derived(placeholder ?? meta?.placeholder ?? '');
	// The form's values by name (each widget below binds one).
	const v = $derived(form.f as Record<string, unknown> as Record<string, never>);
	const dirty = $derived(form.dirty(name));
</script>

{#snippet tags()}
	{#if meta?.controller_only}<span class="chip" title="Used by this machine only; remote workers have their own">This machine only</span>{/if}
	{#if dirty}<span class="unsaved" title="Not saved yet">●</span>{/if}
{/snippet}

<div class="setting" id="setting-{name}" data-setting={name}>
	{#if !meta}
		<span class="small error-text">Unknown setting {name}</span>
	{:else if meta.kind === 'secret'}
		<SecretField {form} name={name as SecretName} label={title} />
	{:else if meta.kind === 'bool'}
		<label class="check">
			<input type="checkbox" bind:checked={v[name]} {disabled} />
			<span>{title} {@render tags()}</span>
		</label>
	{:else}
		<label class="field">
			<span>
				{title}{#if meta.kind === 'range'} ≥ {Number(v[name]).toFixed(2)}{/if}
				{@render tags()}
			</span>
			{#if meta.kind === 'choice'}
				<select bind:value={v[name]} {disabled}>
					{#each meta.choices as c (c.value)}<option value={c.value}>{c.label}</option>{/each}
				</select>
			{:else if meta.kind === 'range'}
				<input type="range" min={meta.minimum} max={meta.maximum} step={meta.step} bind:value={v[name]} {disabled} />
			{:else if meta.kind === 'number'}
				<input type="number" inputmode="decimal" min={meta.minimum} max={meta.maximum} step={meta.step ?? 'any'}
					bind:value={v[name]} {disabled} />
			{:else if meta.kind === 'list'}
				<ListInput bind:value={v[name]} {multiline} placeholder={hold} {disabled} class={multiline ? 'mono' : ''} />
			{:else}
				<input class:mono={meta.kind === 'path'} bind:value={v[name]} placeholder={hold} {disabled} />
			{/if}
		</label>
	{/if}
	{#if hint || more}
		<p class="small muted help">
			{hint}
			{#if more}<More>{@render more()}</More>{/if}
		</p>
	{/if}
	{@render children?.()}
</div>

<style>
	.setting {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.help {
		margin: 0;
	}
	.unsaved {
		color: var(--warn);
		font-size: 0.7rem;
		vertical-align: middle;
	}
	.chip {
		vertical-align: middle;
		margin-left: 4px;
	}
</style>
