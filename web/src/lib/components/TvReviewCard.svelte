<script lang="ts">
	import type { EpisodeRef, Job, Show, TitleMap, TitleMatch, TitleRole } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import { duration, kindLabel, percent, via } from '../format';
	import { live } from '../live.svelte';
	import { openJob } from '../nav';
	import Evidence from './Evidence.svelte';
	import CoverScans from './CoverScans.svelte';
	import EpisodePicker from './EpisodePicker.svelte';
	import Poster from './Poster.svelte';
	import RefileBanner from './RefileBanner.svelte';
	import Snapshots from './Snapshots.svelte';
	import SplitTimeline from './SplitTimeline.svelte';
	import WebSearch from './WebSearch.svelte';

	let { job }: { job: Job } = $props();
	const act = new Action();

	const plan = $derived(job.tv);
	const progress = $derived(live.progressFor(job));

	// The mapping as the server keeps it: each change made here is saved as it's made
	// (marked as set by hand, so the box's picks leave it alone), and Confirm files it.
	// Split parts sit where the title they're cut from is.
	const rows = $derived(
		[...(plan?.mapping ?? [])].sort(
			(a, b) => (a.part_of ?? a.title_index) - (b.part_of ?? b.title_index) || a.title_index - b.title_index
		)
	);
	const episodeRows = $derived(rows.filter((r) => r.role === 'episode'));
	// The long titles being split are said once, at the top, rather than as rows.
	const otherRows = $derived(rows.filter((r) => r.role !== 'episode' && !splits.some((sp) => sp.title_index === r.title_index)));

	const epKey = (e: EpisodeRef) => `${e.season}:${e.episode}`;
	const code = (e: EpisodeRef) =>
		`S${String(e.season).padStart(2, '0')}E${String(e.episode).padStart(2, '0')}`;
	// Parts of a split title are numbered from 1000 (the next title's from 1100); call them
	// by the title they're from.
	const partNo = (r: TitleMap) => ((r.title_index - 1000) % 100) + 1;
	const rowName = (r: TitleMap) =>
		r.part_of != null ? `Title ${r.part_of}, part ${partNo(r)}` : `Title ${r.title_index}`;
	const rowNumber = (r: TitleMap) => (r.part_of != null ? `${r.part_of}.${partNo(r)}` : `${r.title_index}`);
	const splits = $derived(plan?.splits ?? []);
	const splitOf = (i: number) => splits.find((sp) => sp.title_index === i);
	const byKey = $derived(new Map((plan?.episodes ?? []).map((e) => [epKey(e), e])));
	const same = (a: EpisodeRef[], b: EpisodeRef[]) =>
		JSON.stringify(a.map(epKey).sort()) === JSON.stringify(b.map(epKey).sort());

	// What the cover scan matched to each title.
	const matchFor = $derived(
		new Map((job.scan_match?.matches ?? []).filter((x) => x.kind === 'episode').map((x) => [x.title_index, x]))
	);

	// Episodes on more than one title.
	const clashes = $derived.by(() => {
		const seen = new Map<string, number>();
		for (const r of rows) for (const e of r.episodes) seen.set(epKey(e), (seen.get(epKey(e)) ?? 0) + 1);
		return new Set([...seen].filter(([, n]) => n > 1).map(([k]) => k));
	});
	const holderOf = (k: string, not: number) => rows.find((r) => r.title_index !== not && r.episodes.some((e) => epKey(e) === k));

	// What's wrong with a row, said on the row.
	function problem(r: TitleMap): string | null {
		if (r.role !== 'episode') return null;
		if (!r.episodes.length) return 'Needs an episode';
		const clash = r.episodes.find((e) => clashes.has(epKey(e)));
		if (clash) {
			const other = holderOf(epKey(clash), r.title_index);
			return `${code(clash)} is on ${other ? rowName(other).toLowerCase() : 'another title'} too`;
		}
		return null;
	}
	const unpicked = $derived(episodeRows.filter((r) => !r.episodes.length).length);
	// The server's problems not already said on a row.
	const otherProblems = $derived(
		(plan?.problems ?? []).filter(
			(p) => !/marked as an episode but has none| is on both title |chapters are read once it's ripped/.test(p)
		)
	);
	const problemCount = $derived(episodeRows.filter((r) => problem(r)).length + otherProblems.length);

	// Why a row has its episode, in a few words.
	function reason(r: TitleMap, x: TitleMatch | undefined): string {
		if (r.by_hand) return 'Set by you';
		if (x && x.episodes.length && same(x.episodes, r.episodes))
			return x.confidence === 'high' ? 'Box name and running time match' : 'From the box: worth a check';
		if (plan?.disc_db) return 'From TheDiscDB';
		if (plan?.order_from === 'names') return 'From the run order you pasted';
		if (plan?.order_from === 'season' && plan.order_verified !== true) return "Season order: a guess, check it";
		if (plan?.order_verified) return 'Order checked against the play-all';
		return r.note ?? '';
	}

	const checking = $derived(job.state === 'checking');
	// With the disc out, Confirm works once every title the mapping copies has been ripped.
	const missing = $derived(
		checking
			? rows
					.filter((r) => r.role === 'episode' || r.role === 'extra')
					// A part of a title not split yet needs that title.
					.map((r) => (r.part_of != null && splitOf(r.part_of)?.state !== 'split' ? r.part_of : r.title_index))
					.filter((i, n, all) => all.indexOf(i) === n && !(String(i) in (job.rip.files ?? {})))
			: []
	);
	const canConfirm = $derived(!!plan?.show && !splits.some((sp) => sp.state === 'waiting') && !problemCount && !!episodeRows.length && !act.busy && !missing.length);

	// --- changing titles

	// Save rows changed by hand. An episode another title has moves here, and that title
	// gets this one's old episodes instead (a swap), or none, to pick again.
	async function assign(row: TitleMap, role: TitleRole, eps: EpisodeRef[]) {
		const want = new Set(eps.map(epKey));
		const updates = [{ title_index: row.title_index, role, episodes: role === 'episode' ? eps : [] }];
		for (const other of rows) {
			if (other.title_index === row.title_index || !other.episodes.some((e) => want.has(epKey(e)))) continue;
			let left = other.episodes.filter((e) => !want.has(epKey(e)));
			if (!left.length) left = row.episodes.filter((e) => !want.has(epKey(e)));
			updates.push({ title_index: other.title_index, role: other.role, episodes: left });
		}
		const ok = await act.run(() => api.tvSetMapping(job.id, updates));
		if (ok) picking = null;
	}

	let picking = $state<number | null>(null);
	const pickRow = $derived(picking === null ? null : (rows.find((r) => r.title_index === picking) ?? null));

	// --- show / season controls

	let changingShow = $state(false);
	let showQuery = $state('');
	let found = $state<Show[] | null>(null);
	let season = $state<string>('');
	let disc = $state<string>('');
	let start = $state<string>('');
	let compilation = $state(false);
	let runOrder = $state('');
	let initKey = '';
	$effect(() => {
		const key = `${plan?.show?.key}:${plan?.season}:${plan?.disc_number}:${plan?.start_episode}:${plan?.compilation}`;
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

	// A name from the web search, searched for like a typed one.
	async function useName(name: string) {
		showQuery = name;
		found = (await act.run(() => api.tvSearch(name))) ?? null;
	}

	async function choose(show: Show) {
		found = null;
		const ok = await act.run(() =>
			api.tvPropose(job.id, {
				show_key: show.key,
				show_tmdb_id: null,
				season: compilation ? null : num(season),
				disc_number: num(disc),
				start_episode: null,
				compilation
			})
		);
		if (ok) changingShow = false;
	}

	async function apply() {
		if (!plan?.show) return;
		const ok = await act.run(() =>
			api.tvPropose(job.id, {
				show_key: plan.show!.key,
				show_tmdb_id: null,
				season: compilation ? null : num(season),
				disc_number: num(disc),
				start_episode: compilation ? null : num(start),
				compilation
			})
		);
		if (ok) changingShow = false;
	}

	async function matchText() {
		if (runOrder.trim()) await act.run(() => api.tvMatchText(job.id, runOrder));
	}

	// Eject to check (episode numbers are often printed on the disc). Mid-rip the rip pauses.
	function ejectToCheck() {
		const ripping = job.rip.state === 'running';
		if (ripping && !window.confirm('Eject to check the disc? The rip pauses; titles already ripped are kept, and the rest are ripped when you put the same disc back.'))
			return;
		act.run(() => api.checkDisc(job.id, ripping));
	}

	async function confirm() {
		await act.run(() => api.tvConfirm(job.id, []));
	}

	// --- the cover scan, in one line

	const KIND: Record<string, string> = {
		front: 'Front cover', back: 'Back cover', outside_full: 'Whole outside', inside_full: 'Inside of the cover',
		insert: 'Insert', other: 'Scan'
	};
	const scan = $derived.by(() => {
		const sl = job.listing;
		const l = sl?.edited ?? sl?.found;
		const names = (l?.items ?? []).filter((i) => i.kind === 'episode');
		const scans = job.scans ?? [];
		if (!scans.length && !names.length) return null;
		const ids = [...new Set(names.map((i) => i.scan_id).filter(Boolean))];
		const kinds = [...new Set(scans.filter((s) => !ids.length || ids.includes(s.id)).map((s) => KIND[s.kind]))];
		const what = kinds.length === 1 ? `${kinds[0]} scanned` : `${scans.length} scans`;
		// Titles that have the episode the box gives them.
		const placed = episodeRows.filter((r) => {
			const x = matchFor.get(r.title_index);
			return x && x.episodes.length && x.confidence !== 'low' && same(x.episodes, r.episodes);
		}).length;
		// Episodes that go on parts not cut yet.
		const toCome = splits.filter((sp) => sp.state === 'waiting').reduce((n, sp) => n + sp.expected, 0);
		return { reading: sl?.state === 'reading', progress: sl?.progress, what, names: names.length, placed, toCome };
	});

	// Only the season's order put the episodes on these titles, and nothing checked it.
	const orderGuess = $derived(
		plan?.order_from === 'season' && plan.order_verified == null && episodeRows.length >= 2
	);
	// A long title waits to be ripped before it can be cut; the rip's progress says how long.
	const ripped = $derived(job.rip.state === 'running' && progress ? percent(progress.fraction) : null);
	const titlesText = (xs: number[]) =>
		xs.length === 1 ? `title ${xs[0]}` : `titles ${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`;

	const roleLabel: Record<string, string> = {
		episode: 'Episode',
		extra: 'Extra',
		skip: 'Skipped',
		play_all: 'Play all (skipped)',
		duplicate: 'Duplicate (skipped)'
	};
</script>

<article class="card review" id="job-{job.id}">
	<header class="spread">
		<div class="facts">
			<h2 class="mono label">{job.disc.label ?? 'No volume label'}</h2>
			<div class="small muted">
				TV · {kindLabel(job.disc.kind)}{job.disc.title_count ? ` · ${job.disc.title_count} titles` : ''}
				{#if job.rip.state === 'running' && progress}
					· ripping every title meanwhile, {percent(progress.fraction)} done
				{:else if job.rip.state === 'done'}
					· ripped, waiting for you
				{:else if job.rip.state === 'failed'}
					· <span class="error-text">rip failed</span>
				{/if}
			</div>
		</div>
		<button class="link small" onclick={() => openJob(job.id)}>Details</button>
	</header>

	<RefileBanner {job} />

	{#if plan?.show && !changingShow}
		<section class="show box">
			<Poster url={plan.show.poster_url} alt={plan.show.name} width={44} />
			<div class="grow">
				<h3>{plan.show.name}{plan.show.year ? ` (${plan.show.year})` : ''}</h3>
				<div class="small muted">
					{plan.compilation ? 'Compilation, episodes from anywhere' : `Season ${plan.season}`}{plan.disc_number
						? ` · disc ${plan.disc_number}`
						: ''}{plan.show.providers?.length ? ` · ${via(plan.show.providers)}` : ''}
				</div>
			</div>
			<button class="change" onclick={() => (changingShow = true)}>Change</button>
		</section>
	{:else}
		<section class="box stack tight">
			{#if plan?.show}
				<div class="spread">
					<h3>{plan.show.name}{plan.show.year ? ` (${plan.show.year})` : ''}</h3>
					<button class="link small" onclick={() => (changingShow = false)}>Done</button>
				</div>
				<Evidence items={plan.show.evidence.slice(0, 3)} />
			{:else}
				<p class="small muted">No show picked yet. Search for it below, search the web for the label, or eject the disc to look at it.</p>
				<WebSearch {job} onUse={useName} useLabel="Search shows for" />
			{/if}
			{#if job.media_evidence.length && !job.refile}<p class="small muted">{job.media_evidence.slice(0, 3).join(' · ')}</p>{/if}
			{#if plan?.candidates?.length && plan.candidates.length > 1}
				<p class="small muted">Other shows ({plan.candidates.length - 1})</p>
				<ul class="shows">
					{#each plan.candidates.filter((s) => s.key !== plan.show?.key) as s (s.key)}
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
			{/if}
			<form class="search-row" onsubmit={searchShows}>
				<input placeholder="Search for a show" bind:value={showQuery} />
				<button type="submit" disabled={act.busy || !showQuery.trim()}>Search</button>
			</form>
			{#if found}
				<ul class="shows">
					{#each found as s (s.key)}
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
			{/if}
		</section>
	{/if}

	{#if scan}
		<section class="scan" class:done={scan.names && scan.placed >= episodeRows.length && !unpicked}>
			{#if scan.reading}
				<div class="small" role="status">{scan.progress ?? 'Reading the scans…'}</div>
			{:else}
				<div class="small sub">{scan.what} · {scan.names} name{scan.names === 1 ? '' : 's'} read</div>
				{#if scan.names}
					<div class="headline">
						{#if scan.toCome}
							The box's names go on the titles once {titlesText(splits.filter((sp) => sp.state === 'waiting').map((sp) => sp.title_index))}
							{splits.filter((sp) => sp.state === 'waiting').length === 1 ? 'is' : 'are'} ripped and cut into about
							{scan.toCome} episodes.
						{:else if scan.placed && unpicked}
							{scan.placed} title{scan.placed === 1 ? '' : 's'} placed from the box. {unpicked} need{unpicked === 1 ? 's' : ''} you to pick.
						{:else if scan.placed}
							{scan.placed === episodeRows.length ? `All ${scan.placed}` : scan.placed} title{scan.placed === 1 ? '' : 's'} placed from the box.
						{:else}
							The box's names didn't place any title{unpicked ? '; pick them below' : ''}.
						{/if}
					</div>
					{#if !scan.toCome}<div class="small sub">Matched by name and running time. Nothing is filed until you confirm.</div>{/if}
				{:else}
					<div class="small sub">No episode names read off it. Check “What's on the disc” under Cover scans.</div>
				{/if}
			{/if}
		</section>
	{/if}

	{#if plan?.order_verified === true}
		<span class="badge ok">✓ Order verified against the play-all</span>
	{:else if plan?.order_verified === false}
		<span class="badge bad">✗ {plan.order_note}</span>
	{:else if orderGuess}
		<p class="small warn-note">
			Which {splits.length ? 'part' : 'title'} is which episode is a guess: they're given the season's episodes in
			order{plan?.start_episode ? ` from episode ${plan.start_episode}` : ''}, and discs often hold them in another
			order. Check them, scan the box, or paste its run order under More tools.
		</p>
	{/if}

	{#if plan?.show && !job.refile}
		{#each splits.filter((sp) => sp.state !== 'split') as sp (sp.title_index)}
			<section class="split-card" class:waits={sp.state === 'ready' && !sp.edited}>
				<div class="headline">
					Title {sp.title_index} · <span class="mono">{duration(sp.duration_s)}</span> looks like {sp.expected} episodes in one.
				</div>
				{#if sp.state === 'waiting'}
					<p class="small">
						It's cut into {sp.expected} parts at its chapters once it's ripped{ripped ? ` (the rip is ${ripped} done)` : ''},
						and the box's names then go on the parts in order. Nothing to do until then.
					</p>
				{:else if sp.state === 'ready'}
					<p class="small">
						Cut into {sp.cuts.length + 1} parts{sp.confidence != null && !sp.edited ? `, ${percent(sp.confidence)} sure` : ''}{sp.edited
							? ', at your cuts'
							: ''}. The parts are in the list below{sp.cuts.length + 1 !== sp.expected ? `, though about ${sp.expected} were expected: check the cuts` : ''}.
					</p>
				{:else if sp.state === 'failed'}
					<p class="small error-text">It can't be split: {sp.error}.</p>
				{/if}
				{#if sp.state === 'ready'}
					<details open={!sp.edited && (sp.confidence ?? 0) < 0.8}>
						<summary class="small">Check the cuts</summary>
						<SplitTimeline {job} split={sp} brief />
					</details>
				{:else}
					<button class="link small keep" disabled={act.busy}
						onclick={() => act.run(() => api.tvSplit(job.id, { cuts: null, off: true, cut_at: null, title_index: sp.title_index }))}>
						Keep it as one title
					</button>
				{/if}
			</section>
		{/each}
	{/if}

	{#if plan?.show}
		<div class="list-head">
			<h3>Titles on the disc</h3>
			<span class="small muted">in disc order</span>
		</div>
		<ul class="titles">
			{#each episodeRows as row (row.title_index)}
				{@const x = matchFor.get(row.title_index)}
				{@const p = problem(row)}
				{@const boxDiffers = x && x.episodes.length && row.episodes.length && !same(x.episodes, row.episodes)}
				<li class="title" class:problem={p}>
					<div class="num">
						<span class="n">{rowNumber(row)}</span>
						<span class="t mono">{duration(row.duration_s)}</span>
					</div>
					<div class="body">
						{#if row.episodes.length}
							{#each row.episodes as e (epKey(e))}
								<div class="ep">
									<span class="code">{code(e)}</span>
									<span class="name">{byKey.get(epKey(e))?.name || 'No name'}</span>
								</div>
							{/each}
							{#if reason(row, x)}<div class="small muted why">{reason(row, x)}</div>{/if}
							{#if boxDiffers}
								<div class="small warn-note">
									Box says “{x!.name}”, which is
									<button class="guess" disabled={act.busy} onclick={() => assign(row, 'episode', x!.episodes)}>
										{x!.episodes.map(code).join(' & ')}
									</button>
								</div>
							{/if}
						{:else if x}
							<div class="small warn-note">Box says <strong>“{x.name}”</strong>. {x.guesses.length ? 'Not sure which episode:' : 'No episode with that name.'}</div>
							{#if x.guesses.length}
								<div class="guesses">
									{#each x.guesses as g (epKey(g))}
										{@const holder = holderOf(epKey(g), row.title_index)}
										<button class="guess" disabled={act.busy} title={holder ? `On ${rowName(holder).toLowerCase()} now` : ''}
											onclick={() => assign(row, 'episode', [{ season: g.season, episode: g.episode }])}>
											<span class="code">{code(g)}</span> {g.name}{holder ? ' ⇄' : ''}
										</button>
									{/each}
								</div>
							{/if}
						{:else}
							<div class="small warn-note">{row.note && !/^pick the episode/.test(row.note) ? row.note : 'Pick the episode.'}</div>
						{/if}
						{#if p && row.episodes.length}<div class="small warn-note">{p}</div>{/if}
					</div>
					<button class="edit" aria-label="Change the episode of {rowName(row).toLowerCase()}" title="Change"
						disabled={act.busy} onclick={() => (picking = row.title_index)}>
						<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
							stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4z"></path></svg>
					</button>
				</li>
			{:else}
				<li class="small muted">No title is an episode yet.</li>
			{/each}
		</ul>

		{#if otherRows.length}
			<section class="also box">
				<h4>Also on the disc</h4>
				<ul>
					{#each otherRows as row (row.title_index)}
						<li>
							<span class="small grow">
								{rowName(row)} · <span class="mono">{duration(row.duration_s)}</span> · {roleLabel[row.role]}{row.note ? `: ${row.note}` : ''}
							</span>
							<button class="link small" disabled={act.busy} onclick={() => (picking = row.title_index)}>Change</button>
						</li>
					{/each}
				</ul>
			</section>
		{/if}
	{/if}

	<CoverScans {job} auto={false} />

	<details class="tools">
		<summary class="small">More tools</summary>
		<div class="stack">
			{#if plan?.show}
				<div class="stack tight">
					<label for="run-order-{job.id}" class="small">Paste the run order from the case</label>
					<textarea id="run-order-{job.id}" rows="4" bind:value={runOrder} placeholder="Kite Tram, Puddle Dash, Beard Trim, …"></textarea>
					<div class="row">
						<button onclick={matchText} disabled={act.busy || !runOrder.trim()}>Match episode names</button>
						<span class="small muted">Puts the names on the titles in disc order.</span>
					</div>
				</div>
			{/if}
			{#if !job.refile && !splits.length && (plan?.split_off || plan?.whole?.length)}
				<SplitTimeline {job} />
			{/if}
			<Snapshots {job} />
			{#if !job.refile}
				<div class="row">
					{#if !checking}
						<button disabled={act.busy} title="Open the tray to read the disc (episode numbers are often printed on it)"
							onclick={ejectToCheck}>Eject to check</button>
					{/if}
					<button class="link small" disabled={act.busy} onclick={() => act.run(() => api.setMedia(job.id, 'movie'))}>
						This is a film, not TV
					</button>
				</div>
			{/if}
		</div>
	</details>

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

	{#if otherProblems.length}
		<ul class="problems small">
			{#each otherProblems as p (p)}<li>{p}</li>{/each}
		</ul>
	{/if}
	{#if act.error}<p class="small error-text">{act.error}</p>{/if}

	<div class="bar">
		<div class="small status" class:warn={!canConfirm}>
			{#if !plan?.show}
				Pick the show
			{:else if scan?.toCome}
				Waiting for {titlesText(splits.filter((sp) => sp.state === 'waiting').map((sp) => sp.title_index))} to be ripped
			{:else if unpicked}
				{unpicked} title{unpicked === 1 ? '' : 's'} still need{unpicked === 1 ? 's' : ''} an episode
			{:else if problemCount}
				{problemCount} problem{problemCount === 1 ? '' : 's'} to fix
			{:else if missing.length}
				Put the disc back to rip titles {missing.join(', ')}
			{:else}
				{episodeRows.reduce((n, r) => n + r.episodes.length, 0)} episodes of {plan.show.name}
			{/if}
		</div>
		<button class="primary confirm" disabled={!canConfirm} onclick={confirm}>{job.refile && canConfirm ? 'Move the files' : 'Confirm'}</button>
	</div>
</article>

{#if pickRow && plan}
	<EpisodePicker
		row={pickRow}
		{rows}
		episodes={plan.episodes}
		boxName={matchFor.get(pickRow.title_index)?.name ?? null}
		guesses={matchFor.get(pickRow.title_index)?.guesses ?? []}
		thumb={job.rip.all_titles ? api.thumbUrl(job.id, pickRow.title_index) : null}
		busy={act.busy}
		error={act.error}
		onsave={(role, eps) => assign(pickRow, role, eps)}
		onclose={() => (picking = null)}
	/>
{/if}

<style>
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
	h3 {
		margin: 0;
		font-size: 1rem;
	}
	h4 {
		margin: 0;
		font-size: 0.9rem;
	}
	p {
		margin: 0;
	}
	.box {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface-2);
		padding: 12px;
	}
	.show {
		display: flex;
		gap: 12px;
		align-items: center;
	}
	.change {
		min-height: 44px;
	}
	.grow {
		flex: 1;
		min-width: 0;
	}
	.tight {
		gap: 8px;
	}
	.shows {
		list-style: none;
		margin: 0;
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
	.scan {
		background: var(--info-bg);
		border-radius: var(--radius);
		padding: 12px 14px;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.scan.done {
		background: var(--ok-bg);
	}
	.scan .sub {
		color: var(--info);
	}
	.scan.done .sub {
		color: var(--ok);
	}
	.headline {
		font-weight: 600;
		font-size: 1rem;
		line-height: 1.35;
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
	.warn-note {
		color: var(--warn);
	}
	.split-card {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface-2);
		padding: 12px 14px;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.split-card.waits {
		border-color: var(--accent);
	}
	.split-card .keep {
		align-self: flex-start;
	}
	.split-card summary {
		min-height: 44px;
	}
	.list-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		margin-top: 4px;
	}
	.titles {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.title {
		display: flex;
		gap: 12px;
		align-items: flex-start;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 10px 10px 10px 12px;
	}
	.title.problem {
		border-color: var(--warn);
	}
	.num {
		width: 44px;
		flex: none;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 1px;
		padding-top: 2px;
	}
	.num .n {
		font-size: 1.05rem;
		font-weight: 600;
	}
	.num .t {
		font-size: 0.72rem;
		color: var(--muted);
	}
	.body {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
		padding-top: 2px;
	}
	.ep {
		display: flex;
		gap: 6px;
		align-items: baseline;
		flex-wrap: wrap;
	}
	.code {
		font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
		font-size: 0.75rem;
		background: var(--info-bg);
		color: var(--info);
		border-radius: 4px;
		padding: 1px 5px;
	}
	.name {
		font-weight: 500;
		overflow-wrap: anywhere;
	}
	.guesses {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
	}
	.guess {
		min-height: 36px;
		border-radius: 18px;
		padding: 4px 12px;
		font-size: 0.85rem;
		text-align: left;
	}
	.warn-note .guess {
		min-height: 30px;
		padding: 2px 10px;
		margin-left: 2px;
	}
	.edit {
		width: 44px;
		height: 44px;
		min-height: 44px;
		flex: none;
		padding: 0;
		justify-content: center;
	}
	.also ul {
		list-style: none;
		margin: 6px 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.also li {
		display: flex;
		align-items: center;
		gap: 8px;
		min-height: 36px;
		color: var(--muted);
	}
	.tools > .stack {
		margin-top: 10px;
	}
	summary {
		cursor: pointer;
		color: var(--muted);
		min-height: 36px;
		display: flex;
		align-items: center;
	}
	.tools summary {
		display: list-item;
		line-height: 36px;
	}
	.checking {
		background: var(--warn-bg);
		border-radius: 9px;
		padding: 8px 12px;
	}
	.problems {
		margin: 0;
		padding-left: 18px;
		color: var(--warn);
	}
	.bar {
		display: flex;
		align-items: center;
		gap: 12px;
		padding-top: 12px;
		border-top: 1px solid var(--border);
	}
	.bar .status {
		flex: 1;
		min-width: 0;
		color: var(--muted);
	}
	.bar .status.warn {
		color: var(--warn);
	}
	.confirm {
		min-height: 48px;
		padding: 0 22px;
		font-size: 1rem;
		justify-content: center;
	}
	/* On a phone the bar stays in sight above the tabs while the card is on screen. */
	@media (max-width: 759px) {
		.bar {
			position: sticky;
			bottom: calc(56px + env(safe-area-inset-bottom));
			z-index: 5;
			margin: 0 -14px -14px;
			padding: 12px 14px;
			background: var(--surface);
			border-radius: 0 0 var(--radius) var(--radius);
		}
	}
</style>
