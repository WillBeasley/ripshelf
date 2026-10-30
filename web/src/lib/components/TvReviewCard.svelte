<script lang="ts">
	import type { Episode, EpisodeRef, Job, Show, TitleMap, TitleRole } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { duration, kindLabel, percent, via } from '../format';
	import { live } from '../live.svelte';
	import { openJob } from '../nav';
	import Evidence from './Evidence.svelte';
	import Poster from './Poster.svelte';

	let { job }: { job: Job } = $props();
	const act = new Action();

	const plan = $derived(job.tv);
	const progress = $derived(live.progressFor(job));

	// Editable copy of the server's mapping; reset whenever the server's changes.
	let rows = $state<TitleMap[]>([]);
	let serverKey = '';
	$effect(() => {
		const key = JSON.stringify(plan?.mapping ?? []);
		if (key !== serverKey) {
			serverKey = key;
			rows = JSON.parse(key); // a plain copy (structuredClone rejects reactive proxies)
		}
	});

	const epKey = (e: EpisodeRef) => `${e.season}:${e.episode}`;
	const code = (e: EpisodeRef) =>
		`S${String(e.season).padStart(2, '0')}E${String(e.episode).padStart(2, '0')}`;
	const byKey = $derived(new Map((plan?.episodes ?? []).map((e) => [epKey(e), e])));
	const seasons = $derived.by(() => {
		const out = new Map<number, Episode[]>();
		for (const e of plan?.episodes ?? []) {
			if (!out.has(e.season)) out.set(e.season, []);
			out.get(e.season)!.push(e);
		}
		return [...out.entries()].sort(([a], [b]) => (a === 0 ? 1 : b === 0 ? -1 : a - b));
	});

	// Episodes used by more than one row.
	const clashes = $derived.by(() => {
		const seen = new Map<string, number>();
		for (const r of rows) for (const e of r.episodes) seen.set(epKey(e), (seen.get(epKey(e)) ?? 0) + 1);
		return new Set([...seen].filter(([, n]) => n > 1).map(([k]) => k));
	});
	const localProblems = $derived.by(() => {
		const out: string[] = [];
		if (!plan?.show) out.push('Pick the show');
		for (const r of rows)
			if (r.role === 'episode' && !r.episodes.length) out.push(`Title ${r.title_index} needs an episode`);
		if (clashes.size) out.push('An episode is on two titles');
		if (!rows.some((r) => r.role === 'episode')) out.push('No title is mapped to an episode');
		return out;
	});
	const dirty = $derived(JSON.stringify(rows) !== serverKey);
	const serverProblems = $derived(dirty ? [] : (plan?.problems ?? []));
	const checking = $derived(job.state === 'checking');
	// With the disc out, Confirm works once every title the mapping copies has been ripped.
	const missing = $derived(
		checking
			? rows
					.filter((r) => (r.role === 'episode' || r.role === 'extra') && !(String(r.title_index) in (job.rip.files ?? {})))
					.map((r) => r.title_index)
			: []
	);
	const canConfirm = $derived(!localProblems.length && !serverProblems.length && !act.busy && !missing.length);

	// Eject to check (episode numbers are often printed on the disc). Mid-rip the rip pauses.
	function ejectToCheck() {
		const ripping = job.rip.state === 'running';
		if (ripping && !window.confirm('Eject to check the disc? The rip pauses; titles already ripped are kept, and the rest are ripped when you put the same disc back.'))
			return;
		act.run(() => api.checkDisc(job.id, ripping));
	}

	function setEpisode(row: TitleMap, value: string) {
		if (!value) {
			row.episodes = [];
			return;
		}
		const [s, n] = value.split(':').map(Number);
		const want = { season: s, episode: n };
		// Swap, so whichever row held this episode gets this row's old episodes.
		const holder = rows.find((r) => r !== row && r.episodes.some((e) => epKey(e) === value));
		if (holder) {
			holder.episodes = row.episodes.filter((e) => epKey(e) !== value);
			if (!holder.episodes.length && holder.role === 'episode') holder.note = 'swapped, so pick an episode';
		}
		const twoPart = row.episodes.length === 2;
		row.episodes = twoPart ? [want, { season: s, episode: n + 1 }] : [want];
		row.role = 'episode';
	}

	function setRole(row: TitleMap, role: TitleRole) {
		row.role = role;
		if (role !== 'episode') row.episodes = [];
	}

	function toggleTwoPart(row: TitleMap, on: boolean) {
		const first = row.episodes[0];
		if (!first) return;
		row.episodes = on ? [first, { season: first.season, episode: first.episode + 1 }] : [first];
	}

	// --- show / season controls
	let showQuery = $state('');
	let found = $state<Show[] | null>(null);
	let season = $state<string>('');
	let disc = $state<string>('');
	let start = $state<string>('');
	let compilation = $state(false);
	let runOrder = $state('');
	let initKey = '';
	$effect(() => {
		const key = `${plan?.show?.tmdb_id}:${plan?.season}:${plan?.disc_number}:${plan?.start_episode}:${plan?.compilation}`;
		if (key !== initKey) {
			initKey = key;
			season = plan?.season != null ? String(plan.season) : '';
			disc = plan?.disc_number != null ? String(plan.disc_number) : '';
			start = plan?.start_episode != null ? String(plan.start_episode) : '';
			compilation = plan?.compilation ?? false;
		}
	});
	const num = (s: string) => (/^\d+$/.test(s.trim()) ? Number(s.trim()) : null);

	async function searchShows(e: SubmitEvent) {
		e.preventDefault();
		found = (await act.run(() => api.tvSearch(showQuery.trim()))) ?? null;
	}

	async function choose(show: Show) {
		found = null;
		await act.run(() =>
			api.tvPropose(job.id, {
				show_tmdb_id: show.tmdb_id,
				season: compilation ? null : num(season),
				disc_number: num(disc),
				start_episode: null,
				compilation
			})
		);
	}

	async function apply() {
		if (!plan?.show) return;
		await act.run(() =>
			api.tvPropose(job.id, {
				show_tmdb_id: plan.show!.tmdb_id,
				season: compilation ? null : num(season),
				disc_number: num(disc),
				start_episode: compilation ? null : num(start),
				compilation
			})
		);
	}

	async function matchText() {
		if (runOrder.trim()) await act.run(() => api.tvMatchText(job.id, runOrder));
	}

	async function confirm() {
		await act.run(() =>
			api.tvConfirm(
				job.id,
				rows.map((r) => ({ title_index: r.title_index, role: r.role, episodes: r.episodes }))
			)
		);
	}

	const skippedRoles = new Set(['play_all', 'duplicate']);
	const roleLabel: Record<string, string> = {
		episode: 'Episode',
		extra: 'Extra',
		skip: 'Skip',
		play_all: 'Play all',
		duplicate: 'Duplicate'
	};
	let thumbFailed = $state<Record<number, boolean>>({});
</script>

<article class="card review">
	<header class="spread">
		<div class="facts">
			<h2 class="mono label">{job.disc.label ?? 'No volume label'}</h2>
			<div class="small muted">
				TV · {kindLabel(job.disc.kind)}{job.disc.title_count ? ` · ${job.disc.title_count} titles` : ''}
			</div>
			<div class="small muted">
				{#if job.rip.state === 'running' && progress}
					Ripping every title meanwhile, {percent(progress.fraction)} done
				{:else if job.rip.state === 'done'}
					Ripped, waiting for you
				{:else if job.rip.state === 'failed'}
					<span class="error-text">Rip failed</span>
				{/if}
			</div>
		</div>
		<button class="link small" onclick={() => openJob(job.id)}>Details</button>
	</header>

	{#if job.media_evidence.length}
		<p class="small muted">{job.media_evidence.slice(0, 3).join(' · ')}</p>
	{/if}

	{#if plan?.show}
		<section class="show">
			<Poster url={plan.show.poster_url} alt={plan.show.name} width={56} />
			<div class="grow">
				<h3>{plan.show.name}{plan.show.year ? ` (${plan.show.year})` : ''}</h3>
				<div class="small muted">
					{plan.compilation ? 'Compilation, with episodes from anywhere in the show' : `Season ${plan.season}`}{plan.disc_number
						? ` · disc ${plan.disc_number}`
						: ''}{plan.show.episode_runtime ? ` · episodes ~${plan.show.episode_runtime} min` : ''}{plan.show.providers?.length
						? ` · ${via(plan.show.providers)}`
						: ''}
				</div>
				<Evidence items={plan.show.evidence.slice(0, 3)} />
			</div>
		</section>
	{:else}
		<p class="small muted">No show picked yet. Search for it below.</p>
	{/if}

	{#if plan?.candidates?.length && plan.candidates.length > 1}
		<div>
			<p class="small muted">Other shows ({plan.candidates.length - 1})</p>
			<ul class="shows">
				{#each plan.candidates.filter((s) => s.tmdb_id !== plan.show?.tmdb_id) as s (s.tmdb_id)}
					<li>
						<button class="other" onclick={() => choose(s)} disabled={act.busy}>
							<Poster url={s.poster_url} alt={s.name} width={32} />
							<span class="grow">
								{s.name}{s.year ? ` (${s.year})` : ''}
								{#if s.providers?.length}<span class="muted small">{` · ${via(s.providers)}`}</span>{/if}
								{#if s.excluded}<span class="small error-text excl">{s.evidence[0]?.text}</span>{/if}
							</span>
							<span class="small muted">{s.score.toFixed(2)}</span>
						</button>
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	<form class="search-row" onsubmit={searchShows}>
		<input placeholder="Search TMDB for a show" bind:value={showQuery} />
		<button type="submit" disabled={act.busy || !showQuery.trim()}>Search</button>
	</form>
	{#if found}
		<ul class="shows">
			{#each found as s (s.tmdb_id)}
				<li>
					<button class="other" onclick={() => choose(s)} disabled={act.busy}>
						<Poster url={s.poster_url} alt={s.name} width={32} />
						<span class="grow">{s.name}{s.year ? ` (${s.year})` : ''}</span>
					</button>
				</li>
			{:else}
				<li class="small muted">No shows found.</li>
			{/each}
		</ul>
	{/if}

	{#if plan?.show}
		<div class="controls">
			<label class="check"><input type="checkbox" bind:checked={compilation} /> Compilation</label>
			{#if !compilation}
				<label class="field tiny">Season <input bind:value={season} inputmode="numeric" /></label>
				<label class="field tiny">Disc <input bind:value={disc} inputmode="numeric" /></label>
				<label class="field tiny">From ep. <input bind:value={start} inputmode="numeric" /></label>
			{/if}
			<button onclick={apply} disabled={act.busy}>Apply</button>
		</div>
		<details>
			<summary class="small">Paste the run order from the case</summary>
			<div class="stack tight">
				<textarea rows="4" bind:value={runOrder} placeholder="Brolly Bus, Rainbow Race, Hair Cut, …"></textarea>
				<button onclick={matchText} disabled={act.busy || !runOrder.trim()}>Match episode names</button>
			</div>
		</details>
	{/if}

	{#if plan?.order_verified === true}
		<span class="badge ok">✓ Order verified against the play-all</span>
	{:else if plan?.order_verified === false}
		<span class="badge bad">✗ {plan.order_note}</span>
	{/if}

	<table class="map">
		<tbody>
			{#each rows as row (row.title_index)}
				{@const skipped = skippedRoles.has(row.role)}
				{@const clash = row.episodes.some((e) => clashes.has(epKey(e)))}
				<tr class:skipped class:clash>
					<td class="thumb">
						{#if job.rip.all_titles && !thumbFailed[row.title_index]}
							<img
								src={api.thumbUrl(job.id, row.title_index)}
								alt=""
								loading="lazy"
								onerror={() => (thumbFailed[row.title_index] = true)}
							/>
						{:else}
							<div class="nothumb"></div>
						{/if}
					</td>
					<td class="what">
						<div><strong>Title {row.title_index}</strong> <span class="muted">{duration(row.duration_s)}</span></div>
						{#if skipped}
							<div class="small muted">{roleLabel[row.role]}: skipped{row.note ? ` (${row.note})` : ''}</div>
						{:else}
							<div class="pick">
								<select
									aria-label="Role of title {row.title_index}"
									value={row.role}
									onchange={(e) => setRole(row, e.currentTarget.value as TitleRole)}
								>
									<option value="episode">Episode</option>
									<option value="extra">Extra</option>
									<option value="skip">Skip</option>
								</select>
								{#if row.role === 'episode'}
									<select
										aria-label="Episode for title {row.title_index}"
										value={row.episodes[0] ? epKey(row.episodes[0]) : ''}
										onchange={(e) => setEpisode(row, e.currentTarget.value)}
									>
										<option value="">Pick an episode</option>
										{#each seasons as [s, eps] (s)}
											<optgroup label={s === 0 ? 'Specials' : `Season ${s}`}>
												{#each eps as e (epKey(e))}
													<option value={epKey(e)}>
														{code(e)} · {e.name}{e.runtime ? ` · ${e.runtime} min` : ''}
													</option>
												{/each}
											</optgroup>
										{/each}
									</select>
								{/if}
							</div>
							{#if row.role === 'episode' && row.episodes[0]}
								<label class="check small">
									<input
										type="checkbox"
										checked={row.episodes.length === 2}
										onchange={(e) => toggleTwoPart(row, e.currentTarget.checked)}
									/>
									Two-part ({row.episodes.length === 2
										? `${code(row.episodes[0])}–E${String(row.episodes[1].episode).padStart(2, '0')}`
										: '+ next episode'})
								</label>
							{/if}
							{#if row.note}<div class="small muted">{row.note}</div>{/if}
							{#if clash}<div class="small error-text">This episode is on another title too</div>{/if}
						{/if}
					</td>
				</tr>
			{:else}
				<tr><td class="small muted">No titles.</td></tr>
			{/each}
		</tbody>
	</table>

	{#if localProblems.length || serverProblems.length}
		<ul class="problems small">
			{#each [...new Set([...localProblems, ...serverProblems])] as p (p)}<li>{p}</li>{/each}
		</ul>
	{/if}

	{#if checking}
		<p class="checking small">
			Ejected to check. Put the same disc back to carry on. Your changes here are kept.
			{#if missing.length}
				Titles {missing.join(', ')} aren't ripped yet, so Confirm needs the disc back.
			{:else}
				Every title this mapping needs is ripped, so you can confirm without the disc.
			{/if}
		</p>
	{/if}

	<div class="actions">
		{#if !checking}
			<button disabled={act.busy} title="Open the tray to read the disc (episode numbers are often printed on it)"
				onclick={ejectToCheck}>Eject to check</button>
		{/if}
		<button class="primary confirm" disabled={!canConfirm} onclick={confirm}>
			Confirm {rows.filter((r) => r.role === 'episode').length} episodes{plan?.show ? ` of ${plan.show.name}` : ''}
		</button>
		<button class="link small" disabled={act.busy} onclick={() => act.run(() => api.setMedia(job.id, 'movie'))}>
			This is a film, not TV
		</button>
	</div>
	{#if act.error}<p class="small error-text">{act.error}</p>{/if}
</article>

<style>
	.checking {
		background: var(--warn-bg);
		border-radius: 9px;
		padding: 8px 12px;
		margin: 0;
	}
	.review {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.facts {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.label {
		font-size: 1rem;
	}
	.show {
		display: flex;
		gap: 12px;
		align-items: flex-start;
	}
	.grow {
		flex: 1;
		min-width: 0;
	}
	.shows {
		list-style: none;
		margin: 6px 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.other {
		width: 100%;
		text-align: left;
		background: none;
	}
	.excl {
		display: block;
	}
	.search-row,
	.controls {
		display: flex;
		gap: 8px;
		align-items: flex-end;
		flex-wrap: wrap;
	}
	.search-row input {
		flex: 1;
		min-width: 0;
	}
	.tiny {
		width: 5.2em;
	}
	.tight {
		gap: 6px;
		margin-top: 6px;
	}
	.badge {
		font-size: 0.82rem;
		font-weight: 600;
		padding: 4px 10px;
		border-radius: 999px;
		align-self: flex-start;
	}
	.badge.ok {
		background: var(--ok-bg);
		color: var(--ok);
	}
	.badge.bad {
		background: var(--bad-bg);
		color: var(--bad);
	}
	.map {
		width: 100%;
		border-collapse: collapse;
	}
	.map td {
		padding: 6px 4px;
		border-top: 1px solid var(--border);
		vertical-align: top;
	}
	.map tr.skipped td {
		opacity: 0.55;
	}
	.map tr.clash td {
		background: var(--bad-bg);
	}
	.thumb {
		width: 84px;
	}
	.thumb img,
	.nothumb {
		width: 80px;
		aspect-ratio: 16 / 9;
		object-fit: cover;
		border-radius: 4px;
		background: var(--surface-2);
		display: block;
	}
	.what {
		min-width: 0;
	}
	.pick {
		display: flex;
		gap: 6px;
		margin-top: 4px;
		flex-wrap: wrap;
	}
	.pick select:first-child {
		width: auto;
		flex: none;
	}
	.pick select:last-child {
		flex: 1;
		min-width: 0;
	}
	.problems {
		margin: 0;
		padding-left: 18px;
		color: var(--warn);
	}
	.actions {
		display: flex;
		flex-direction: column;
		gap: 8px;
		align-items: center;
	}
	.confirm {
		width: 100%;
		justify-content: center;
		min-height: 46px;
		font-size: 1rem;
	}
	summary {
		cursor: pointer;
		color: var(--muted);
	}
	p {
		margin: 0;
	}
</style>
