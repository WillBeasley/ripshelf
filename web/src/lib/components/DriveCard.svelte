<script lang="ts">
	import type { Drive } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { guessTitle, kindLabel } from '../format';
	import { isAfterDrive, live } from '../live.svelte';
	import { openJob } from '../nav';
	import DriveName from './DriveName.svelte';
	import PresetForm from './PresetForm.svelte';
	import Progress from './Progress.svelte';
	import StatePill from './StatePill.svelte';

	let { drive }: { drive: Drive } = $props();
	const act = new Action();

	const job = $derived(live.jobFor(drive));
	// Ripped and now transcoding or copying, so shown under "After the drive", not here.
	const afterDrive = $derived(!!job && isAfterDrive(job));
	// A finished job stays linked to the drive; only show it while the disc is in.
	const current = $derived(
		job && !afterDrive && (drive.status === 'disc' || !['done', 'failed', 'cancelled'].includes(job.state))
			? job
			: null
	);
	const progress = $derived(current ? live.progressFor(current) : null);
	const showProgress = $derived(
		current && progress && (current.rip.state === 'running' || current.state === 'finalising' || current.state === 'transcoding')
	);
	const problem = $derived(drive.problem ?? (current?.state === 'failed' ? current.problem : null));
	const fake = $derived(live.status?.fake_hardware ?? false);
	// MakeMKV has the drive open, so the tray can't move until the scan or rip ends.
	const working = $derived(!!current && (current.state === 'scanning' || current.rip.state === 'running'));
	const activeJob = $derived(!!current && !['done', 'failed', 'cancelled'].includes(current.state));

	function eject() {
		if (activeJob && !confirm('This disc has a job in progress. Eject it anyway?')) return;
		act.run(() => api.eject(drive.id));
	}
	let artFailed = $state<Record<string, boolean>>({});

	// Before the scan is done (or after it failed), you can say what the disc is.
	const early = $derived(
		!!current &&
			(['detected', 'scanning'].includes(current.state) || (current.state === 'failed' && !current.main_title))
	);
	let presetOpen = $state(false);
	const preset = $derived(current?.preset ?? null);
	const presetText = $derived.by(() => {
		if (!preset) return '';
		if (preset.media === 'movie' && preset.candidate) {
			const c = preset.candidate;
			return c.year ? `${c.title} (${c.year})` : c.title;
		}
		const parts = [preset.show?.name ?? 'a TV show'];
		if (preset.season != null) parts.push(`season ${preset.season}`);
		if (preset.disc_number) parts.push(`disc ${preset.disc_number}`);
		if (preset.start_episode) parts.push(`from episode ${preset.start_episode}`);
		return parts.join(', ');
	});
</script>

<article class="card drive" class:problem={!!problem}>
	<header class="spread">
		<div>
			<DriveName {drive} heading />
			<div class="small muted">
				{#if drive.name}{`${drive.vendor} ${drive.model}`.replace(/\s+/g, ' ')} · {/if}<span class="mono"
					>{drive.device}{drive.sg_device ? ` · ${drive.sg_device}` : ''}</span
				>
			</div>
		</div>
		{#if current}
			<StatePill state={current.state} />
		{:else if !drive.enabled}
			<span class="chip">Disabled</span>
		{:else}
			<StatePill drive={drive.status} />
		{/if}
	</header>

	<div class="row chips">
		{#if drive.worker}<span class="chip">on {drive.worker}</span>{/if}
		{#each drive.capabilities as c (c)}
			<span class="chip" class:uhd={c === 'uhd'}>{c === 'bluray' ? 'Blu-ray' : c.toUpperCase()}</span>
		{/each}
	</div>

	{#if !drive.enabled}
		<p class="small muted">
			{#if drive.status === 'disc'}Disc <span class="mono">{drive.disc_label || 'no label'}</span> · {/if}Disabled in
			Settings, so ripshelf doesn't scan, rip or move the tray of this drive.
		</p>
	{:else if current}
		<button class="disc" onclick={() => openJob(current.id)}>
			<div class="disc-row">
				{#if current.art && !artFailed[current.art]}
					<img
						class="art"
						src={`/api/jobs/${current.id}/art?k=${current.art}`}
						alt=""
						loading="lazy"
						onerror={() => (artFailed[current.art!] = true)}
					/>
				{:else}
					<div class="art none" aria-hidden="true">💿</div>
				{/if}
				<div class="title">
					<strong>{guessTitle(current)}</strong>
					<div class="small muted">
						{kindLabel(current.disc.kind)}{#if current.disc.label}{' · '}<span class="mono"
								>{current.disc.label}</span
							>{/if}
					</div>
					{#if current.art_proposed}<span class="chip">proposed</span>{/if}
				</div>
				<span class="muted chev" aria-hidden="true">›</span>
			</div>
		</button>

		{#if showProgress && progress}
			<Progress {progress} />
		{:else if current.state === 'scanning'}
			<p class="small muted">MakeMKV is scanning the disc…</p>
		{/if}

		{#if early}
			{#if presetOpen}
				<PresetForm job={current} onclose={() => (presetOpen = false)} />
			{:else if preset}
				<div class="said small">
					<span class="grow">You said this is <strong>{presetText}</strong>. It's used once the scan is done.</span>
					<button class="link small" onclick={() => (presetOpen = true)}>Change</button>
					<button class="link small" disabled={act.busy} onclick={() => act.run(() => api.clearPreset(current.id))}
						>Forget</button
					>
				</div>
			{:else}
				<button class="link small know" onclick={() => (presetOpen = true)}>I know what this disc is</button>
			{/if}
		{/if}

		{#if current.state === 'review'}
			<a class="button attention" href="/review">Needs review →</a>
		{:else if current.state === 'clash'}
			<a class="button attention" href="/review">Already in the library, so decide →</a>
		{:else if current.state === 'checking'}
			<p class="small muted">Ejected to check. Put the same disc back to carry on.</p>
		{/if}
	{:else if drive.status === 'empty' || drive.status === 'tray_open'}
		<p class="small muted">No disc</p>
	{/if}
	{#if drive.enabled && afterDrive && job}
		<p class="small muted">
			{drive.status === 'disc' ? 'Ripped' : 'Last disc'}: <button class="link" onclick={() => openJob(job.id)}>{guessTitle(job)}</button>
			is {job.state === 'transcoding' ? 'transcoding' : 'copying to the library'}.{drive.status === 'disc'
				? ' The disc can come out.'
				: ''}
		</p>
	{/if}

	{#if problem && drive.enabled}
		<div class="problem-box">
			<p>{problem.message}</p>
			{#if problem.hint}<p class="hint">{problem.hint}</p>{/if}
			{#if problem.detail}
				<details>
					<summary>What MakeMKV said</summary>
					<p class="mono">{problem.detail}</p>
				</details>
			{/if}
			<div class="row">
				{#if problem.actions.includes('retry_scan') || problem.actions.includes('retry')}
					<button
						disabled={act.busy}
						onclick={() => act.run(() => api.rescan(drive.id))}
					>
						Retry scan
					</button>
				{/if}
				{#if problem.actions.includes('setup')}
					<a class="button" href="/settings">Setup</a>
				{/if}
			</div>
		</div>
	{/if}

	<footer class="row">
		{#if !drive.enabled}
			<!-- no actions on a disabled drive -->
		{:else if drive.status === 'tray_open'}
			<button disabled={act.busy} onclick={() => act.run(() => api.closeTray(drive.id))}>Close tray</button>
		{:else if drive.status !== 'error'}
			<button disabled={act.busy || working} title={working ? 'MakeMKV is using the drive' : undefined} onclick={eject}>Eject</button>
		{/if}
		{#if drive.enabled && drive.status === 'disc' && !afterDrive && (!current || ['done', 'cancelled'].includes(current.state))}
			<button
				disabled={act.busy}
				title="Read the disc in this drive again as a new job"
				onclick={() => act.run(() => api.rescan(drive.id))}>Scan again</button
			>
		{/if}
		{#if current && !['done', 'failed', 'cancelled'].includes(current.state)}
			<button class="danger" disabled={act.busy} onclick={() => act.run(() => api.cancel(current.id))}>
				Cancel
			</button>
		{/if}
		{#if fake && drive.status !== 'disc'}
			<button onclick={() => act.run(() => api.devInsert(drive.id))}>Insert disc (fake)</button>
		{/if}
	</footer>
	{#if act.error}<p class="small error-text">{act.error}</p>{/if}
</article>

<style>
	.drive {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.drive.problem {
		border-color: var(--bad);
	}
	.chips {
		gap: 5px;
	}
	.disc {
		display: block;
		width: 100%;
		text-align: left;
		background: var(--surface-2);
		border-color: transparent;
		padding: 10px 12px;
	}
	.disc-row {
		display: flex;
		gap: 12px;
		align-items: center;
	}
	.art {
		width: 54px;
		aspect-ratio: 2 / 3;
		object-fit: cover;
		border-radius: 5px;
		flex: none;
		background: var(--surface);
	}
	.art.none {
		display: grid;
		place-items: center;
		font-size: 1.4rem;
	}
	.title {
		flex: 1;
		min-width: 0;
		overflow-wrap: anywhere;
		display: flex;
		flex-direction: column;
		gap: 3px;
		align-items: flex-start;
	}
	.chev {
		flex: none;
	}
	.problem-box {
		background: var(--bad-bg);
		color: var(--text);
		border-radius: 9px;
		padding: 10px 12px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.problem-box p {
		margin: 0;
		font-size: 0.9rem;
	}
	.problem-box .hint {
		color: var(--muted);
	}
	.problem-box details {
		font-size: 0.8rem;
	}
	.problem-box details p {
		font-size: 0.8rem;
		word-break: break-word;
		margin-top: 4px;
	}
	.said {
		display: flex;
		gap: 8px;
		align-items: baseline;
		flex-wrap: wrap;
	}
	.said .grow {
		flex: 1;
		min-width: 0;
	}
	.know {
		align-self: flex-start;
	}
	footer:empty {
		display: none;
	}
	p {
		margin: 0;
	}
</style>
