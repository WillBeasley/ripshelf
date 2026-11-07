<script lang="ts">
	// Settings → Media servers: Emby, Jellyfin and Plex, one at a time.
	import MediaServer, { type Server } from './MediaServer.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();

	const servers: Server[] = [
		{ id: 'emby', name: 'Emby', key: 'emby_api_key', port: 8096 },
		{ id: 'jellyfin', name: 'Jellyfin', key: 'jellyfin_api_key', port: 8096 },
		{ id: 'plex', name: 'Plex', key: 'plex_token', port: 32400 }
	];
	const set = (s: Server) => Boolean(form.view?.[`${s.id}_url`]);
	const touched = (s: Server) => form.changed(form.names('media-servers')).some((n) => n.startsWith(`${s.id}_`) || n === s.key);
	// The first one set up (else Emby). Settings search clicks a tab (data-tab) to show a field.
	let shown = $state(servers.find(set)?.id ?? 'emby');
</script>

<div class="switch" role="tablist" aria-label="Media server">
	{#each servers as s (s.id)}
		<button type="button" role="tab" data-tab={s.id} aria-selected={shown === s.id} class:on={shown === s.id} onclick={() => (shown = s.id)}>
			{s.name}
			{#if touched(s)}<span class="unsaved" title="Not saved yet">●</span>
			{:else if set(s)}<span class="dot" title="Set up"></span>{/if}
		</button>
	{/each}
</div>
{#each servers as s (s.id)}
	<div hidden={shown !== s.id} role="tabpanel" data-server={s.id}>
		<MediaServer {form} srv={s} />
	</div>
{/each}

<style>
	.switch {
		display: flex;
		gap: 4px;
		padding: 4px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 12px;
	}
	.switch button {
		flex: 1;
		justify-content: center;
		background: none;
		border: none;
		min-height: 40px;
	}
	.switch button.on {
		background: var(--surface-2);
		font-weight: 600;
	}
	.dot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--ok);
	}
	.unsaved {
		color: var(--warn);
		font-size: 0.7rem;
	}
</style>
