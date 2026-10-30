<script lang="ts">
	import { live } from '$lib/live.svelte';
	import DriveCard from '$lib/components/DriveCard.svelte';
	import SetupProblems from '$lib/components/SetupProblems.svelte';
	import StoragePanel from '$lib/components/StoragePanel.svelte';
	import DriveStatsPanel from '$lib/components/DriveStatsPanel.svelte';
	import AfterDriveJobs from '$lib/components/AfterDriveJobs.svelte';
	import { api } from '$lib/api';
	import { Action } from '$lib/actions.svelte';

	const act = new Action();
	let note = $state<string | null>(null);

	async function redetect() {
		note = null;
		const before = live.drives.length;
		const drives = await act.run(() => api.redetect());
		if (drives) {
			live.drives = drives;
			const added = drives.length - before;
			note = added > 0 ? `Found ${added} new drive(s)` : `${drives.length} drive(s), nothing new`;
		}
	}
</script>

<svelte:head><title>Overview · ripshelf</title></svelte:head>

<div class="stack">
	<div class="spread">
		<h1>Overview</h1>
		<button onclick={redetect} disabled={act.busy} title="Look for drives again, e.g. one plugged in since ripshelf started">
			{act.busy ? 'Looking…' : '↻ Re-detect drives'}
		</button>
	</div>
	{#if note}<p class="small muted">{note}</p>{/if}
	{#if act.error}<p class="small error-text">{act.error}</p>{/if}
	<SetupProblems />
	{#if live.loaded && !live.drives.length}
		<p class="muted">No optical drives found. Check that /dev/sr* is passed into the container.</p>
	{/if}
	<div class="grid">
		{#each live.drives as drive (drive.id)}
			<DriveCard {drive} />
		{/each}
	</div>
	<AfterDriveJobs hideEmpty />
	<StoragePanel />
	<DriveStatsPanel />
</div>
