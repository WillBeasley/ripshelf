<script lang="ts">
	// Sending a confirmed disc to TheDiscDB (the contribute setting in Settings → Metadata providers).
	import type { ContributionExtra, ContributionView, Job } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { duration, size, when } from '../format';

	type Kind = ContributionExtra['kind'] | '';
	const kinds: [Kind, string][] = [
		['', 'Leave out'],
		['Extra', 'Extra'],
		['Featurette', 'Featurette'],
		['DeletedScene', 'Deleted scene'],
		['Trailer', 'Trailer']
	];
	const kindLabel: Record<string, string> = Object.fromEntries(kinds);

	let { job }: { job: Job } = $props();
	const act = new Action();

	let view = $state<ContributionView | null>(null);
	let preview = $state(false);
	// The naming list: each title that isn't the film or an episode, sent as an extra under
	// the name given here, or left out. Nothing is sent until it has been saved once.
	let draft = $state<Record<number, { kind: Kind; name: string }> | null>(null);

	async function load() {
		try {
			view = await api.contribution(job.id);
		} catch {
			view = null;
		}
		if (view?.todo && !draft) edit();
	}

	function edit() {
		if (!view) return;
		const saved = new Map((view.contribution?.extras ?? []).map((x) => [x.title_index, x]));
		const confirmed = view.contribution?.items_confirmed;
		draft = Object.fromEntries(
			view.titles
				.filter((t) => !t.fixed)
				.map((t) => {
					const x = saved.get(t.title_index);
					const kind: Kind = x?.kind ?? (!confirmed && t.suggested ? 'Extra' : '');
					return [t.title_index, { kind, name: x?.name ?? '' }];
				})
		);
	}

	const unnamed = $derived(draft ? Object.values(draft).filter((d) => d.kind && !d.name.trim()).length : 0);

	async function saveExtras() {
		if (!draft || unnamed) return;
		const extras = Object.entries(draft)
			.filter(([, d]) => d.kind)
			.map(([i, d]) => ({ title_index: Number(i), kind: d.kind as ContributionExtra['kind'], name: d.name.trim() }));
		const v = await act.run(() => api.contributionExtras(job.id, extras));
		if (v) {
			view = v;
			draft = null;
		}
	}

	// Again whenever what decides it changes, such as the identity or how the send went.
	$effect(() => {
		job.id;
		job.identity?.tmdb_id;
		job.identity_source;
		job.tv?.mapping?.length;
		job.contribution?.status;
		job.contribution?.release_id;
		load();
	});

	const c = $derived(job.contribution ?? view?.contribution ?? null);
	const status = $derived(c?.status ?? 'not_sent');

	async function send() {
		const v = await act.run(() => api.contribute(job.id));
		if (v) view = v;
	}

	async function release(newRelease: boolean) {
		const v = await act.run(() => api.contributionRelease(job.id, newRelease));
		if (v) view = v;
	}
</script>

{#if view && view.mode !== 'off'}
	<section>
		<h3>TheDiscDB</h3>
		{#if view.known}
			<p class="small">Already in TheDiscDB. It recognised this disc, so there's nothing to add.</p>
		{:else if !view.eligible && status !== 'sending' && view.reason}
			<p class="small muted">Not ready to contribute. {view.reason}.</p>
		{:else}
			<p class="small">
				{#if status === 'sending'}
					Sending…
				{:else if status === 'sent'}
					Sent{c?.sent_at ? ` ${when(c.sent_at)}` : ''}{c?.updated ? ' (replaced the earlier copy)' : ''}. Finish
					the release on TheDiscDB (release date, region, cover) and submit it for review.
				{:else if status === 'failed'}
					<span class="error-text">Not sent. {c?.error ?? 'Unknown error'}</span>
				{:else if view.todo}
					{view.todo}. TheDiscDB lists each title under the name sent with it.
				{:else if view.mode === 'auto' && job.identity_source !== 'user'}
					Not sent automatically, because only discs you confirm go on their own. Check it and send it here.
				{:else}
					Not sent yet. TheDiscDB doesn't know this disc, and sending it helps others' rips recognise it.
				{/if}
			</p>
			{#if view.titles.length && status !== 'sending'}
				{#if draft}
					<p class="small muted">
						The film or episodes go under their own names. Give each extra worth keeping a proper name (such as
						"Behind the Scenes"), and leave out the rest: menus, duplicates, logos and other films' trailers.
					</p>
					<table class="titles">
						<tbody>
							{#each view.titles as t (t.title_index)}
								<tr>
									<td class="what">
										<div>
											<strong>Title {t.title_index}</strong>
											<span class="muted">{duration(t.duration_s)} · {size(t.size_bytes)}</span>
										</div>
										<div class="small muted mono">{t.source ?? ''}{t.chapters ? ` · ${t.chapters} ch` : ''}</div>
									</td>
									<td class="pick">
										{#if t.fixed}
											<span class="small">{t.fixed === 'MainMovie' ? 'Film' : 'Episode'}: <strong>{t.name}</strong></span>
										{:else if draft[t.title_index]}
											<select aria-label="Send title {t.title_index} as" bind:value={draft[t.title_index].kind}>
												{#each kinds as [k, label] (k)}<option value={k}>{label}</option>{/each}
											</select>
											{#if draft[t.title_index].kind}
												<input
													aria-label="Name of title {t.title_index}"
													placeholder="Name on TheDiscDB"
													maxlength="200"
													bind:value={draft[t.title_index].name}
												/>
											{/if}
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
					<div class="row">
						<button class="primary" disabled={act.busy || unnamed > 0} onclick={saveExtras}>Save names</button>
						{#if c?.items_confirmed}
							<button class="link small" onclick={() => (draft = null)}>Cancel</button>
						{/if}
						{#if unnamed}<span class="small muted">{unnamed} extra{unnamed === 1 ? '' : 's'} still need{unnamed === 1 ? 's' : ''} a name</span>{/if}
					</div>
				{:else if c?.items_confirmed}
					<p class="small muted">
						Sent as {view.titles.filter((t) => t.fixed).map((t) => t.name).join(', ')}{#if c.extras.length}, with
							{c.extras.map((x) => `${x.name} (${kindLabel[x.kind].toLowerCase()})`).join(', ')}{:else}, and no extras{/if}.
						<button class="link small" disabled={act.busy} onclick={edit}>Change</button>
					</p>
				{/if}
			{/if}
			{#if c?.release_id}
				<p class="small muted">
					Disc {c.disc_number ?? '?'} of release <span class="mono">{c.release_id.slice(0, 8)}</span>{c.new_release
						? ' (a release of its own)'
						: ''}.
					{#if status !== 'sending'}
						{#if c.new_release}
							<button class="link small" disabled={act.busy} onclick={() => release(false)}
								>Join a recent release of the same title</button
							>
						{:else}
							<button class="link small" disabled={act.busy} onclick={() => release(true)}
								>Start a new release instead</button
							>
						{/if}
					{/if}
				</p>
			{/if}
			<div class="row">
				{#if status === 'sent' && c?.contribute_url}
					<a class="button primary" href={c.contribute_url} target="_blank" rel="noreferrer">Finish on TheDiscDB →</a>
				{/if}
				{#if view.eligible}
					<button class:primary={status !== 'sent'} disabled={act.busy} onclick={send}>
						{status === 'failed' ? 'Retry' : status === 'sent' ? 'Send again' : 'Send to TheDiscDB'}
					</button>
				{/if}
				{#if view.payload}
					<button class="link small" onclick={() => (preview = !preview)}>{preview ? 'Hide' : 'Preview'} what is sent</button>
				{/if}
			</div>
			{#if preview && view.payload}
				<p class="small muted">
					Posted to <span class="mono">{view.base_url}/api/engram/disc</span>, then MakeMKV's title list ({view.scan_log_lines}
					lines, with only the disc, title and stream lines and no drive lines or messages).
				</p>
				<pre class="payload">{JSON.stringify(view.payload, null, 2)}</pre>
			{/if}
		{/if}
		{#if act.error}<p class="small error-text">{act.error}</p>{/if}
	</section>
{/if}

<style>
	section {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	section p {
		margin: 0;
	}
	.titles {
		border-collapse: collapse;
		width: 100%;
	}
	.titles td {
		padding: 6px 4px;
		border-top: 1px solid var(--border);
		vertical-align: top;
	}
	.titles .pick {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		justify-content: flex-end;
	}
	.titles input {
		min-width: 0;
		flex: 1 1 160px;
	}
	.payload {
		margin: 0;
		background: var(--surface-2);
		border-radius: 8px;
		padding: 10px;
		font-size: 0.75rem;
		line-height: 1.4;
		max-height: 320px;
		overflow: auto;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
</style>
