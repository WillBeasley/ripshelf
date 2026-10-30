<script lang="ts">
	// Under a folder field, the free space, or that it doesn't exist yet, with a button to make it.
	import type { FolderState } from '$lib/api-types';
	import { api } from '$lib/api';
	import { Action } from '$lib/actions.svelte';
	import { gb } from '$lib/format';

	let { path }: { path: string } = $props();
	const act = new Action();
	let info = $state<FolderState | null>(null);

	// Checked a moment after typing stops.
	$effect(() => {
		const p = path.trim();
		info = null;
		if (!p) return;
		const t = setTimeout(async () => {
			const r = await api.folder(p).catch(() => null);
			if (path.trim() === p) info = r;
		}, 400);
		return () => clearTimeout(t);
	});

	async function create() {
		const r = await act.run(() => api.folder(path.trim(), true));
		if (r) info = r;
	}
</script>

{#if info}
	<span class="small folder-status">
		{#if info.exists}
			<span class="muted">{info.created ? '✓ Created. ' : ''}{info.free != null ? `${gb(info.free, 0)} free` : ''}</span>
		{:else}
			<span class="warn">Doesn't exist yet.</span>
			<button type="button" onclick={create} disabled={act.busy}>{act.busy ? 'Creating…' : 'Create it'}</button>
		{/if}
		{#if info.error}<span class="error-text">{info.error}</span>{/if}
		{#if act.error}<span class="error-text">{act.error}</span>{/if}
	</span>
{/if}

<style>
	.folder-status {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 10px;
	}
	.warn {
		color: var(--warn);
	}
	button {
		padding: 2px 10px;
	}
</style>
