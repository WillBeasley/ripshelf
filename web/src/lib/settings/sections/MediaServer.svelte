<script lang="ts">
	// Settings: a media server told about new files, and the libraries films and TV go into.
	import type { ServerLibrary } from '#lib/api-types.ts';
	import { api, type MediaServer } from '#lib/api.ts';
	import { Action } from '#lib/actions.svelte.ts';
	import TestButton from '#lib/components/TestButton.svelte';
	import Field from '../Field.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	export type Server = { id: MediaServer; name: string; key: 'emby_api_key' | 'jellyfin_api_key' | 'plex_token'; port: number };
	let { form, srv }: { form: SettingsForm; srv: Server } = $props();
	const f = $derived(form.f);
	const view = $derived(form.view!);

	const libAct = new Action();
	let libs = $state<ServerLibrary[]>([]);
	async function loadLibraries() {
		const key = form.secrets[srv.key]?.trim() || null;
		const url = f[`${srv.id}_url`].trim();
		libs = (await libAct.run(() => api.serverLibraries(srv.id, url, key))) ?? [];
	}
	const fits = (l: ServerLibrary, k: 'movies' | 'tv') =>
		!l.type || l.type === 'mixed' || l.type === (k === 'movies' ? 'movies' : 'tvshows');
	function pickLibrary(k: 'movies' | 'tv', id: string) {
		const lib = libs.find((l) => l.id === id);
		f[`${srv.id}_${k}_library`] = id;
		f[`${srv.id}_${k}_path`] = lib?.locations[0] ?? '';
	}
</script>

<section class="card stack" aria-label={srv.name}>
	<p class="small muted">
		{srv.name} is told about each new film or episode, so it shows up straight away. Leave the URL empty to skip it.
	</p>
	<Field {form} name="{srv.id}_url" placeholder="http://{srv.id}.lan:{srv.port}" />
	{#if srv.id === 'plex'}
		<Field {form} name={srv.key} help="Plex has no API keys; the token is in Plex Web.">
			{#snippet more()}
				Open any item in Plex Web, choose Get Info → View XML, and copy the X-Plex-Token value from the end of the
				address.
			{/snippet}
		</Field>
	{:else if srv.id === 'jellyfin'}
		<Field {form} name={srv.key} help="Make one in Jellyfin under Dashboard → API Keys." />
	{:else}
		<Field {form} name={srv.key} help="Make one in Emby under Settings → Advanced → API Keys." />
	{/if}
	<div class="row">
		<button type="button" onclick={() => loadLibraries()} disabled={libAct.busy || !f[`${srv.id}_url`].trim()}>
			{libAct.busy ? 'Fetching…' : 'Fetch libraries'}
		</button>
		{#if libAct.error}<span class="small error-text">{libAct.error}</span>
		{:else if libs.length}<span class="small ok">✓ {libs.length} libraries</span>{/if}
	</div>
	{#each [['movies', 'Movies', view.movies_dir], ['tv', 'TV', view.tv_dir]] as [kind, label, ours] (kind)}
		{@const k = kind as 'movies' | 'tv'}
		{@const chosen = f[`${srv.id}_${k}_library`]}
		{@const path = f[`${srv.id}_${k}_path`]}
		{@const lib = libs.find((l) => l.id === chosen)}
		<label class="field" id="setting-{srv.id}_{k}_library">
			{label} library
			<select value={chosen} onchange={(e) => pickLibrary(k, e.currentTarget.value)}
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
	<TestButton check={srv.id} values={form.body} label="Test the connection" />
</section>

<style>
	p {
		margin: 0;
	}
	.ok {
		color: var(--ok);
	}
	.mono {
		overflow-wrap: anywhere;
	}
</style>
