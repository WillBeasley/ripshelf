<script lang="ts">
	import type {
		MakeMKVFile,
		ProviderInfo,
		ProviderSlot,
		ServerLibrary,
		SettingsUpdate,
		SettingsView,
		TranscodeSource
	} from '$lib/api-types';

	type RipMode = 'titles' | 'backup';
	import { api, type MediaServer } from '$lib/api';
	import { Action } from '$lib/actions.svelte';
	import { live } from '$lib/live.svelte';
	import { alerts } from '$lib/alerts.svelte';
	import DriveName from '$lib/components/DriveName.svelte';
	import SetupProblems from '$lib/components/SetupProblems.svelte';
	import TestButton from '$lib/components/TestButton.svelte';
	import FolderStatus from '$lib/components/FolderStatus.svelte';
	import HardwarePanel from '$lib/components/HardwarePanel.svelte';
	import RemoteWorkers from '$lib/components/RemoteWorkers.svelte';
	import ProviderList from '$lib/components/ProviderList.svelte';

	type Lists = { movie: ProviderSlot[]; tv: ProviderSlot[]; episodes: ProviderSlot[] };

	const act = new Action();
	const importAct = new Action();

	let view = $state<SettingsView | null>(null);
	let saved = $state(false);
	let importNote = $state<string | null>(null);

	// Form fields. Secrets start empty. Typing replaces, and "Clear" removes.
	let f = $state({
		tmdb_language: '',
		tvdb_episode_order: 'aired' as 'aired' | 'dvd',
		metadata_providers: { movie: [], tv: [], episodes: [] } as Lists,
		thediscdb_contribute: 'off' as 'off' | 'ask' | 'auto',
		thediscdb_contribute_url: 'https://thediscdb.com',
		auto_confirm_threshold: 0.9,
		tv_auto_confirm: true,
		tv_rip_extras: true,
		verify_order: true,
		tv_auto_threshold: 0.9,
		catalogue_search: 'off' as 'off' | 'searxng',
		searxng_url: '',
		auto_eject: true,
		scan_timeout_s: 300,
		scan_max_s: 7200,
		poll_interval_s: 3,
		uhd_models: '',
		disabled_drives: [] as string[],
		rip_mode: 'titles' as RipMode,
		drive_modes: {} as Record<string, RipMode>,
		public_url: '',
		emby_url: '',
		emby_movies_library: '',
		emby_movies_path: '',
		emby_tv_library: '',
		emby_tv_path: '',
		jellyfin_url: '',
		jellyfin_movies_library: '',
		jellyfin_movies_path: '',
		jellyfin_tv_library: '',
		jellyfin_tv_path: '',
		plex_url: '',
		plex_movies_library: '',
		plex_movies_path: '',
		plex_tv_library: '',
		plex_tv_path: '',
		pushover_device: '',
		notify_events: {} as Record<string, number | null>,
		notify_batch_s: 120,
		backup_keep: 7,
		transcode_enabled: false,
		transcode_codecs: 'vc1',
		transcode_crop: 'when_transcoding' as 'off' | 'when_transcoding' | 'always',
		transcode_hdr: false,
		transcode_sources: {} as Record<string, TranscodeSource>,
		transcode_encoder: 'auto' as 'auto' | 'vaapi' | 'x265',
		transcode_quality: 21,
		transcode_keep_original: false,
		transcode_parallel: 1,
		vaapi_device: '/dev/dri/renderD128',
		library_dir: '',
		movies_dir: '',
		tv_dir: '',
		disc_backups_dir: '',
		scratch_dir: ''
	});
	const emptySecrets = () => ({
		tmdb_api_key: '',
		tvdb_api_key: '',
		tvdb_pin: '',
		omdb_api_key: '',
		thediscdb_api_key: '',
		emby_api_key: '',
		jellyfin_api_key: '',
		plex_token: '',
		makemkv_key: '',
		pushover_user_key: '',
		pushover_app_token: ''
	});
	let secrets = $state(emptySecrets());

	// Imported files, kept for remote workers.
	const filesAct = new Action();
	let mkFiles = $state<MakeMKVFile[]>([]);
	async function loadFiles() {
		mkFiles = (await filesAct.run(() => api.makemkvFiles())) ?? mkFiles;
	}
	$effect(() => {
		loadFiles();
	});
	async function deleteFile(f: MakeMKVFile) {
		if (!confirm(`Stop offering ${f.name} (${f.sha256.slice(0, 12)}…) to workers? Drives that have it keep it.`)) return;
		await filesAct.run(() => api.deleteMakemkvFile(f.sha256, f.name));
		await loadFiles();
	}
	const driveLabel = (id: string) => live.drives.find((d) => d.id === id)?.display_name ?? id;
	const kb = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`);

	let importPath = $state('');
	let importDrives = $state('all');
	let fileInput = $state<HTMLInputElement | null>(null);

	function fill(v: SettingsView) {
		view = v;
		f = {
			tmdb_language: v.tmdb_language,
			tvdb_episode_order: v.tvdb_episode_order,
			metadata_providers: {
				movie: [...(v.metadata_providers.movie ?? [])],
				tv: [...(v.metadata_providers.tv ?? [])],
				episodes: [...(v.metadata_providers.episodes ?? [])]
			},
			thediscdb_contribute: v.thediscdb_contribute ?? 'off',
			thediscdb_contribute_url: v.thediscdb_contribute_url ?? 'https://thediscdb.com',
			auto_confirm_threshold: v.auto_confirm_threshold,
			tv_auto_confirm: v.tv_auto_confirm,
			tv_rip_extras: v.tv_rip_extras,
			verify_order: v.verify_order,
			tv_auto_threshold: v.tv_auto_threshold,
			catalogue_search: v.catalogue_search,
			searxng_url: v.searxng_url ?? '',
			auto_eject: v.auto_eject,
			scan_timeout_s: v.scan_timeout_s,
			scan_max_s: v.scan_max_s ?? 7200,
			poll_interval_s: v.poll_interval_s,
			uhd_models: v.uhd_models.join('\n'),
			disabled_drives: [...v.disabled_drives],
			rip_mode: v.rip_mode ?? 'titles',
			drive_modes: { ...v.drive_modes },
			public_url: v.public_url ?? '',
			emby_url: v.emby_url ?? '',
			emby_movies_library: v.emby_movies_library ?? '',
			emby_movies_path: v.emby_movies_path ?? '',
			emby_tv_library: v.emby_tv_library ?? '',
			emby_tv_path: v.emby_tv_path ?? '',
			jellyfin_url: v.jellyfin_url ?? '',
			jellyfin_movies_library: v.jellyfin_movies_library ?? '',
			jellyfin_movies_path: v.jellyfin_movies_path ?? '',
			jellyfin_tv_library: v.jellyfin_tv_library ?? '',
			jellyfin_tv_path: v.jellyfin_tv_path ?? '',
			plex_url: v.plex_url ?? '',
			plex_movies_library: v.plex_movies_library ?? '',
			plex_movies_path: v.plex_movies_path ?? '',
			plex_tv_library: v.plex_tv_library ?? '',
			plex_tv_path: v.plex_tv_path ?? '',
			pushover_device: v.pushover_device ?? '',
			notify_events: { ...v.notify_events },
			notify_batch_s: v.notify_batch_s,
			backup_keep: v.backup_keep,
			transcode_enabled: v.transcode_enabled,
			transcode_codecs: v.transcode_codecs.join(', '),
			transcode_crop: v.transcode_crop,
			transcode_hdr: v.transcode_hdr,
			transcode_sources: Object.fromEntries(
				Object.entries(v.transcode_sources).map(([k, src]) => [k, { ...src }])
			),
			transcode_encoder: v.transcode_encoder,
			transcode_quality: v.transcode_quality,
			transcode_keep_original: v.transcode_keep_original,
			transcode_parallel: v.transcode_parallel,
			vaapi_device: v.vaapi_device,
			library_dir: v.library_dir,
			movies_dir: v.custom_movies_dir ?? '',
			tv_dir: v.custom_tv_dir ?? '',
			disc_backups_dir: v.custom_disc_backups_dir ?? '',
			scratch_dir: v.scratch_dir
		};
		secrets = emptySecrets();
		live.threshold = v.auto_confirm_threshold;
	}

	$effect(() => {
		act.run(() => api.settings()).then((v) => v && fill(v));
	});

	const orNull = (s: string) => (s.trim() ? s.trim() : null);
	// The path separator this machine's folders use (for the placeholders).
	const sep = $derived(view?.library_dir.includes('\\') ? '\\' : '/');
	const priorities: [string, string][] = [
		['off', 'Off'],
		['-2', 'Lowest (no alert)'],
		['-1', 'Low (quiet)'],
		['0', 'Normal'],
		['1', 'High (bypasses quiet hours)']
	];
	const discKinds: [string, string][] = [
		['dvd', 'DVD'],
		['bluray', 'Blu-ray'],
		['uhd', '4K UHD']
	];
	const events: [string, string][] = [
		['disc', 'Disc inserted'],
		['review', 'Needs review'],
		['exists', 'Already in library'],
		['done', 'Ripped'],
		['failed', 'Failed'],
		['setup', 'Setup problem']
	];
	const eventPriority = (e: string) => {
		const p = f.notify_events[e];
		return p === null || p === undefined ? 'off' : String(p);
	};
	const setEventPriority = (e: string, v: string) => {
		f.notify_events = { ...f.notify_events, [e]: v === 'off' ? null : Number(v) };
	};

	// Media servers told about new files, and their libraries, to pick which one films and TV go into.
	const servers: { id: MediaServer; name: string; key: 'emby_api_key' | 'jellyfin_api_key' | 'plex_token'; port: number }[] = [
		{ id: 'emby', name: 'Emby', key: 'emby_api_key', port: 8096 },
		{ id: 'jellyfin', name: 'Jellyfin', key: 'jellyfin_api_key', port: 8096 },
		{ id: 'plex', name: 'Plex', key: 'plex_token', port: 32400 }
	];
	const libraryActs = { emby: new Action(), jellyfin: new Action(), plex: new Action() };
	let libraries = $state<Record<MediaServer, ServerLibrary[]>>({ emby: [], jellyfin: [], plex: [] });
	async function loadLibraries(srv: (typeof servers)[number]) {
		const key = secrets[srv.key].trim() || null;
		const url = f[`${srv.id}_url`].trim();
		libraries[srv.id] = (await libraryActs[srv.id].run(() => api.serverLibraries(srv.id, url, key))) ?? [];
	}
	const fits = (l: ServerLibrary, k: 'movies' | 'tv') =>
		!l.type || l.type === 'mixed' || l.type === (k === 'movies' ? 'movies' : 'tvshows');
	function pickLibrary(srv: MediaServer, k: 'movies' | 'tv', id: string) {
		const lib = libraries[srv].find((l) => l.id === id);
		f[`${srv}_${k}_library`] = id;
		f[`${srv}_${k}_path`] = lib?.locations[0] ?? '';
	}

	// Metadata providers, their descriptions, and whether their keys are set (or typed).
	const providerInfo = $derived(
		Object.fromEntries((view?.providers ?? []).map((p) => [p.name, p])) as Record<string, ProviderInfo>
	);
	// Data sources in use, as saved: ticked in a list with their keys set. TMDB's key is used for
	// every TV disc and every poster, whatever the lists say. Their terms ask for credit (About).
	const inUse = $derived.by(() => {
		const used = new Set<string>();
		if (!view) return used;
		for (const rows of Object.values(view.metadata_providers ?? {}))
			for (const r of rows ?? []) if (r.enabled && providerInfo[r.name]?.configured) used.add(r.name);
		if (view.tmdb_api_key_set) used.add('tmdb');
		return used;
	});
	const keyed = (name: string) => {
		const p = providerInfo[name];
		if (!p || p.configured) return true;
		return p.keys.every((k) => (secrets as Record<string, string>)[k]?.trim());
	};

	// The form as typed, secrets included only when typed (blank means the saved one).
	function body(): SettingsUpdate {
		const b = formBody();
		for (const [k, v] of Object.entries(secrets)) {
			if (v.trim()) (b as Record<string, unknown>)[k] = v.trim();
		}
		return b;
	}

	async function save(e: SubmitEvent) {
		e.preventDefault();
		saved = false;
		const v = await act.run(() => api.saveSettings(body()));
		if (v) {
			fill(v);
			saved = true;
		}
	}

	function formBody(): SettingsUpdate {
		return {
			tmdb_language: f.tmdb_language.trim() || 'en-GB',
			tvdb_episode_order: f.tvdb_episode_order,
			metadata_providers: f.metadata_providers,
			thediscdb_contribute: f.thediscdb_contribute,
			thediscdb_contribute_url: f.thediscdb_contribute_url.trim() || 'https://thediscdb.com',
			auto_confirm_threshold: Number(f.auto_confirm_threshold),
			tv_auto_confirm: f.tv_auto_confirm,
			tv_rip_extras: f.tv_rip_extras,
			verify_order: f.verify_order,
			tv_auto_threshold: Number(f.tv_auto_threshold),
			catalogue_search: f.catalogue_search,
			searxng_url: orNull(f.searxng_url),
			auto_eject: f.auto_eject,
			scan_timeout_s: Number(f.scan_timeout_s),
			scan_max_s: Number(f.scan_max_s),
			poll_interval_s: Number(f.poll_interval_s),
			uhd_models: f.uhd_models.split('\n').map((s) => s.trim()).filter(Boolean),
			disabled_drives: f.disabled_drives,
			rip_mode: f.rip_mode,
			drive_modes: f.drive_modes,
			public_url: orNull(f.public_url),
			emby_url: orNull(f.emby_url),
			emby_movies_library: orNull(f.emby_movies_library),
			emby_movies_path: orNull(f.emby_movies_path),
			emby_tv_library: orNull(f.emby_tv_library),
			emby_tv_path: orNull(f.emby_tv_path),
			jellyfin_url: orNull(f.jellyfin_url),
			jellyfin_movies_library: orNull(f.jellyfin_movies_library),
			jellyfin_movies_path: orNull(f.jellyfin_movies_path),
			jellyfin_tv_library: orNull(f.jellyfin_tv_library),
			jellyfin_tv_path: orNull(f.jellyfin_tv_path),
			plex_url: orNull(f.plex_url),
			plex_movies_library: orNull(f.plex_movies_library),
			plex_movies_path: orNull(f.plex_movies_path),
			plex_tv_library: orNull(f.plex_tv_library),
			plex_tv_path: orNull(f.plex_tv_path),
			pushover_device: orNull(f.pushover_device),
			notify_events: f.notify_events,
			notify_batch_s: Number(f.notify_batch_s),
			backup_keep: Number(f.backup_keep),
			transcode_enabled: f.transcode_enabled,
			transcode_codecs: f.transcode_codecs.split(/[\s,]+/).map((c) => c.trim().toLowerCase()).filter(Boolean),
			transcode_crop: f.transcode_crop,
			transcode_hdr: f.transcode_hdr,
			transcode_sources: f.transcode_sources,
			transcode_encoder: f.transcode_encoder,
			transcode_quality: Number(f.transcode_quality),
			transcode_keep_original: f.transcode_keep_original,
			transcode_parallel: Number(f.transcode_parallel),
			vaapi_device: f.vaapi_device.trim() || '/dev/dri/renderD128',
			...changedFolders()
		};
	}

	// Only folders that were changed, because saving other settings must not pin the folders
	// (they may come from the environment, e.g. a container's mounts).
	function changedFolders(): SettingsUpdate {
		if (!view) return {};
		const was: Record<string, string> = {
			library_dir: view.library_dir,
			movies_dir: view.custom_movies_dir ?? '',
			tv_dir: view.custom_tv_dir ?? '',
			disc_backups_dir: view.custom_disc_backups_dir ?? '',
			scratch_dir: view.scratch_dir
		};
		const out: Record<string, string> = {};
		for (const [k, v] of Object.entries(was)) {
			const now = (f as Record<string, unknown>)[k] as string;
			if (now.trim() !== v) out[k] = now.trim();
		}
		return out;
	}

	async function clearSecret(name: keyof typeof secrets) {
		const v = await act.run(() => api.saveSettings({ [name]: '' }));
		if (v) fill(v);
	}

	async function importData(e: SubmitEvent) {
		e.preventDefault();
		importNote = null;
		const form = new FormData();
		form.set('drive_ids', importDrives);
		const file = fileInput?.files?.[0];
		if (file) form.set('file', file);
		else if (importPath.trim()) form.set('path', importPath.trim());
		else {
			importAct.error = 'Choose a file or give a path on the server';
			return;
		}
		const r = await importAct.run(() => api.importPrivateData(form));
		if (r) {
			importNote = `Imported into ${r.drive_ids.length} drive(s). Retry any scan that failed or timed out.`;
			const v = await api.settings();
			fill(v);
			await loadFiles();
		}
	}

	// '' follows the default rip mode; otherwise the drive's own.
	function setDriveMode(id: string, mode: string) {
		const { [id]: _, ...rest } = f.drive_modes;
		f.drive_modes = mode ? { ...rest, [id]: mode as RipMode } : rest;
	}

	function toggleDrive(id: string, on: boolean) {
		f.disabled_drives = on ? f.disabled_drives.filter((d) => d !== id) : [...f.disabled_drives, id];
	}
</script>

<svelte:head><title>Settings · ripshelf</title></svelte:head>

{#snippet site(name: string)}
	{#if providerInfo[name]?.site}
		<a class="small" href={providerInfo[name].site} target="_blank" rel="noreferrer"
			>{providerInfo[name].needs_key ? 'Get a key' : 'Website'} →</a
		>
	{/if}
{/snippet}

{#snippet secret(name: keyof typeof secrets, label: string, isSet: boolean)}
	<label class="field">
		{label}
		<div class="row nowrap">
			<input
				type="password"
				autocomplete="off"
				placeholder={isSet ? '•••••••• (set; type to replace)' : 'not set'}
				bind:value={secrets[name]}
			/>
			{#if isSet}<button type="button" onclick={() => clearSecret(name)}>Clear</button>{/if}
		</div>
	</label>
{/snippet}

<div class="stack page">
	<div class="spread">
		<h1>Settings</h1>
		<span class="small"><a href="/setup">Run the setup again</a> · <a href="/logs">ripshelf log →</a></span>
	</div>
	<SetupProblems />

	{#if view}
		<form class="stack" onsubmit={save}>
			<section class="card stack">
				<h2>Metadata providers</h2>
				<p class="small muted">
					Where ripshelf looks discs, films and shows up. Each list is asked in order; untick a provider to
					leave it out. Providers that need a key are skipped until it is set below.
				</p>
				<ProviderList
					title="Films"
					hint="TheDiscDB recognises the disc itself; every ticked search is asked and their results merged."
					bind:rows={f.metadata_providers.movie}
					info={providerInfo}
					{keyed}
				/>
				<ProviderList
					title="TV shows"
					hint="Asked in order until one finds the show. TMDB names everything, so it needs a TMDB key either way."
					bind:rows={f.metadata_providers.tv}
					info={providerInfo}
					{keyed}
				/>
				<ProviderList
					title="TV episodes"
					hint="A season's episode list comes from the first provider that has it (TMDB's if none does)."
					bind:rows={f.metadata_providers.episodes}
					info={providerInfo}
					{keyed}
				/>

				<h3>TMDB</h3>
				{@render secret('tmdb_api_key', 'TMDB API key (v3)', view.tmdb_api_key_set)}
				<div class="row">
					<TestButton check="tmdb" values={body} label="Test connection" />
					{@render site('tmdb')}
				</div>
				<label class="field">TMDB language <input bind:value={f.tmdb_language} /></label>

				<h3>TheTVDB</h3>
				{@render secret('tvdb_api_key', 'TheTVDB API key (v4)', view.tvdb_api_key_set)}
				{@render secret('tvdb_pin', 'Subscriber PIN (only for user-supported keys)', view.tvdb_pin_set)}
				<label class="field">
					Episode order
					<select bind:value={f.tvdb_episode_order}>
						<option value="aired">Aired order</option>
						<option value="dvd">DVD order (falls back to aired)</option>
					</select>
				</label>
				<p class="small muted">
					DVD order follows the discs, but Emby numbers episodes in aired order unless the TV library is set to
					DVD order.
				</p>
				<div class="row">
					<TestButton check="tvdb" values={body} label="Test connection" />
					{@render site('tvdb')}
				</div>

				<h3>OMDb</h3>
				{@render secret('omdb_api_key', 'OMDb API key', view.omdb_api_key_set)}
				<div class="row">
					<TestButton check="omdb" values={body} label="Test connection" />
					{@render site('omdb')}
				</div>

				<h3>TheDiscDB and TVmaze</h3>
				<p class="small muted">Free, no key. TheDiscDB only knows discs someone has added, and you can add yours on its site.</p>
				<div class="row">
					<TestButton check="thediscdb" values={body} label="Test TheDiscDB" />
					<TestButton check="tvmaze" values={body} label="Test TVmaze" />
				</div>

				<h3>Contribute to TheDiscDB</h3>
				<p class="small muted">
					Send discs you have confirmed, and TheDiscDB doesn't know yet, so others' rips recognise them. What's sent is the
					disc's content hash, label and type, what it is (TMDB id, season) and its titles (playlist, length, size,
					which film or episode), with MakeMKV's title list. Never your drives, folders or host name. You finish
					each release on TheDiscDB's site, signed in with GitHub, before it is reviewed.
					<a href="https://thediscdb.com/help/contributing" target="_blank" rel="noreferrer">How it works →</a>
				</p>
				<label class="field">
					Contribute confirmed discs
					<select bind:value={f.thediscdb_contribute}>
						<option value="off">Off</option>
						<option value="ask">Ask, with a Send button on each job</option>
						<option value="auto">Automatically, once I confirm a disc</option>
					</select>
				</label>
				{#if f.thediscdb_contribute !== 'off'}
					<label class="field">
						TheDiscDB address
						<input bind:value={f.thediscdb_contribute_url} placeholder="https://thediscdb.com" />
					</label>
					{@render secret('thediscdb_api_key', 'API key (optional; only if TheDiscDB gave you one)', view.thediscdb_api_key_set)}
				{/if}
			</section>

			<section class="card stack">
				<h2>Identification</h2>
				<label class="field">
					Auto-confirm at score ≥ {Number(f.auto_confirm_threshold).toFixed(2)}
					<input type="range" min="0.5" max="1" step="0.01" bind:value={f.auto_confirm_threshold} />
				</label>
				<label class="check">
					<input type="checkbox" bind:checked={f.tv_auto_confirm} /> Confirm TV discs without review when everything agrees
				</label>
				<label class="field">
					TV show match needed ≥ {Number(f.tv_auto_threshold).toFixed(2)}
					<input type="range" min="0.5" max="1" step="0.01" bind:value={f.tv_auto_threshold} disabled={!f.tv_auto_confirm} />
				</label>
				<p class="small muted">
					It also needs the label to give the season and disc (or the disc continues your collection), every episode
					runtime matches, and the order is verified against the play-all.
				</p>
				<label class="check">
					<input type="checkbox" bind:checked={f.tv_rip_extras} /> Rip TV extras by default
				</label>
				<p class="small muted">
					When off, titles that look like extras start as Skip in the review table (switch any back to Extra to keep it).
				</p>
				<label class="check">
					<input type="checkbox" bind:checked={f.verify_order} /> Check the episode order after ripping a TV disc
				</label>
				<p class="small muted">
					Compares each episode with the play-all title, when the disc has one. Needs ffmpeg.
				</p>
			</section>

			<section class="card stack">
				<h2>Catalogue numbers</h2>
				<p class="small muted">
					Some DVDs are labelled with the publisher's catalogue number. A web search for it finds shop listings;
					the title several of them agree on is searched on TMDB, and its runtime still has to fit.
				</p>
				<label class="field">
					Look catalogue numbers up with
					<select bind:value={f.catalogue_search}>
						<option value="off">Off</option>
						<option value="searxng">SearXNG (self-hosted)</option>
					</select>
				</label>
				{#if f.catalogue_search === 'searxng'}
					<label class="field">SearXNG address <input bind:value={f.searxng_url} placeholder="http://searxng:8080" /></label>
					<p class="small muted">JSON output must be on. Add <code>json</code> to <code>search.formats</code> in its settings.yml.</p>
				{/if}
				<TestButton check="catalogue" values={body} label="Test (looks up EO51710)" />
			</section>

			<section class="card stack">
				<h2>Drives and MakeMKV</h2>
				{@render secret('makemkv_key', 'MakeMKV registration key', view.makemkv_key_set)}
				<TestButton check="makemkv" values={body} label="Test the key" />
				<p class="small muted">
					MakeMKV is paid software; the free beta key is a courtesy while it's in beta. If ripshelf is useful to
					you, please <a href="https://www.makemkv.com/buy/" target="_blank" rel="noreferrer">buy a licence</a>.
					The current beta key is on the <a href="https://forum.makemkv.com/forum/viewtopic.php?t=1053" target="_blank" rel="noreferrer">MakeMKV forum</a>.
				</p>
				<label class="field">
					What to rip
					<select bind:value={f.rip_mode}>
						<option value="titles">Titles, the film (or every episode) as MKV</option>
						<option value="backup">Full disc backup, the whole Blu-ray or UHD, decrypted (BDMV folder)</option>
					</select>
				</label>
				<p class="small muted">
					A full backup keeps menus, extras and every playlist, at the size of the disc (up to ~100 GB for UHD),
					and goes to <span class="mono">{view.disc_backups_dir}</span>. DVDs are ripped as titles either way.
					Each drive can have its own mode below.
				</p>
				<label class="check"><input type="checkbox" bind:checked={f.auto_eject} /> Eject when the rip finishes</label>
				<div class="two">
					<label class="field">Scan stall timeout (s) <input type="number" min="30" max="3600" bind:value={f.scan_timeout_s} /></label>
					<label class="field">Scan time limit (s) <input type="number" min="300" max="43200" bind:value={f.scan_max_s} /></label>
				</div>
				<p class="small muted">
					A scan is stopped only when MakeMKV has said nothing for the stall timeout, so a damaged disc that is
					slowly being read keeps going, up to the time limit.
				</p>
				<div class="two">
					<label class="field">Poll interval (s) <input type="number" min="1" max="60" step="0.5" bind:value={f.poll_interval_s} /></label>
				</div>
				<label class="field">
					UHD-capable drive models (one per line, "VENDOR MODEL")
					<textarea rows="2" bind:value={f.uhd_models}></textarea>
				</label>
				{#if live.drives.length}
					<div class="stack tight">
						<span class="small">Enabled drives</span>
						{#each live.drives as d (d.id)}
							<label class="check">
								<input
									type="checkbox"
									checked={!f.disabled_drives.includes(d.id)}
									onchange={(e) => toggleDrive(d.id, e.currentTarget.checked)}
								/>
								<DriveName drive={d} />
								{#if d.name}<span class="muted">{`${d.vendor} ${d.model}`.replace(/\s+/g, ' ')}</span>{/if}
								<span class="muted mono">{d.device}</span>
								{#if view.makemkv_drive_data[d.id]}<span class="chip">private data</span>{/if}
								{#if view.makemkv_drive_dumps[d.id]}<span class="chip">firmware dump</span>{/if}
								<select
									class="mode"
									aria-label="Rip mode for {d.display_name}"
									value={f.drive_modes[d.id] ?? ''}
									onchange={(e) => setDriveMode(d.id, e.currentTarget.value)}
								>
									<option value="">Default ({f.rip_mode === 'backup' ? 'full backup' : 'titles'})</option>
									<option value="titles">Titles</option>
									<option value="backup">Full backup</option>
								</select>
							</label>
						{/each}
					</div>
				{/if}
				<h3>Folders</h3>
				<label class="field">
					Library
					<input class="mono" bind:value={f.library_dir} required />
				</label>
				<FolderStatus path={f.library_dir} />
				<label class="field">
					Films (leave empty for the Movies folder in the library)
					<input class="mono" bind:value={f.movies_dir} placeholder={`${f.library_dir}${sep}Movies`} />
				</label>
				<FolderStatus path={f.movies_dir} />
				<label class="field">
					TV (leave empty for the TV folder in the library)
					<input class="mono" bind:value={f.tv_dir} placeholder={`${f.library_dir}${sep}TV`} />
				</label>
				<FolderStatus path={f.tv_dir} />
				<label class="field">
					Full disc backups (leave empty for the Disc backups folder in the library)
					<input class="mono" bind:value={f.disc_backups_dir} placeholder={`${f.library_dir}${sep}Disc backups`} />
				</label>
				<FolderStatus path={f.disc_backups_dir} />
				<label class="field">
					Scratch (rips in progress, where a UHD disc needs about 100 GB)
					<input class="mono" bind:value={f.scratch_dir} required />
				</label>
				<FolderStatus path={f.scratch_dir} />
				<p class="small muted">
					Full paths, as this machine sees them. The library folders must already exist ("Create it" makes one
					that doesn't). The scratch folder can't move while jobs are in progress.
				</p>
				<TestButton check="paths" values={body} label="Check the folders are writable" />
				<h3>Database backups</h3>
				<label class="field">
					Daily backups to keep (0 turns them off)
					<input type="number" min="0" max="365" bind:value={f.backup_keep} />
				</label>
				<p class="small muted">
					A copy of the database (jobs, collections and statistics) is made once a day in
					<span class="mono">{view?.backups_dir}</span>. The oldest are deleted once there are more than this.
				</p>
			</section>

			<section class="card stack">
				<h2>Transcoding</h2>
				<label class="check"><input type="checkbox" bind:checked={f.transcode_enabled} /> Transcode after ripping</label>
				<label class="field">
					Video codecs to re-encode to H.265 (ffprobe names, comma separated)
					<input bind:value={f.transcode_codecs} placeholder="vc1" disabled={!f.transcode_enabled} />
				</label>
				<label class="field">
					Crop black bars
					<select bind:value={f.transcode_crop} disabled={!f.transcode_enabled}>
						<option value="off">Never</option>
						<option value="when_transcoding">When transcoding anyway</option>
						<option value="always">Always (re-encodes any letterboxed film)</option>
					</select>
				</label>
				<div class="sources">
					{#each discKinds as [kind, label] (kind)}
						{@const src = f.transcode_sources[kind]}
						{#if src}
							<fieldset class="stack tight" disabled={!f.transcode_enabled}>
								<legend>{label}</legend>
								<label class="check"><input type="checkbox" bind:checked={f.transcode_sources[kind].transcode} /> Transcode</label>
								<label class="check">
									<input type="checkbox" bind:checked={f.transcode_sources[kind].crop} disabled={!src.transcode || f.transcode_crop === 'off'} /> Crop black bars
								</label>
							</fieldset>
						{/if}
					{/each}
				</div>
				<p class="small muted">
					Which discs are transcoded, and which have their black bars cropped (when the setting above crops at all).
					HDR and Dolby Vision video is still left alone unless allowed below, even on a 4K UHD disc that's ticked.
				</p>
				<div class="two">
					<label class="field">
						Encoder
						<select bind:value={f.transcode_encoder} disabled={!f.transcode_enabled}>
							<option value="auto">Auto (GPU if it works)</option>
							<option value="vaapi">GPU (VAAPI)</option>
							<option value="x265">CPU (x265)</option>
						</select>
					</label>
					<label class="field">
						Quality (QP/CRF, lower = better) <input type="number" min="10" max="40" bind:value={f.transcode_quality} disabled={!f.transcode_enabled} />
					</label>
				</div>
				<label class="field">
					Transcodes at once <input type="number" min="1" max="8" bind:value={f.transcode_parallel} disabled={!f.transcode_enabled} />
				</label>
				<p class="small muted">
					How many jobs transcode side by side, whichever drive or worker ripped them; the rest wait their turn.
					One is best for x265, which already uses every CPU core. A GPU can often take two or three, though some
					consumer cards cap how many encodes run at once. A new limit applies to the jobs still waiting.
				</p>
				<label class="check"><input type="checkbox" bind:checked={f.transcode_keep_original} disabled={!f.transcode_enabled} /> Keep the original rip too (“… - original.mkv”)</label>
				<label class="check"><input type="checkbox" bind:checked={f.transcode_hdr} disabled={!f.transcode_enabled} /> Also touch HDR and Dolby Vision sources</label>
				<label class="field">GPU device <input bind:value={f.vaapi_device} disabled={!f.transcode_enabled} /></label>
				<p class="small muted">Audio, subtitles, chapters and metadata are copied untouched. If a transcode fails, the rip goes to the library as it is.</p>
				<HardwarePanel />
			</section>

			<section class="card stack">
				<h2>This browser</h2>
				<label class="check">
					<input type="checkbox" checked={alerts.sound} onchange={(e) => (alerts.sound = e.currentTarget.checked)} /> Play a sound when something needs attention
				</label>
				<p class="small muted">
					A disc waiting for review, a clash, or a failed job. Only while a page is open; browsers stay silent
					until you have clicked somewhere on the page once. Saved in this browser only.
				</p>
				<div><button type="button" onclick={() => alerts.chime()}>Play the sound</button></div>
			</section>

			<section class="card stack">
				<h2>Notifications (Pushover)</h2>
				<label class="field">
					This UI's address from a phone, so every notification links to its job
					<input bind:value={f.public_url} placeholder="http://192.168.1.10:8380" />
				</label>
				{#if !f.public_url.trim()}
					<p class="small error-text">Not set, so notifications will have no link to the job.</p>
				{/if}
				<p class="small muted">Off until both keys are set.</p>
				{@render secret('pushover_user_key', 'User key', view.pushover_user_key_set)}
				{@render secret('pushover_app_token', 'Application API token', view.pushover_app_token_set)}
				<label class="field">Device (optional, else all your devices) <input bind:value={f.pushover_device} /></label>
				<div class="events">
					{#each events as [e, label] (e)}
						<label class="field">
							{label}
							<select value={eventPriority(e)} onchange={(ev) => setEventPriority(e, ev.currentTarget.value)}>
								{#each priorities as [v, plabel] (v)}<option value={v}>{plabel}</option>{/each}
							</select>
						</label>
					{/each}
				</div>
				<label class="field">
					Batch window in seconds. More of the same kind within this time come as one summary (0 sends every one at once)
					<input type="number" min="0" max="3600" bind:value={f.notify_batch_s} />
				</label>
				<TestButton check="pushover" values={body} label="Send a test message" />
			</section>

			{#each servers as srv (srv.id)}
				{@const libs = libraries[srv.id]}
				{@const libAct = libraryActs[srv.id]}
				<section class="card stack">
					<h2>{srv.name}</h2>
					<p class="small muted">
						{srv.name} is told about each new film or episode, so it shows up straight away. Leave the URL empty to skip it.
					</p>
					<label class="field">
						{srv.name} URL <input bind:value={f[`${srv.id}_url`]} placeholder="http://{srv.id}.lan:{srv.port}" />
					</label>
					{#if srv.id === 'plex'}
						{@render secret(srv.key, 'Plex token', view.plex_token_set)}
						<p class="small muted">
							Plex has no API keys. Open any item in Plex Web, choose Get Info → View XML, and copy the X-Plex-Token
							value from the end of the address.
						</p>
					{:else if srv.id === 'jellyfin'}
						{@render secret(srv.key, 'Jellyfin API key', view.jellyfin_api_key_set)}
						<p class="small muted">Make one in Jellyfin under Dashboard → API Keys.</p>
					{:else}
						{@render secret(srv.key, 'Emby API key', view.emby_api_key_set)}
					{/if}
					<div class="row">
						<button type="button" onclick={() => loadLibraries(srv)} disabled={libAct.busy || !f[`${srv.id}_url`].trim()}>
							{libAct.busy ? 'Fetching…' : 'Fetch libraries'}
						</button>
						{#if libAct.error}<span class="small error-text">{libAct.error}</span>{/if}
					</div>
					{#each [['movies', 'Movies', view.movies_dir], ['tv', 'TV', view.tv_dir]] as [kind, label, ours] (kind)}
						{@const k = kind as 'movies' | 'tv'}
						{@const chosen = f[`${srv.id}_${k}_library`]}
						{@const path = f[`${srv.id}_${k}_path`]}
						{@const lib = libs.find((l) => l.id === chosen)}
						<label class="field">
							{label} library
							<select value={chosen} onchange={(e) => pickLibrary(srv.id, k, e.currentTarget.value)}
								disabled={!libs.length && !chosen}>
								<option value="">
									{srv.id === 'plex' ? `None (Plex scans all its libraries)` : `None (don't refresh ${srv.name} for ${label.toLowerCase()})`}
								</option>
								{#if chosen && !lib}
									<option value={chosen}>{path || 'saved library'} (fetch to change)</option>
								{/if}
								{#each libs.filter((l) => fits(l, k)) as l (l.id)}
									<option value={l.id}>{l.name}{l.type ? ` (${l.type})` : ''}</option>
								{/each}
							</select>
						</label>
						{#if lib && lib.locations.length > 1}
							<label class="field">
								Which of its folders is ripshelf's {label.toLowerCase()} folder?
								<select bind:value={f[`${srv.id}_${k}_path`]}>
									{#each lib.locations as loc (loc)}<option value={loc}>{loc}</option>{/each}
								</select>
							</label>
						{/if}
						{#if path}
							<p class="small muted"><span class="mono">{ours}/…</span> → {srv.name} <span class="mono">{path}/…</span></p>
						{/if}
					{/each}
					<TestButton check={srv.id} values={body} label="Test the connection" />
				</section>
			{/each}

			<div class="row save">
				<button class="primary" type="submit" disabled={act.busy}>Save settings</button>
				{#if saved}<span class="small muted">Saved</span>{/if}
				{#if act.error}<span class="small error-text">{act.error}</span>{/if}
			</div>
		</form>

		<form class="card stack" onsubmit={importData}>
			<h2>Import MakeMKV private data</h2>
			<p class="small muted">
				A drive's <span class="mono">_private_data.tar</span> (LibreDrive and per-drive keys) from an existing
				MakeMKV install. Without it some UHD discs fail with “volume key unknown”. Also takes a firmware dump
				(<span class="mono">dump_*.tgz</span>), because older drives such as the LG GGC-H20N hang in MakeMKV 1.17.7 and
				later without one. Remote workers fetch these when they connect.
			</p>
			<label class="field">Upload <input type="file" accept=".tar,.tgz" bind:this={fileInput} /></label>
			<label class="field">…or a path on the server <input bind:value={importPath} placeholder="/path/to/_private_data.tar" /></label>
			<label class="field">
				For
				<select bind:value={importDrives}>
					<option value="all">All drives</option>
					{#each live.drives as d (d.id)}
						<option value={d.id}>{d.display_name} ({d.device})</option>
					{/each}
				</select>
			</label>
			<div class="row">
				<button type="submit" disabled={importAct.busy}>Import</button>
				{#if importNote}<span class="small muted">{importNote}</span>{/if}
				{#if importAct.error}<span class="small error-text">{importAct.error}</span>{/if}
			</div>
			{#if mkFiles.length}
				<h3>Kept for workers</h3>
				{#each mkFiles as mf (mf.sha256 + mf.name)}
					<div class="spread">
						<div class="small">
							<span class="mono">{mf.name}</span> · {kb(mf.size)} ·
							{mf.drives ? mf.drives.map(driveLabel).join(', ') : 'all drives'}
							<div class="mono muted" title="sha256">{mf.sha256}</div>
						</div>
						<button type="button" onclick={() => deleteFile(mf)} disabled={filesAct.busy}>Delete</button>
					</div>
				{/each}
			{/if}
			{#if filesAct.error}<span class="small error-text">{filesAct.error}</span>{/if}
		</form>

		<RemoteWorkers />

		<section class="card stack about">
			<h2>About</h2>
			<p class="small muted">
				ripshelf {live.status?.version ?? ''}{#if live.status?.makemkv_version}
					{` · MakeMKV ${live.status.makemkv_version}`}{/if}
			</p>
			{#if inUse.has('tmdb')}
				<p class="small">
					<a href="https://www.themoviedb.org" target="_blank" rel="noopener"><img class="tmdb" src="/credits/tmdb.svg" alt="TMDB" /></a>
					This product uses the TMDB API but is not endorsed or certified by TMDB.
				</p>
			{/if}
			{#if inUse.has('tvdb')}
				<p class="small">
					Metadata provided by <a href="https://thetvdb.com" target="_blank" rel="noopener">TheTVDB</a>. Please consider
					adding missing information or <a href="https://thetvdb.com/subscribe" target="_blank" rel="noopener">subscribing</a>.
				</p>
			{/if}
			{#if inUse.has('tvmaze')}
				<p class="small">TV data from <a href="https://www.tvmaze.com" target="_blank" rel="noopener">TVmaze</a>, licensed CC BY-SA 4.0.</p>
			{/if}
			{#if inUse.has('omdb')}
				<p class="small">Film data from <a href="https://www.omdbapi.com" target="_blank" rel="noopener">OMDb</a>, licensed CC BY-NC 4.0.</p>
			{/if}
			{#if inUse.has('thediscdb')}
				<p class="small">Disc contents from <a href="https://thediscdb.com" target="_blank" rel="noopener">TheDiscDB</a>.</p>
			{/if}
			<p class="small muted">
				Not affiliated with MakeMKV or any of these services. The software ripshelf includes and its licences are
				listed in THIRD-PARTY-NOTICES.md.
			</p>
		</section>
	{:else if act.error}
		<p class="error-text">{act.error}</p>
	{/if}
</div>

<style>
	.page {
		max-width: 680px;
	}
	.about .tmdb {
		height: 10px;
		width: auto;
		margin-right: 6px;
		vertical-align: baseline;
	}
	.events {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
		gap: 10px;
	}
	.sources {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 10px;
	}
	.sources fieldset {
		margin: 0;
		padding: 8px 10px;
		border: 1px solid var(--border);
		border-radius: 9px;
	}
	.sources legend {
		padding: 0 4px;
	}
	.two {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 10px;
	}
	.nowrap {
		flex-wrap: nowrap;
	}
	.tight {
		gap: 6px;
	}
	.mode {
		width: auto;
		margin-left: auto;
	}
	.save {
		position: sticky;
		bottom: calc(64px + env(safe-area-inset-bottom));
		background: var(--bg);
		padding: 8px 0;
	}
	@media (min-width: 760px) {
		.save {
			bottom: 0;
		}
	}
	p {
		margin: 0;
	}
	h3 {
		margin: 8px 0 0;
		font-size: 1rem;
	}
</style>
