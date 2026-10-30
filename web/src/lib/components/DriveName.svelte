<script lang="ts">
	// A drive's name with a pencil to rename it in place (Enter saves, Esc cancels).
	import type { Drive } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { live } from '../live.svelte';

	let { drive, heading = false }: { drive: Drive; heading?: boolean } = $props();
	const act = new Action();
	const MAX = 40;

	let editing = $state(false);
	let value = $state('');
	let input = $state<HTMLInputElement | null>(null);

	function start() {
		value = drive.name ?? '';
		act.error = null;
		editing = true;
	}

	async function save() {
		const out = await act.run(() => api.renameDrive(drive.id, value.trim()));
		if (out) live.upsertDrive(out);
		if (!act.error) editing = false;
	}

	// Not a <form>, because this can sit inside the Settings form.
	function key(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			e.preventDefault();
			save();
		} else if (e.key === 'Escape') {
			e.preventDefault();
			editing = false;
		}
	}

	$effect(() => {
		if (editing && input) {
			input.focus();
			input.select();
		}
	});
</script>

{#if editing}
	<span class="rename">
		<input
			bind:this={input}
			bind:value
			onkeydown={key}
			maxlength={MAX}
			placeholder={`${drive.vendor} ${drive.model}`.replace(/\s+/g, ' ').trim()}
			aria-label="Name for {drive.device}"
		/>
		<button type="button" class="primary small" disabled={act.busy} onclick={save}>Save</button>
		<button type="button" class="small" onclick={() => (editing = false)}>Cancel</button>
		{#if act.error}<span class="small error-text">{act.error}</span>{/if}
	</span>
{:else}
	<span class="named">
		{#if heading}<h2>{drive.display_name}</h2>{:else}<span>{drive.display_name}</span>{/if}
		<button
			type="button"
			class="link pencil"
			onclick={start}
			title={drive.name ? 'Rename this drive (clear the name to show the model)' : 'Name this drive'}
			aria-label="Rename {drive.display_name}">✎</button
		>
	</span>
{/if}

<style>
	.named {
		display: inline-flex;
		align-items: baseline;
		gap: 6px;
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.pencil {
		color: var(--muted);
		font-size: 0.95rem;
	}
	.pencil:hover {
		color: var(--accent);
	}
	.rename {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
	}
	.rename input {
		flex: 1 1 12ch;
		min-width: 0;
	}
</style>
