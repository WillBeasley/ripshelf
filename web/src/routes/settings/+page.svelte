<script lang="ts">
	import SetupProblems from '#lib/components/SetupProblems.svelte';
	import RemoteWorkers from '#lib/components/RemoteWorkers.svelte';
	import { SettingsForm } from '#lib/settings/form.svelte.ts';
	import Providers from '#lib/settings/sections/Providers.svelte';
	import Identification from '#lib/settings/sections/Identification.svelte';
	import WebSearch from '#lib/settings/sections/WebSearch.svelte';
	import Drives from '#lib/settings/sections/Drives.svelte';
	import Scanner from '#lib/settings/sections/Scanner.svelte';
	import Transcoding from '#lib/settings/sections/Transcoding.svelte';
	import ThisBrowser from '#lib/settings/sections/ThisBrowser.svelte';
	import Notifications from '#lib/settings/sections/Notifications.svelte';
	import MediaServer, { type Server } from '#lib/settings/sections/MediaServer.svelte';
	import PrivateData from '#lib/settings/sections/PrivateData.svelte';
	import About from '#lib/settings/sections/About.svelte';

	const form = new SettingsForm();
	$effect(() => {
		form.load();
	});

	// Media servers told about new files, and their libraries, to pick which one films and TV go into.
	const servers: Server[] = [
		{ id: 'emby', name: 'Emby', key: 'emby_api_key', port: 8096 },
		{ id: 'jellyfin', name: 'Jellyfin', key: 'jellyfin_api_key', port: 8096 },
		{ id: 'plex', name: 'Plex', key: 'plex_token', port: 32400 }
	];

	function save(e: SubmitEvent) {
		e.preventDefault();
		form.save();
	}
</script>

<svelte:head><title>Settings · ripshelf</title></svelte:head>

<div class="stack page settings">
	<div class="spread">
		<h1>Settings</h1>
		<span class="small"><a href="/setup">Run the setup again</a> · <a href="/logs">ripshelf log →</a></span>
	</div>
	<SetupProblems />

	{#if form.ready}
		<form class="stack" onsubmit={save}>
			<Providers {form} />
			<Identification {form} />
			<WebSearch {form} />
			<Drives {form} />
			<Scanner {form} />
			<Transcoding {form} />
			<ThisBrowser {form} />
			<Notifications {form} />
			{#each servers as srv (srv.id)}
				<MediaServer {form} {srv} />
			{/each}

			<div class="row save">
				<button class="primary" type="submit" disabled={form.act.busy}>Save settings</button>
				{#if form.saved}<span class="small muted">Saved</span>{/if}
				{#if form.act.error}<span class="small error-text">{form.act.error}</span>{/if}
			</div>
		</form>

		<PrivateData {form} />

		<RemoteWorkers />

		<About {form} />
	{:else if form.act.error}
		<p class="error-text">{form.act.error}</p>
	{/if}
</div>

<style>
	.page {
		max-width: 680px;
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
</style>
