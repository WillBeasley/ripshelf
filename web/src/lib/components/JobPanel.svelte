<script lang="ts">
	import type { JobDetail, JobEvent } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { duration, jobTitle, kindLabel, size, when } from '../format';
	import { live } from '../live.svelte';
	import { closeJob } from '../nav';
	import ContributionPanel from './ContributionPanel.svelte';
	import Progress from './Progress.svelte';
	import StatePill from './StatePill.svelte';

	let { jobId }: { jobId: number } = $props();
	const act = new Action();

	let detail = $state<JobDetail | null>(null);
	let events = $state<JobEvent[]>([]);
	let log = $state<string[]>([]);
	// Which log the panel shows, MakeMKV's (scan/rip) or ffmpeg's (transcode).
	let logKind = $state<'rip' | 'transcode'>('rip');
	let logAll = $state(false);
	let loadError = $state<string | null>(null);

	// The live copy is newer than the detail fetched on open.
	const job = $derived(live.jobs[jobId] ?? detail);
	const progress = $derived(job ? live.progressFor(job) : null);
	const active = $derived(job ? !['done', 'failed', 'cancelled'].includes(job.state) : false);

	async function load() {
		try {
			const d = await api.job(jobId);
			detail = d;
			events = d.events;
			live.upsertJob(d);
			loadError = null;
		} catch (e) {
			loadError = e instanceof Error ? e.message : String(e);
		}
	}

	async function loadLog() {
		try {
			const kind = logKind;
			const lines = await api.log(jobId, logAll ? 500 : 30, kind);
			if (kind === logKind) log = lines;
		} catch {
			/* keep the old lines */
		}
	}

	const hasTranscode = $derived(!!job && (job.state === 'transcoding' || !!job.transcode?.files?.length));
	// Open on the transcode log while ffmpeg is the thing working (once per job).
	let autoKind = false;

	$effect(() => {
		jobId;
		detail = null;
		events = [];
		log = [];
		logKind = 'rip';
		logAll = false;
		autoKind = false;
		load();
		return live.onJobEvent((e) => {
			if (e.job_id === jobId && !events.some((x) => x.id === e.id)) events = [...events, e];
		});
	});

	$effect(() => {
		if (job?.state === 'transcoding' && !autoKind) {
			autoKind = true;
			showLog('transcode');
		}
	});

	$effect(() => {
		jobId;
		logKind;
		logAll;
		loadLog();
	});

	function showLog(kind: 'rip' | 'transcode') {
		if (kind !== logKind) log = [];
		logKind = kind;
	}

	$effect(() => {
		if (!active) return;
		const t = setInterval(loadLog, 4000);
		return () => clearInterval(t);
	});

	async function remove() {
		if (!confirm('Delete this job from the history? Its log goes too. Files in the library are kept.')) return;
		const r = await act.run(() => api.deleteJobs([jobId]));
		if (r) {
			live.removeJobs(r.deleted);
			closeJob();
		}
	}

	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape') closeJob();
	}

	const EVENT_ICON: Record<string, string> = {
		state: '→',
		info: '•',
		warning: '!',
		error: '✗',
		identity: '✓',
		rip: '◉',
		finalise: '▣',
		transcode: '⚙'
	};
</script>

<svelte:window onkeydown={onKey} />

<div class="backdrop" onclick={closeJob} aria-hidden="true"></div>
<aside class="panel" aria-label="Job details">
	<header class="spread sticky">
		<div class="head">
			{#if job}
				<h2>{jobTitle(job)}</h2>
				<div class="small muted">
					Job {job.id} · {kindLabel(job.disc.kind)}{job.mode === 'backup' ? ' · full backup' : ''} · {live.driveName(job)}
				</div>
			{:else}
				<h2>Job {jobId}</h2>
			{/if}
		</div>
		<button class="close" onclick={closeJob}>✕ Close</button>
	</header>

	{#if loadError}
		<p class="error-text">{loadError}</p>
	{/if}

	{#if job}
		<div class="row"><StatePill state={job.state} />
			{#if job.identity_source}<span class="small muted">identified by {job.identity_source}</span>{/if}
		</div>

		{#if job.step && (job.state === 'finalising' || job.state === 'transcoding')}
			<p class="small">
				{job.step.label}{#if job.step.detail}<span class="muted"> · {job.step.detail}</span>{/if}
			</p>
		{/if}
		{#if progress && (job.rip.state === 'running' || job.state === 'finalising' || job.state === 'transcoding')}
			<Progress {progress} />
		{/if}

		{#if job.problem}
			<div class="problem">
				<p>{job.problem.message}</p>
				{#if job.problem.hint}<p class="muted">{job.problem.hint}</p>{/if}
				{#if job.problem.detail}<p class="mono small muted detail">{job.problem.detail}</p>{/if}
			</div>
		{/if}

		<section>
			<h3>Destination</h3>
			<p class="mono">{job.destination ?? 'Not decided until the disc is identified'}</p>
			{#if job.destination && job.state !== 'done'}<p class="small muted">planned</p>{/if}
		</section>

		{#if job.tv?.auto_evidence?.length}
			<section>
				<h3>{job.tv.auto_confirmed ? 'Confirmed without review because' : 'Not confirmed automatically'}</h3>
				<ul class="tc small">
					{#each job.tv.auto_evidence as e, i (i)}<li>{e}</li>{/each}
				</ul>
			</section>
		{/if}

		{#if job.transcode?.files?.length}
			<section>
				<h3>Transcode</h3>
				<ul class="tc small">
					{#each job.transcode.files as f (f.title_index)}
						<li>
							Title {f.title_index}: <strong>{f.action}</strong>
							{#if f.source_codec}<span class="muted">({f.source_codec}{f.encoder ? ` → H.265, ${f.encoder === 'vaapi' ? 'GPU' : 'x265'}` : ''}{f.crop ? `, ${f.crop}` : ''})</span>{/if}
							{#if f.size_before && f.size_after}<span class="muted"> · {Math.round((100 * f.size_after) / f.size_before)}% of the size</span>{/if}
							{#if f.note}<div class="muted">{f.note}</div>{/if}
						</li>
					{/each}
				</ul>
			</section>
		{/if}

		<section>
			<h3>Disc</h3>
			<dl>
				<dt>Volume label</dt><dd class="mono">{job.disc.label ?? '–'}</dd>
				{#if job.disc.name}<dt>Disc title</dt><dd>{job.disc.name}</dd>{/if}
				<dt>Size</dt><dd>{size(job.disc.size_bytes)}</dd>
				{#if job.main_title}
					<dt>Main feature</dt>
					<dd>
						title {job.main_title.index} · {duration(job.main_title.duration_s)} · {size(job.main_title.size_bytes)}
						{#if job.main_title.resolution}· {job.main_title.resolution}{/if}
					</dd>
				{/if}
				<dt>Started</dt><dd>{when(job.created_at)}</dd>
				{#if job.finished_at}<dt>Finished</dt><dd>{when(job.finished_at)}</dd>{/if}
			</dl>
		</section>

		{#if job.identity || job.disc_match}
			<ContributionPanel {job} />
		{/if}

		<div class="row">
			{#if job.state === 'failed'}
				<button class="primary" disabled={act.busy} onclick={() => act.run(() => api.retry(jobId))}>Retry</button>
			{/if}
			{#if ['review', 'ripping', 'failed', 'identifying', 'clash'].includes(job.state)}
				<button disabled={act.busy} onclick={() => act.run(() => api.identifyAgain(jobId))}>Identify again</button>
			{/if}
			{#if (job.state === 'review' || job.state === 'clash') && job.rip.state !== 'running'}
				<button disabled={act.busy} title="Open the tray to look at the disc without losing the job"
					onclick={() => act.run(() => api.checkDisc(jobId))}>Eject to check</button>
			{/if}
			{#if active}
				<button class="danger" disabled={act.busy} onclick={() => act.run(() => api.cancel(jobId))}>Cancel job</button>
			{:else}
				<button class="danger" disabled={act.busy} onclick={remove}>Delete from history</button>
			{/if}
		</div>
		{#if act.error}<p class="small error-text">{act.error}</p>{/if}

		<section>
			<h3>Timeline</h3>
			<ol class="timeline">
				{#each events as e (e.id)}
					<li class={e.kind}>
						<span class="icon" aria-hidden="true">{EVENT_ICON[e.kind] ?? '•'}</span>
						<span class="msg">{e.message}</span>
						<time class="small muted">{when(e.ts)}</time>
					</li>
				{/each}
			</ol>
		</section>

		<section>
			<div class="spread">
				{#if hasTranscode}
					<div class="row log-tabs" role="tablist" aria-label="Log">
						<button role="tab" class:on={logKind === 'rip'} aria-selected={logKind === 'rip'} onclick={() => showLog('rip')}>MakeMKV log</button>
						<button role="tab" class:on={logKind === 'transcode'} aria-selected={logKind === 'transcode'} onclick={() => showLog('transcode')}>Transcode log</button>
					</div>
				{:else}
					<h3>MakeMKV log</h3>
				{/if}
				<button class="link small" onclick={() => (logAll = !logAll)}>{logAll ? 'Last 30 lines' : 'Show more'}</button>
			</div>
			{#if log.length}
				<pre class="log" class:tall={logAll}>{log.join('\n')}</pre>
			{:else}
				<p class="small muted">{logKind === 'transcode' ? 'ffmpeg has not started yet' : 'No output yet'}</p>
			{/if}
		</section>
	{/if}
	<button class="close-bottom" onclick={closeJob}>Close</button>
</aside>

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgb(0 0 0 / 0.35);
		z-index: 20;
	}
	.panel {
		position: fixed;
		z-index: 21;
		top: 0;
		right: 0;
		bottom: 0;
		width: min(100vw, 520px);
		background: var(--bg);
		border-left: 1px solid var(--border);
		overflow-y: auto;
		padding: 16px 16px calc(24px + env(safe-area-inset-bottom));
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.head {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.close {
		flex: none;
	}
	/* The close button stays in reach however far the panel is scrolled. */
	.sticky {
		position: sticky;
		top: -16px;
		margin: -16px -16px 0;
		padding: 16px;
		background: var(--bg);
		border-bottom: 1px solid var(--border);
		z-index: 1;
	}
	.close-bottom {
		align-self: stretch;
	}
	section {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	section p {
		margin: 0;
	}
	.problem {
		background: var(--bad-bg);
		border-radius: 9px;
		padding: 10px 12px;
		font-size: 0.9rem;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.problem p {
		margin: 0;
	}
	.problem .detail {
		word-break: break-word;
	}
	dl {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 3px 12px;
		margin: 0;
		font-size: 0.88rem;
	}
	dt {
		color: var(--muted);
	}
	dd {
		margin: 0;
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.timeline {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 5px;
		font-size: 0.87rem;
	}
	.timeline li {
		display: grid;
		grid-template-columns: 1.2em 1fr auto;
		gap: 6px;
	}
	.msg {
		overflow-wrap: anywhere;
	}
	.icon {
		color: var(--muted);
		text-align: center;
	}
	.error .icon,
	.error .msg {
		color: var(--bad);
	}
	.warning .icon {
		color: var(--warn);
	}
	.identity .icon {
		color: var(--ok);
	}
	.tc {
		margin: 0;
		padding-left: 18px;
	}
	.log {
		margin: 0;
		background: var(--surface-2);
		border-radius: 8px;
		padding: 10px;
		font-size: 0.75rem;
		line-height: 1.4;
		max-height: 280px;
		overflow: auto;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.log.tall {
		max-height: 70vh;
	}
	.log-tabs {
		gap: 4px;
	}
	.log-tabs button {
		min-height: 30px;
		padding: 4px 10px;
		font-size: 0.85rem;
		background: transparent;
	}
	.log-tabs button.on {
		background: var(--surface-2);
		font-weight: 600;
	}
</style>
