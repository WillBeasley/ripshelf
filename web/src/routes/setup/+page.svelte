<script lang="ts">
	// First-start setup, the basics, one step at a time. Each step is saved when you move on,
	// so leaving half-way loses nothing. The fields are Settings' own (lib/settings), so
	// they read and save the same way there.
	import type { SettingsUpdate, SetupTools } from '#lib/api-types.ts';
	import { goto } from '$app/navigation';
	import { api } from '#lib/api.ts';
	import { Action } from '#lib/actions.svelte.ts';
	import { live } from '#lib/live.svelte.ts';
	import TestButton from '#lib/components/TestButton.svelte';
	import FolderStatus from '#lib/components/FolderStatus.svelte';
	import Command from '#lib/components/Command.svelte';
	import SetupProblems from '#lib/components/SetupProblems.svelte';
	import { SettingsForm, type SecretName } from '#lib/settings/form.svelte.ts';
	import DriveList from '#lib/settings/DriveList.svelte';
	import Field from '#lib/settings/Field.svelte';
	import MediaServer from '#lib/settings/sections/MediaServer.svelte';

	const steps = ['Drives', 'MakeMKV', 'Folders', 'Film and TV data', 'Extras (optional)'] as const;
	// What each step saves.
	const STEP_FIELDS: string[][] = [
		['disabled_drives'],
		['makemkv_key'],
		['library_dir', 'scratch_dir'],
		['tmdb_api_key', 'tvdb_api_key', 'omdb_api_key', 'metadata_providers'],
		['transcode_enabled', 'transcode_codecs', 'transcode_crop', 'transcode_hdr', 'transcode_encoder', 'transcode_quality',
			'transcode_keep_original', 'emby_url', 'emby_api_key', 'emby_movies_library', 'emby_movies_path', 'emby_tv_library',
			'emby_tv_path', 'pushover_user_key', 'pushover_app_token']
	];
	let step = $state(0);
	let saved = $state<number[]>([]); // steps saved (a ✓), not just passed

	const form = new SettingsForm();
	const act = form.act;
	const importAct = new Action();
	let tools = $state<SetupTools | null>(null);
	let importNote = $state<string | null>(null);
	let fileInput = $state<HTMLInputElement | null>(null);

	$effect(() => {
		form.load();
		api.setupTools().then((t) => (tools = t)).catch(() => {});
	});
	const f = $derived(form.f);

	async function save(i: number): Promise<boolean> {
		if (!(await form.save(STEP_FIELDS[i]))) return false;
		if (i === 1) tools = (await api.setupTools().catch(() => null)) ?? tools;
		return true;
	}

	// Without TMDB films are identified only from TheDiscDB, so that step asks for a key (or "Skip for now").
	const needsTmdb = $derived(step === 3 && form.ready && !form.isSet('tmdb_api_key') && !form.secrets.tmdb_api_key?.trim());

	async function next() {
		if (needsTmdb) {
			act.error = 'Enter a TMDB key first (or skip for now)';
			return;
		}
		if (!(await save(step))) return;
		if (!saved.includes(step)) saved = [...saved, step];
		if (step < steps.length - 1) step += 1;
		else await finish();
	}

	async function finish() {
		const v = await act.run(() => api.saveSettings({ setup_complete: true } as SettingsUpdate));
		if (v) {
			live.setupComplete = true;
			goto('/');
		}
	}

	const uhdDrives = $derived(live.drives.filter((d) => d.capabilities.includes('uhd')));
	const needsData = $derived(uhdDrives.filter((d) => !form.view?.makemkv_drive_data[d.id]));

	async function importData(e: SubmitEvent) {
		e.preventDefault();
		importNote = null;
		const file = fileInput?.files?.[0];
		if (!file) {
			importAct.error = 'Choose a _private_data.tar first';
			return;
		}
		const data = new FormData();
		data.set('drive_ids', 'all');
		data.set('file', file);
		const r = await importAct.run(() => api.importPrivateData(data));
		if (r) {
			importNote = `Imported for ${r.drive_ids.length} drive(s).`;
			await form.refresh();
		}
	}

	// Metadata providers, shown in the order they're asked (best first), each switched on
	// or off in every list it's in. Reordering is in Settings.
	const ORDER = ['thediscdb', 'tmdb', 'tvdb', 'tvmaze', 'omdb'] as const;
	const WHY: Record<string, string> = {
		thediscdb: 'Recognises the exact disc, its edition, and which title is the film or which episodes are on it. No key.',
		tmdb: 'Names films and shows, with posters and episode lists. Films need it unless TheDiscDB knows the disc.',
		tvdb: 'Recommended for TV. Finds shows TMDB misses, and has the DVD episode order.',
		tvmaze: 'TV shows and episode lists. No key, so TV works even without TMDB.',
		omdb: 'A second opinion on films (IMDb data). Off unless you want it.'
	};
	const KEY_FIELD: Record<string, SecretName> = { tmdb: 'tmdb_api_key', tvdb: 'tvdb_api_key', omdb: 'omdb_api_key' };
	const providers = $derived(ORDER.map((n) => form.view?.providers.find((p) => p.name === n)).filter((p) => p != null));
	const lists = $derived(f.metadata_providers);
	const providerOn = (name: string) => Object.values(lists).some((l) => l.some((r) => r.name === name && r.enabled));
	function setProvider(name: string, on: boolean) {
		const set = (l: typeof lists.movie) => l.map((r) => (r.name === name ? { ...r, enabled: on } : r));
		f.metadata_providers = { movie: set(lists.movie), tv: set(lists.tv), episodes: set(lists.episodes) };
	}

	// Transcoding as one choice: off, crop only (re-encodes just the letterboxed rips), or
	// transcode the codecs listed. It sets Settings' transcoding fields.
	type Mode = 'off' | 'crop' | 'transcode';
	const mode = $derived<Mode>(
		!f.transcode_enabled ? 'off' : !f.transcode_codecs.length && f.transcode_crop === 'always' ? 'crop' : 'transcode'
	);
	function setMode(m: Mode) {
		f.transcode_enabled = m !== 'off';
		if (m === 'crop') {
			f.transcode_codecs = [];
			f.transcode_crop = 'always';
		} else if (m === 'transcode') {
			if (!f.transcode_codecs.length) f.transcode_codecs = ['vc1'];
			if (f.transcode_crop === 'always') f.transcode_crop = 'when_transcoding';
		}
	}
</script>

<svelte:head><title>Setup · ripshelf</title></svelte:head>

<div class="stack setup">
	<div class="spread">
		<h1>Set up ripshelf</h1>
		<button type="button" class="link" onclick={finish} disabled={act.busy}>Skip setup</button>
	</div>
	<p class="muted">
		Each step is saved when you move on, and everything here can be changed later in
		<a href="/settings">Settings</a>.
	</p>

	<ol class="steps" aria-label="Steps">
		{#each steps as s, i (s)}
			<li class:current={i === step} class:done={saved.includes(i)}>
				<button type="button" class="link" onclick={() => (step = i)}>{i + 1}. {s}</button>
			</li>
		{/each}
	</ol>

	{#if !form.ready}
		<p class="muted">{act.error ?? 'Loading…'}</p>
	{:else}
		<section class="card stack">
			{#if step === 0}
				<h2>Drives</h2>
				{#if !live.drives.length}
					<p>No optical drives found. Plug one in (or check it's switched on), then <button type="button" class="link" onclick={() => api.redetect().then((d) => (live.drives = d))}>look again</button>.</p>
				{:else}
					<p class="muted small">Give each drive a name you'll recognise (e.g. "Top bay"), and untick any ripshelf should leave alone.</p>
					<DriveList {form} modes={false} />
				{/if}
			{:else if step === 1}
				<h2>MakeMKV</h2>
				{#if tools}
					{#if tools.makemkv_eula_pending}
						<div class="notice bad">
							<p>MakeMKV is proprietary software with its own licence, and it isn't accepted yet, so MakeMKV isn't run and discs wait.
								<a href="/api/setup/makemkv/eula" target="_blank" rel="noopener">Read the MakeMKV EULA</a>. If you accept it, set this on the container (and on any worker containers), then restart it. In compose.yaml it goes under <span class="mono">environment:</span></p>
							<Command text={'RIPSHELF_ACCEPT_MAKEMKV_EULA: "yes"'} />
							<p class="small muted">In an env file, or with <span class="mono">docker run -e</span>, it's written like this:</p>
							<Command text="RIPSHELF_ACCEPT_MAKEMKV_EULA=yes" />
						</div>
					{/if}
					{#if tools.makemkv.found}
						<p class="ok">✓ MakeMKV found at <span class="mono small">{tools.makemkv.path}</span></p>
					{:else}
						<div class="notice bad">
							<p>MakeMKV isn't installed (or isn't where ripshelf looks).
								{#if tools.windows}Install it, then come back to this step:{:else}Install it, or set <span class="mono">RIPSHELF_MAKEMKVCON</span> to its path.{/if}</p>
							{#if tools.windows}<Command text="winget install -e --id GuinpinSoft.MakeMKV" />{/if}
						</div>
					{/if}
					<p class="small">
						{#if form.isSet('makemkv_key')}✓ ripshelf has a MakeMKV key, and gives it to MakeMKV.
						{:else if tools.makemkv_registered}✓ MakeMKV is already registered (in the MakeMKV app). A key here replaces that one.
						{:else}No MakeMKV key yet. Without one, MakeMKV runs as a trial.{/if}
					</p>
				{/if}
				<Field {form} name="makemkv_key" />
				<TestButton check="makemkv" values={form.body} label="Check the key" />
				<p class="small muted">
					MakeMKV is paid software; the free beta key is a courtesy while it's in beta. If ripshelf is useful to
					you, please <a href="https://www.makemkv.com/buy/" target="_blank" rel="noreferrer">buy a licence</a>.
					The current beta key is on the <a href="https://forum.makemkv.com/forum/viewtopic.php?t=1053" target="_blank" rel="noreferrer">MakeMKV forum</a>.
				</p>
				{#if uhdDrives.length}
					<h3>UHD drives</h3>
					{#if needsData.length}
						<p class="small">
							UHD discs need LibreDrive data (MakeMKV's <span class="mono">_private_data.tar</span>) for each UHD drive:
							{needsData.map((d) => d.display_name).join(', ')}.
							{#if tools?.windows}If you've already opened a UHD disc in the MakeMKV app, ripshelf uses that one by itself.{/if}
						</p>
						<form class="row" onsubmit={importData}>
							<input type="file" accept=".tar" bind:this={fileInput} />
							<button type="submit" disabled={importAct.busy}>{importAct.busy ? 'Importing…' : 'Import'}</button>
						</form>
						{#if importAct.error}<p class="small error-text">{importAct.error}</p>{/if}
					{:else}
						<p class="small ok">✓ Every UHD drive has LibreDrive data.</p>
					{/if}
					{#if importNote}<p class="small ok">{importNote}</p>{/if}
				{/if}
			{:else if step === 2}
				<h2>Folders</h2>
				<p class="muted small">Full paths, as this machine sees them. A network share works for the library. Use its full path, not a mapped drive letter.</p>
				<Field {form} name="library_dir"><FolderStatus path={f.library_dir} /></Field>
				<Field {form} name="scratch_dir"><FolderStatus path={f.scratch_dir} /></Field>
				<TestButton check="paths" values={form.body} label="Check the folders are writable" />
			{:else if step === 3}
				<h2>Film and TV data</h2>
				<p class="small">
					ripshelf looks each disc up with these, in this order. TheDiscDB comes first, since it knows the disc itself, then the
					searches. TMDB is the one you want: without it, films are identified only when TheDiscDB knows the disc, without
					posters. TV shows still work without it, through TVmaze (no key) or TheTVDB.
				</p>
				{#each providers as p (p.name)}
					{@const keyField = KEY_FIELD[p.name]}
					<div class="provider">
						<label class="check">
							<input type="checkbox" aria-label="Use {p.label}" checked={p.name === 'tmdb' || providerOn(p.name)} disabled={p.name === 'tmdb'}
								onchange={(e) => setProvider(p.name, e.currentTarget.checked)} />
							<strong>{p.label}</strong>
							{#if p.name === 'tmdb'}<span class="chip">needed for films</span>{:else if p.name === 'tvdb'}<span class="chip">recommended for TV</span>{/if}
						</label>
						<p class="small muted">{WHY[p.name] ?? p.description}</p>
						{#if keyField}
							<Field {form} name={keyField} />
							<div class="row">
								{#if p.check}<TestButton check={p.check} values={form.body} />{/if}
								{#if p.site}<a class="small" href={p.site} target="_blank" rel="noreferrer">Get a key →</a>{/if}
							</div>
						{/if}
					</div>
				{/each}
				<p class="small muted">To change the order, or use a provider for films but not TV, see <a href="/settings/identify">Settings → Identify</a>.</p>
			{:else}
				<h2>Extras <span class="chip">optional</span></h2>
				<p class="muted small">These are all optional, so skip them now and set them up in Settings when you want.</p>
				<h3>Transcoding</h3>
				<div class="choices" role="radiogroup" aria-label="Transcoding">
					{#each [['off', 'Off (copy rips as they are)'], ['crop', 'Crop black bars only (re-encodes just the letterboxed rips)'], ['transcode', 'Transcode to H.265']] as [m, label] (m)}
						<label class="check"><input type="radio" name="transcode" checked={mode === m} onchange={() => setMode(m as Mode)} /> {label}</label>
					{/each}
				</div>
				{#if mode === 'transcode'}
					<Field {form} name="transcode_codecs" />
					<label class="check">
						<input type="checkbox" checked={f.transcode_crop !== 'off'}
							onchange={(e) => (f.transcode_crop = e.currentTarget.checked ? 'when_transcoding' : 'off')} /> Crop black bars while transcoding
					</label>
				{/if}
				{#if mode !== 'off'}
					<div class="two">
						<Field {form} name="transcode_encoder" />
						<Field {form} name="transcode_quality" />
					</div>
					<Field {form} name="transcode_hdr" />
					<Field {form} name="transcode_keep_original" />
				{/if}
				{#if tools && !(tools.ffmpeg.found && tools.ffprobe.found)}
					<div class="notice">
						<p>ffmpeg isn't installed, so rips are copied as they are{mode !== 'off' ? ' until it is' : ''}.{tools.windows ? ' Install it:' : ''}</p>
						{#if tools.windows}<Command text="winget install -e --id Gyan.FFmpeg" />{/if}
					</div>
				{/if}
				{#if tools?.mkvmerge && !tools.mkvmerge.found}
					<div class="notice">
						<p>mkvmerge (MKVToolNix) isn't installed, so a TV disc with every episode in one long title can't be split into episodes.{tools.windows ? ' Install it:' : ''}</p>
						{#if tools.windows}<Command text="winget install -e --id MoritzBunkus.MKVToolNix" />{/if}
					</div>
				{/if}
				{#if tools && !tools.java.found}
					<div class="notice">
						<p>No Java found. MakeMKV needs it for some Blu-ray menus (BD-J).{tools.windows ? ' Install it:' : ''}</p>
						{#if tools.windows}<Command text="winget install -e --id Microsoft.OpenJDK.21" />{/if}
					</div>
				{/if}
				{#if tools?.tesseract && !tools.tesseract.found}
					<div class="notice">
						<p>Tesseract isn't installed, so the text on cover scans isn't read (the scans are still kept).{tools.windows ? ' Install it:' : ''}</p>
						{#if tools.windows}<Command text="winget install -e --id UB-Mannheim.TesseractOCR" />{/if}
					</div>
				{/if}
				{#if tools?.zbar && !tools.zbar.found}
					<div class="notice">
						{#if tools.windows}
							<p>zbar can't read barcodes on cover scans until the Visual C++ 2013 runtime it needs is installed:</p>
							<Command text="winget install -e --id Microsoft.VCRedist.2013.x64" />
						{:else}
							<p>zbar isn't installed, so barcodes on cover scans are read from their printed digits only.</p>
						{/if}
					</div>
				{/if}
				<h3>Emby</h3>
				<MediaServer {form} srv={{ id: 'emby', name: 'Emby', key: 'emby_api_key', port: 8096 }} />
				<p class="small muted">Jellyfin and Plex can be set up the same way in Settings afterwards.</p>
				<h3>Notifications (Pushover)</h3>
				<Field {form} name="pushover_user_key" />
				<Field {form} name="pushover_app_token" />
				<TestButton check="pushover" values={form.body} label="Send a test notification" />
			{/if}

			{#if act.error}<p class="small error-text">{act.error}</p>{/if}
			<div class="row nav">
				{#if step > 0}<button type="button" onclick={() => (step -= 1)} disabled={act.busy}>Back</button>{/if}
				<button type="button" class="primary" onclick={next} disabled={act.busy || needsTmdb}>
					{act.busy ? 'Saving…' : step < steps.length - 1 ? 'Save and continue' : 'Finish'}
				</button>
				{#if step < steps.length - 1}
					<button type="button" class="link" onclick={() => { act.error = null; step += 1; }}>
						{needsTmdb ? 'Skip for now (no TV, and only films TheDiscDB knows)' : 'Skip this step'}
					</button>
				{/if}
			</div>
		</section>

		<SetupProblems />
	{/if}
</div>

<style>
	.setup {
		max-width: 720px;
	}
	.steps {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 14px;
		list-style: none;
		padding: 0;
		margin: 0;
	}
	.steps li.current button {
		font-weight: 600;
		text-decoration: underline;
	}
	.steps li.done button::after {
		content: ' ✓';
	}
	.nav {
		margin-top: 6px;
	}
	.choices {
		display: grid;
		gap: 4px;
	}
	.two {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 10px;
	}
	.notice {
		border-left: 3px solid var(--warn);
		background: var(--warn-bg);
		border-radius: 6px;
		padding: 8px 10px;
		font-size: 14px;
	}
	.notice.bad {
		border-left-color: var(--bad);
		background: var(--bad-bg);
	}
	.notice p {
		margin: 0;
	}
	.provider {
		display: grid;
		gap: 6px;
		padding: 8px 0;
		border-top: 1px solid var(--border);
	}
	.provider p {
		margin: 0;
	}
	h3 {
		margin: 8px 0 0;
	}
	.ok {
		color: var(--ok);
	}
	button.link {
		background: none;
		border: none;
		padding: 0;
		color: var(--accent, inherit);
		text-decoration: underline;
		cursor: pointer;
	}
	@media (max-width: 480px) {
		.two {
			grid-template-columns: 1fr;
		}
	}
</style>
