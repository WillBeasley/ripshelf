<script lang="ts">
	// A disc whose film or episodes are already in the library. Overwrite, keep both, or skip.
	import type { Job } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { jobTitle, size, when } from '../format';
	import { openJob } from '../nav';

	let { job }: { job: Job } = $props();
	const act = new Action();
	const tv = $derived(job.media === 'tv');
	const files = $derived(job.clash?.files ?? []);
	const ripping = $derived(job.rip.state === 'running');
	const name = (p: string) => p.split('/').pop();

	function resolve(action: 'overwrite' | 'keep_both' | 'skip' | 'skip_episodes', ask?: string) {
		if (ask && !confirm(ask)) return;
		act.run(() => api.resolveClash(job.id, action));
	}
</script>

<article class="card stack">
	<header class="spread">
		<button class="link" onclick={() => openJob(job.id)}><strong>{jobTitle(job)}</strong></button>
		<span class="small muted mono">{job.disc.label ?? ''}</span>
	</header>

	{#if job.state === 'checking'}
		<p>Ejected to check. Put the same disc back and close the tray to carry on, or put another disc in to start
			over with that one.</p>
	{:else}
		<p>
			Already in the library{job.clash?.proposed ? ' (if this is the best guess)' : ''}:
			{tv ? `${files.length} file(s)` : ''}
		</p>
		<ul class="files small">
			{#each files as f (f.path)}
				<li><span class="mono">{name(f.path)}</span> <span class="muted">· {size(f.size_bytes)} · {when(f.modified)}</span></li>
			{/each}
		</ul>
		<p class="small muted">
			{job.rip.state === 'pending' ? 'Nothing has been ripped yet.' : ripping ? 'The rip carries on meanwhile.' : 'The rip is done and waits here.'}
		</p>
		<div class="row">
			<button class="danger" disabled={act.busy}
				onclick={() => resolve('overwrite', tv ? 'Replace these episodes with the new rip?' : 'Replace the film in the library with the new rip?')}>
				{tv ? 'Overwrite all' : 'Overwrite and rip'}
			</button>
			{#if !tv && job.clash?.keep_both_name}
				<button disabled={act.busy} onclick={() => resolve('keep_both')}>Keep both → {job.clash.keep_both_name}</button>
			{/if}
			{#if tv}
				<button disabled={act.busy} onclick={() => resolve('skip_episodes')}>Skip these, copy the rest</button>
			{/if}
			<button disabled={act.busy} onclick={() => resolve('skip')}>{tv ? 'Skip the disc' : 'Skip'}</button>
			{#if !ripping}
				<button disabled={act.busy} title="Open the tray to look at the disc without losing the job"
					onclick={() => act.run(() => api.checkDisc(job.id))}>Eject to check</button>
			{/if}
		</div>
	{/if}
	{#if act.error}<p class="small error-text">{act.error}</p>{/if}
</article>

<style>
	.files {
		margin: 0;
		padding-left: 18px;
		overflow-wrap: anywhere;
	}
	.link {
		background: none;
		border: none;
		padding: 0;
		text-align: left;
		color: inherit;
		cursor: pointer;
	}
	p {
		margin: 0;
	}
</style>
