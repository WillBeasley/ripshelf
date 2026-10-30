<script lang="ts">
	import { guessTitle, kindLabel } from '../format';
	import { live } from '../live.svelte';
	import { openJob } from '../nav';
	import Progress from './Progress.svelte';
	import StatePill from './StatePill.svelte';

	// Jobs whose disc is ripped, now transcoding or copying to the library while the
	// drive has moved on to the next disc.
	let { hideEmpty = false }: { hideEmpty?: boolean } = $props();

	const jobs = $derived(live.afterDriveJobs);
	let artFailed = $state<Record<string, boolean>>({});
</script>

{#if jobs.length || !hideEmpty}
	<section class="card stack">
		<div class="spread">
			<h2>Transcoding and copying</h2>
			{#if jobs.length}<span class="small muted">{jobs.length} job{jobs.length === 1 ? '' : 's'}</span>{/if}
		</div>
		{#each jobs as job (job.id)}
			{@const progress = live.progressFor(job)}
			<button class="job" onclick={() => openJob(job.id)}>
				{#if job.art && !artFailed[job.art]}
					<img
						class="art"
						src={`/api/jobs/${job.id}/art?k=${job.art}`}
						alt=""
						loading="lazy"
						onerror={() => (artFailed[job.art!] = true)}
					/>
				{:else}
					<div class="art none" aria-hidden="true">💿</div>
				{/if}
				<div class="body">
					<div class="spread">
						<div class="title">
							<strong>{guessTitle(job)}</strong>
							<div class="small muted">
								{kindLabel(job.disc.kind)} · from {live.driveName(job)}
							</div>
						</div>
						<StatePill state={job.state} />
					</div>
					{#if progress}
						<!-- The bar's caption already names the step. -->
						<Progress {progress} />
						{#if job.step?.detail}<p class="small muted">{job.step.detail}</p>{/if}
					{:else if job.step}
						<p class="small">
							{job.step.label}{#if job.step.detail}<span class="muted"> · {job.step.detail}</span>{/if}
						</p>
					{:else}
						<p class="small muted">{job.state === 'transcoding' ? 'Checking the titles…' : 'Starting the copy…'}</p>
					{/if}
				</div>
			</button>
		{:else}
			<p class="small muted">Nothing is transcoding or copying. Ripped discs show here once they leave the drive.</p>
		{/each}
	</section>
{/if}

<style>
	.job {
		display: flex;
		gap: 12px;
		align-items: flex-start;
		width: 100%;
		text-align: left;
		background: var(--surface-2);
		border-color: transparent;
		padding: 10px 12px;
	}
	.art {
		width: 44px;
		aspect-ratio: 2 / 3;
		object-fit: cover;
		border-radius: 5px;
		flex: none;
		background: var(--surface);
	}
	.art.none {
		display: grid;
		place-items: center;
		font-size: 1.2rem;
	}
	.body {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.title {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	p {
		margin: 0;
	}
</style>
