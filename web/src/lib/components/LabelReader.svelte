<script lang="ts">
	// Ask the AI model what a cryptic label stands for ("BTTF2"). A film disc's candidates
	// are ranked again on the server; each title it gives can also be searched for here.
	import type { Job, LabelReading } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { live } from '../live.svelte';

	let {
		job,
		onUse,
		useLabel = 'Search TMDB for'
	}: { job: Job; onUse: (name: string) => void; useLabel?: string } = $props();
	const act = new Action();

	let reading = $state<LabelReading | null>(null);
	const enabled = $derived(live.status?.label_ai ?? false);

	async function ask() {
		const got = await act.run(() => api.readLabel(job.id));
		if (got) reading = got;
	}
</script>

{#if enabled && job.disc.label}
	<div class="stack tight">
		<div class="row">
			<button class="small" onclick={ask} disabled={act.busy}>
				{act.busy ? 'Asking the AI model…' : 'Ask the AI model what the label means'}
			</button>
		</div>
		{#if act.error}<p class="small error-text">{act.error}</p>{/if}
		{#if reading}
			{#if reading.titles.length}
				<p class="small muted">
					{reading.model} read <span class="mono">{job.disc.label}</span> as:
				</p>
				<div class="row wrap">
					{#each reading.titles as t (t)}
						<button class="small" onclick={() => onUse(t)}>{useLabel} “{t}”</button>
					{/each}
				</div>
				{#if reading.candidates.length}
					<p class="small muted">Its titles were searched and the candidates ranked again; a match only its reading supports never confirms on its own.</p>
				{/if}
			{:else}
				<p class="small muted">{reading.model} couldn't say what the label stands for.</p>
			{/if}
		{/if}
	</div>
{/if}

<style>
	.tight {
		gap: 8px;
	}
	.wrap {
		flex-wrap: wrap;
	}
</style>
