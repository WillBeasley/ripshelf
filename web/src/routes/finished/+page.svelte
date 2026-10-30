<script lang="ts">
	import { live } from '$lib/live.svelte';
	import { jobTitle, kindLabel, when } from '$lib/format';
	import { openJob } from '$lib/nav';
	import Poster from '$lib/components/Poster.svelte';
	import StatePill from '$lib/components/StatePill.svelte';
	import { api } from '$lib/api';
	import { Action } from '$lib/actions.svelte';

	const act = new Action();
	let selecting = $state(false);
	let picked = $state<Set<number>>(new Set());
	let filter = $state<'all' | 'done' | 'failed' | 'cancelled'>('all');

	const shown = $derived(live.finishedJobs.filter((j) => filter === 'all' || j.state === filter));

	function toggle(id: number) {
		const next = new Set(picked);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		picked = next;
	}

	function stopSelecting() {
		selecting = false;
		picked = new Set();
	}

	async function removePicked() {
		const ids = [...picked];
		if (!ids.length) return;
		const what = ids.length === 1 ? 'this job' : `these ${ids.length} jobs`;
		if (!confirm(`Delete ${what} from the history? Their logs go too. Files in the library are kept.`)) return;
		const r = await act.run(() => api.deleteJobs(ids));
		if (r) {
			live.removeJobs(r.deleted);
			stopSelecting();
		}
	}
</script>

<svelte:head><title>Finished · ripshelf</title></svelte:head>

<div class="stack">
	<div class="spread">
		<h1>Finished</h1>
		{#if live.finishedJobs.length}
			{#if selecting}
				<button onclick={stopSelecting}>Done</button>
			{:else}
				<button onclick={() => (selecting = true)}>Select…</button>
			{/if}
		{/if}
	</div>
	{#if live.finishedJobs.length}
		<div class="row">
			<select bind:value={filter} aria-label="Show">
				<option value="all">All</option>
				<option value="done">Done</option>
				<option value="failed">Failed</option>
				<option value="cancelled">Cancelled</option>
			</select>
			{#if selecting}
				<button onclick={() => (picked = new Set(shown.map((j) => j.id)))}>Select all {shown.length}</button>
				<button class="danger" disabled={!picked.size || act.busy} onclick={removePicked}>
					Delete {picked.size || ''} from history
				</button>
			{/if}
		</div>
		{#if selecting}<p class="small muted">Only the history is deleted. Files in the library are kept.</p>{/if}
		{#if act.error}<p class="small error-text">{act.error}</p>{/if}
	{/if}
	{#if live.loaded && !live.finishedJobs.length}
		<p class="muted">No finished jobs yet.</p>
	{/if}
	<ul class="list">
		{#each shown as job (job.id)}
			<li class:picked={picked.has(job.id)}>
				<button class="card item" onclick={() => (selecting ? toggle(job.id) : openJob(job.id))}>
					{#if selecting}
						<input type="checkbox" checked={picked.has(job.id)} tabindex="-1" aria-label="Select" />
					{/if}
					<Poster url={job.identity?.poster_url ?? null} alt={jobTitle(job)} width={44} />
					<div class="body">
						<div class="spread">
							<strong>{jobTitle(job)}</strong>
							<StatePill state={job.state} />
						</div>
						<div class="small muted">
							{kindLabel(job.disc.kind)} · {when(job.finished_at ?? job.updated_at)}
							{#if job.disc.label}· <span class="mono">{job.disc.label}</span>{/if}
						</div>
						{#if job.state === 'done' && job.destination}
							<div class="mono muted">{job.destination}</div>
						{:else if job.problem}
							<div class="small error-text">{job.problem.message}</div>
						{/if}
					</div>
				</button>
			</li>
		{/each}
	</ul>
</div>

<style>
	.list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.picked .item {
		outline: 2px solid var(--accent, currentColor);
	}
	.item input {
		margin-top: 4px;
		pointer-events: none;
	}
	.item {
		width: 100%;
		display: flex;
		gap: 12px;
		text-align: left;
		align-items: flex-start;
		font-size: 0.95rem;
	}
	.body {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 3px;
		overflow-wrap: anywhere;
	}
</style>
