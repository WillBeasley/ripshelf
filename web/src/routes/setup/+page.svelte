<script lang="ts">
	// First-start setup, the basics, one step at a time. Each step is saved when you
	// move on, so leaving half-way loses nothing; everything here is in Settings too.
	import type { ProviderSlot, ServerLibrary, SettingsUpdate, SettingsView, SetupTools } from '$lib/api-types';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { Action } from '$lib/actions.svelte';
	import { live } from '$lib/live.svelte';
	import TestButton from '$lib/components/TestButton.svelte';
	import FolderStatus from '$lib/components/FolderStatus.svelte';
	import Command from '$lib/components/Command.svelte';
	import SetupProblems from '$lib/components/SetupProblems.svelte';

	const steps = ['Drives', 'MakeMKV', 'Folders', 'Film and TV data', 'Extras (optional)'] as const;
	let step = $state(0);
	let saved = $state<number[]>([]); // steps saved (a ✓), not just passed

	const act = new Action();
	const importAct = new Action();
	let view = $state<SettingsView | null>(null);
	let tools = $state<SetupTools | null>(null);
	let importNote = $state<string | null>(null);
	let fileInput = $state<HTMLInputElement | null>(null);

	// The fields of every step. Secrets start empty, and blank keeps the saved one.
	let names = $state<Record<string, string>>({});
	let disabled = $state<string[]>([]);
	type Lists = { movie: ProviderSlot[]; tv: ProviderSlot[]; episodes: ProviderSlot[] };
	let lists = $state<Lists>({ movie: [], tv: [], episodes: [] });
	let f = $state({
		makemkv_key: '',
		library_dir: '',
		scratch_dir: '',
		tmdb_api_key: '',
		tvdb_api_key: '',
		omdb_api_key: '',
		emby_url: '',
		emby_api_key: '',
		emby_movies_library: '',
		emby_movies_path: '',
		emby_tv_library: '',
		emby_tv_path: '',
		pushover_user_key: '',
		pushover_app_token: '',
		transcode_enabled: true,
		// Off, crop only (re-encodes letterboxed rips, nothing else), or transcode the codecs listed.
		transcode_mode: 'transcode' as 'off' | 'crop' | 'transcode',
		transcode_codecs: 'vc1',
		transcode_crop: true,
		transcode_hdr: false,
		transcode_encoder: 'auto' as 'auto' | 'qsv' | 'vaapi' | 'nvenc' | 'x265',
		transcode_quality: 21,
		transcode_keep_original: false
	});

	async function load() {
		const [v, t] = await Promise.all([act.run(() => api.settings()), act.run(() => api.setupTools())]);
		if (v) {
			view = v;
			disabled = [...v.disabled_drives];
			lists = {
				movie: [...(v.metadata_providers.movie ?? [])],
				tv: [...(v.metadata_providers.tv ?? [])],
				episodes: [...(v.metadata_providers.episodes ?? [])]
			};
			names = Object.fromEntries(live.drives.map((d) => [d.id, d.name ?? '']));
			f.library_dir = v.library_dir;
			f.scratch_dir = v.scratch_dir;
			f.emby_url = v.emby_url ?? '';
			f.emby_movies_library = v.emby_movies_library ?? '';
			f.emby_movies_path = v.emby_movies_path ?? '';
			f.emby_tv_library = v.emby_tv_library ?? '';
			f.emby_tv_path = v.emby_tv_path ?? '';
			f.transcode_enabled = v.transcode_enabled;
			f.transcode_mode = !v.transcode_enabled ? 'off' : !v.transcode_codecs.length && v.transcode_crop === 'always' ? 'crop' : 'transcode';
			f.transcode_codecs = v.transcode_codecs.join(', ') || 'vc1';
			f.transcode_crop = v.transcode_crop !== 'off';
			f.transcode_hdr = v.transcode_hdr;
			f.transcode_encoder = v.transcode_encoder;
			f.transcode_quality = v.transcode_quality;
			f.transcode_keep_original = v.transcode_keep_original;
		}
		if (t) tools = t;
	}
	$effect(() => {
		load();
	});

	const typed = (v: string) => (v.trim() ? v.trim() : undefined);

	// What each step saves (only what was typed or changed).
	function stepBody(i: number): SettingsUpdate {
		if (!view) return {};
		switch (i) {
			case 0:
				return { disabled_drives: disabled };
			case 1:
				return { makemkv_key: typed(f.makemkv_key) };
			case 2:
				return {
					library_dir: f.library_dir.trim() !== view.library_dir ? f.library_dir.trim() : undefined,
					scratch_dir: f.scratch_dir.trim() !== view.scratch_dir ? f.scratch_dir.trim() : undefined
				};
			case 3:
				return {
					tmdb_api_key: typed(f.tmdb_api_key),
					tvdb_api_key: typed(f.tvdb_api_key),
					omdb_api_key: typed(f.omdb_api_key),
					metadata_providers: lists
				};
			default:
				return {
					emby_url: f.emby_url.trim() || null,
					emby_api_key: typed(f.emby_api_key),
					emby_movies_library: f.emby_movies_library || null,
					emby_movies_path: f.emby_movies_path || null,
					emby_tv_library: f.emby_tv_library || null,
					emby_tv_path: f.emby_tv_path || null,
					pushover_user_key: typed(f.pushover_user_key),
					pushover_app_token: typed(f.pushover_app_token),
					...transcodeBody()
				};
		}
	}

	async function save(i: number): Promise<boolean> {
		if (i === 0) {
			for (const d of live.drives) {
				const name = (names[d.id] ?? '').trim();
				if (name !== (d.name ?? '')) {
					const r = await act.run(() => api.renameDrive(d.id, name));
					if (r === undefined) return false;
				}
			}
		}
		const body = Object.fromEntries(Object.entries(stepBody(i)).filter(([, v]) => v !== undefined));
		if (!Object.keys(body).length) return true;
		const v = await act.run(() => api.saveSettings(body));
		if (!v) return false;
		view = v;
		for (const k of ['makemkv_key', 'tmdb_api_key', 'tvdb_api_key', 'omdb_api_key', 'emby_api_key', 'pushover_user_key', 'pushover_app_token'] as const)
			f[k] = '';
		if (i === 1) tools = (await api.setupTools().catch(() => null)) ?? tools;
		return true;
	}

	// Without TMDB nothing is identified, so that step needs a key (or "Skip for now").
	const needsTmdb = $derived(step === 3 && !view?.tmdb_api_key_set && !f.tmdb_api_key.trim());

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
		const v = await act.run(() => api.saveSettings({ setup_complete: true }));
		if (v) {
			live.setupComplete = true;
			goto('/');
		}
	}

	// The form as typed, for the Test buttons.
	const typedValues = (): SettingsUpdate => ({
		makemkv_key: typed(f.makemkv_key),
		library_dir: f.library_dir.trim(),
		scratch_dir: f.scratch_dir.trim(),
		tmdb_api_key: typed(f.tmdb_api_key),
		tvdb_api_key: typed(f.tvdb_api_key),
		omdb_api_key: typed(f.omdb_api_key),
		emby_url: f.emby_url.trim() || null,
		emby_api_key: typed(f.emby_api_key),
		emby_movies_library: f.emby_movies_library || null,
		emby_movies_path: f.emby_movies_path || null,
		emby_tv_library: f.emby_tv_library || null,
		emby_tv_path: f.emby_tv_path || null,
		pushover_user_key: typed(f.pushover_user_key),
		pushover_app_token: typed(f.pushover_app_token)
	});

	const uhdDrives = $derived(live.drives.filter((d) => d.capabilities.includes('uhd')));
	const needsData = $derived(uhdDrives.filter((d) => !view?.makemkv_drive_data[d.id]));

	async function importData(e: SubmitEvent) {
		e.preventDefault();
		importNote = null;
		const file = fileInput?.files?.[0];
		if (!file) {
			importAct.error = 'Choose a _private_data.tar first';
			return;
		}
		const form = new FormData();
		form.set('drive_ids', 'all');
		form.set('file', file);
		const r = await importAct.run(() => api.importPrivateData(form));
		if (r) {
			importNote = `Imported for ${r.drive_ids.length} drive(s).`;
			view = (await api.settings().catch(() => null)) ?? view;
		}
	}

	// Metadata providers, shown in the order they're asked (best first), each switched on
	// or off in every list it's in. Reordering is in Settings.
	const ORDER = ['thediscdb', 'tmdb', 'tvdb', 'tvmaze', 'omdb'] as const;
	const WHY: Record<string, string> = {
		thediscdb: 'Recognises the exact disc, its edition, and which title is the film or which episodes are on it. No key.',
		tmdb: 'Names films and shows, with posters and episode lists. Every TV lookup goes through it, so it is needed.',
		tvdb: 'Recommended for TV. Finds shows TMDB misses, and has the DVD episode order.',
		tvmaze: 'TV shows and episode lists. No key.',
		omdb: 'A second opinion on films (IMDb data). Off unless you want it.'
	};
	const KEY_FIELD: Record<string, 'tmdb_api_key' | 'tvdb_api_key' | 'omdb_api_key'> = {
		tmdb: 'tmdb_api_key',
		tvdb: 'tvdb_api_key',
		omdb: 'omdb_api_key'
	};
	const providers = $derived(ORDER.map((n) => view?.providers.find((p) => p.name === n)).filter((p) => p != null));
	const providerOn = (name: string) => Object.values(lists).some((l) => l.some((r) => r.name === name && r.enabled));
	function setProvider(name: string, on: boolean) {
		lists = {
			movie: lists.movie.map((r) => (r.name === name ? { ...r, enabled: on } : r)),
			tv: lists.tv.map((r) => (r.name === name ? { ...r, enabled: on } : r)),
			episodes: lists.episodes.map((r) => (r.name === name ? { ...r, enabled: on } : r))
		};
	}
	const keySet = (name: string) =>
		name === 'tmdb' ? view?.tmdb_api_key_set : name === 'tvdb' ? view?.tvdb_api_key_set : name === 'omdb' ? view?.omdb_api_key_set : true;

	// The transcoding choice as settings, where "crop only" re-encodes just the letterboxed rips.
	function transcodeBody(): SettingsUpdate {
		const common = {
			transcode_hdr: f.transcode_hdr,
			transcode_encoder: f.transcode_encoder,
			transcode_quality: Number(f.transcode_quality),
			transcode_keep_original: f.transcode_keep_original
		};
		if (f.transcode_mode === 'off') return { transcode_enabled: false };
		if (f.transcode_mode === 'crop')
			return { ...common, transcode_enabled: true, transcode_codecs: [], transcode_crop: 'always' };
		return {
			...common,
			transcode_enabled: true,
			transcode_codecs: f.transcode_codecs.split(/[\s,]+/).map((c) => c.trim().toLowerCase()).filter(Boolean),
			transcode_crop: f.transcode_crop ? 'when_transcoding' : 'off'
		};
	}

	// Emby's libraries, to pick where films and TV go (and map our folders onto theirs).
	const embyAct = new Action();
	let libraries = $state<ServerLibrary[]>([]);
	async function loadLibraries() {
		libraries = (await embyAct.run(() => api.serverLibraries('emby', f.emby_url.trim(), typed(f.emby_api_key) ?? null))) ?? [];
		for (const k of ['movies', 'tv'] as const) {
			// Pre-pick the only library of the right kind.
			const fitting = libraries.filter((l) => fits(l, k));
			if (!f[`emby_${k}_library`] && fitting.length === 1) pickLibrary(k, fitting[0].id);
		}
	}
	const fits = (l: ServerLibrary, k: 'movies' | 'tv') =>
		!l.type || l.type === 'mixed' || l.type === (k === 'movies' ? 'movies' : 'tvshows');
	function pickLibrary(k: 'movies' | 'tv', id: string) {
		const lib = libraries.find((l) => l.id === id);
		f[`emby_${k}_library`] = id;
		f[`emby_${k}_path`] = lib?.locations[0] ?? '';
	}

	function toggle(id: string, on: boolean) {
		disabled = on ? disabled.filter((d) => d !== id) : [...disabled, id];
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

	{#if !view}
		<p class="muted">Loading…</p>
	{:else}
		<section class="card stack">
			{#if step === 0}
				<h2>Drives</h2>
				{#if !live.drives.length}
					<p>No optical drives found. Plug one in (or check it's switched on), then <button type="button" class="link" onclick={() => api.redetect().then((d) => (live.drives = d))}>look again</button>.</p>
				{:else}
					<p class="muted small">Give each drive a name you'll recognise (e.g. "Top bay"), and untick any ripshelf should leave alone.</p>
					{#each live.drives as d (d.id)}
						<div class="drive">
							<label class="check">
								<input type="checkbox" checked={!disabled.includes(d.id)} onchange={(e) => toggle(d.id, e.currentTarget.checked)} />
								<span>{`${d.vendor} ${d.model}`.replace(/\s+/g, ' ')}</span>
							</label>
							<span class="tags">
								<span class="muted mono small">{d.device}</span>
								{#each d.capabilities as c (c)}<span class="chip" class:uhd={c === 'uhd'}>{c === 'uhd' ? 'UHD' : c === 'bluray' ? 'Blu-ray' : 'DVD'}</span>{/each}
							</span>
							<input aria-label="Name for {d.device}" placeholder="Name (optional)" bind:value={names[d.id]} disabled={disabled.includes(d.id)} />
						</div>
					{/each}
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
						{#if view.makemkv_key_set}✓ ripshelf has a MakeMKV key, and gives it to MakeMKV.
						{:else if tools.makemkv_registered}✓ MakeMKV is already registered (in the MakeMKV app). A key here replaces that one.
						{:else}No MakeMKV key yet. Without one, MakeMKV runs as a trial.{/if}
					</p>
				{/if}
				<label class="field">
					MakeMKV key {view.makemkv_key_set ? '(set, leave blank to keep it)' : ''}
					<input class="mono" bind:value={f.makemkv_key} autocomplete="off" placeholder="T-…" />
				</label>
				<TestButton check="makemkv" values={typedValues} label="Check the key" />
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
				<label class="field">
					Library (films go in its Movies folder, TV in TV)
					<input class="mono" bind:value={f.library_dir} required />
				</label>
				<FolderStatus path={f.library_dir} />
				<label class="field">
					Scratch (rips in progress, about 100 GB for a UHD disc)
					<input class="mono" bind:value={f.scratch_dir} required />
				</label>
				<FolderStatus path={f.scratch_dir} />
				<TestButton check="paths" values={typedValues} label="Check the folders are writable" />
			{:else if step === 3}
				<h2>Film and TV data</h2>
				<p class="small">
					ripshelf looks each disc up with these, in this order. TheDiscDB comes first, since it knows the disc itself, then the
					searches. TMDB is the one you need, because without it TV discs can't be identified at all (the show search goes
					through TMDB), and films only when TheDiscDB knows the disc, without posters.
				</p>
				{#each providers as p (p.name)}
					{@const keyField = KEY_FIELD[p.name]}
					<div class="provider">
						<label class="check">
							<input type="checkbox" aria-label="Use {p.label}" checked={p.name === 'tmdb' || providerOn(p.name)} disabled={p.name === 'tmdb'}
								onchange={(e) => setProvider(p.name, e.currentTarget.checked)} />
							<strong>{p.label}</strong>
							{#if p.name === 'tmdb'}<span class="chip">needed</span>{:else if p.name === 'tvdb'}<span class="chip">recommended for TV</span>{/if}
						</label>
						<p class="small muted">{WHY[p.name] ?? p.description}</p>
						{#if keyField}
							<label class="field small">
								<span>API key {keySet(p.name) ? '(set, leave blank to keep it)' : ''}
									{#if p.site}· <a href={p.site} target="_blank" rel="noreferrer">get one</a>{/if}</span>
								<input class="mono" aria-label="{p.label} API key" bind:value={f[keyField]} autocomplete="off" />
							</label>
							{#if p.check}<TestButton check={p.check} values={typedValues} />{/if}
						{/if}
					</div>
				{/each}
				<p class="small muted">To change the order, or use a provider for films but not TV, see Settings → Metadata providers.</p>
			{:else}
				<h2>Extras <span class="chip">optional</span></h2>
				<p class="muted small">These are all optional, so skip them now and set them up in Settings when you want.</p>
				<h3>Transcoding</h3>
				<div class="choices" role="radiogroup" aria-label="Transcoding">
					<label class="check"><input type="radio" bind:group={f.transcode_mode} value="off" /> Off (copy rips as they are)</label>
					<label class="check"><input type="radio" bind:group={f.transcode_mode} value="crop" /> Crop black bars only (re-encodes just the letterboxed rips)</label>
					<label class="check"><input type="radio" bind:group={f.transcode_mode} value="transcode" /> Transcode to H.265</label>
				</div>
				{#if f.transcode_mode === 'transcode'}
					<label class="field">
						Re-encode rips whose video is (ffprobe codec names, comma separated)
						<input bind:value={f.transcode_codecs} placeholder="vc1" />
					</label>
					<label class="check"><input type="checkbox" bind:checked={f.transcode_crop} /> Crop black bars while transcoding</label>
				{/if}
				{#if f.transcode_mode !== 'off'}
					<div class="row">
						<label class="field">
							Encoder
							<select bind:value={f.transcode_encoder}>
								<option value="auto">Automatic (the first GPU encoder that works, else the CPU)</option>
								{#if !tools?.windows}
									<option value="qsv">GPU (Intel Quick Sync)</option>
									<option value="vaapi">GPU (VAAPI)</option>
								{/if}
								<option value="nvenc">GPU (NVIDIA NVENC)</option>
								<option value="x265">CPU (x265)</option>
							</select>
						</label>
						<label class="field">
							Quality (lower is better and bigger)
							<input type="number" min="10" max="40" bind:value={f.transcode_quality} />
						</label>
					</div>
					<label class="check"><input type="checkbox" bind:checked={f.transcode_hdr} /> Also HDR, Dolby Vision and UHD rips (left alone otherwise)</label>
					<label class="check"><input type="checkbox" bind:checked={f.transcode_keep_original} /> Keep the original rip next to it</label>
				{/if}
				{#if tools && !(tools.ffmpeg.found && tools.ffprobe.found)}
					<div class="notice">
						<p>ffmpeg isn't installed, so rips are copied as they are{f.transcode_mode !== 'off' ? ' until it is' : ''}.{tools.windows ? ' Install it:' : ''}</p>
						{#if tools.windows}<Command text="winget install -e --id Gyan.FFmpeg" />{/if}
					</div>
				{/if}
				{#if tools && !tools.java.found}
					<div class="notice">
						<p>No Java found. MakeMKV needs it for some Blu-ray menus (BD-J).{tools.windows ? ' Install it:' : ''}</p>
						{#if tools.windows}<Command text="winget install -e --id Microsoft.OpenJDK.21" />{/if}
					</div>
				{/if}
				<h3>Emby</h3>
				<p class="small muted">Emby is told about each new film or episode, so it shows up straight away.</p>
				<label class="field">Server address <input bind:value={f.emby_url} placeholder="http://emby.lan:8096" /></label>
				<label class="field">API key {view.emby_api_key_set ? '(set)' : ''} <input class="mono" bind:value={f.emby_api_key} autocomplete="off" /></label>
				<p class="small muted">
					Make one in Emby (as an admin) under Settings (the dashboard) → Advanced → API Keys (on some versions the Security
					tab under Advanced) → New API Key, named something like "ripshelf".
				</p>
				<div class="row">
					<button type="button" onclick={loadLibraries} disabled={embyAct.busy || !f.emby_url.trim()}>
						{embyAct.busy ? 'Connecting…' : libraries.length ? 'Fetch the libraries again' : 'Connect and fetch the libraries'}
					</button>
					{#if embyAct.error}<span class="small error-text">{embyAct.error}</span>
					{:else if libraries.length}<span class="small ok">✓ {libraries.length} libraries</span>{/if}
				</div>
				{#if libraries.length || f.emby_movies_library || f.emby_tv_library}
					{#each [['movies', 'Films', view.movies_dir], ['tv', 'TV', view.tv_dir]] as [kind, label, ours] (kind)}
						{@const k = kind as 'movies' | 'tv'}
						{@const lib = libraries.find((l) => l.id === f[`emby_${k}_library`])}
						<label class="field">
							{label} go into this Emby library
							<select value={f[`emby_${k}_library`]} onchange={(e) => pickLibrary(k, e.currentTarget.value)}>
								<option value="">None</option>
								{#if f[`emby_${k}_library`] && !lib}
									<option value={f[`emby_${k}_library`]}>{f[`emby_${k}_path`] || 'saved library'}</option>
								{/if}
								{#each libraries.filter((l) => fits(l, k)) as l (l.id)}
									<option value={l.id}>{l.name}{l.type ? ` (${l.type})` : ''}</option>
								{/each}
							</select>
						</label>
						{#if lib && lib.locations.length > 1}
							<label class="field">
								Which of its folders is ripshelf's {label.toLowerCase()} folder?
								<select bind:value={f[`emby_${k}_path`]}>
									{#each lib.locations as loc (loc)}<option value={loc}>{loc}</option>{/each}
								</select>
							</label>
						{/if}
						{#if f[`emby_${k}_path`]}
							<p class="small muted">
								ripshelf's <span class="mono">{ours}</span> is Emby's <span class="mono">{f[`emby_${k}_path`]}</span>
							</p>
						{/if}
					{/each}
				{/if}
				<TestButton check="emby" values={typedValues} label="Test the connection" />
				<p class="small muted">Jellyfin and Plex can be set up the same way in Settings afterwards.</p>
				<h3>Notifications (Pushover)</h3>
				<label class="field">User key {view.pushover_user_key_set ? '(set)' : ''} <input class="mono" bind:value={f.pushover_user_key} autocomplete="off" /></label>
				<label class="field">App token {view.pushover_app_token_set ? '(set)' : ''} <input class="mono" bind:value={f.pushover_app_token} autocomplete="off" /></label>
				<TestButton check="pushover" values={typedValues} label="Send a test notification" />
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
	.drive {
		display: grid;
		grid-template-columns: 1fr auto;
		align-items: center;
		gap: 6px 12px;
		padding: 8px 0;
		border-top: 1px solid var(--border);
	}
	.drive .tags {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		align-items: center;
		justify-content: flex-end;
	}
	.drive .tags .chip {
		white-space: nowrap;
	}
	.drive input:not([type='checkbox']) {
		grid-column: 1 / -1;
	}
	.nav {
		margin-top: 6px;
	}
	.choices {
		display: grid;
		gap: 4px;
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
		gap: 4px;
		padding: 8px 0;
		border-top: 1px solid var(--border);
	}
	.provider p {
		margin: 0;
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
</style>
