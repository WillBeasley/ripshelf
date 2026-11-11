<script lang="ts">
	import type { Job } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';

	// A finished job being identified again: what it was filed as, and the way back.
	let { job }: { job: Job } = $props();
	const act = new Action();
	const moved = $derived((job.refile?.moves ?? []).filter((m) => m.done).length);
</script>

{#if job.refile}
	<section class="refile small">
		<p>
			<strong>Identifying a finished disc again.</strong> It was filed as {job.refile.title}. Its files stay where
			they are until you confirm the right one, then move to its names. Nothing is ripped again.
		</p>
		<div class="row">
			<button class="small" disabled={act.busy} onclick={() => act.run(() => api.keepIdentity(job.id))}>
				Keep it as {job.refile.title}{moved ? ` (puts back the ${moved} moved)` : ''}
			</button>
		</div>
		{#if act.error}<p class="error-text">{act.error}</p>{/if}
	</section>
{/if}

<style>
	.refile {
		background: var(--info-bg);
		border-radius: 9px;
		padding: 8px 12px;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	p {
		margin: 0;
	}
</style>
