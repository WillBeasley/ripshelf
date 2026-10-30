<script lang="ts">
	import type { NewRemoteWorker, RemoteWorkers } from '$lib/api-types';
	import { api } from '$lib/api';
	import { Action } from '$lib/actions.svelte';
	import { when } from '$lib/format';

	const act = new Action();
	let list = $state<RemoteWorkers | null>(null);
	let name = $state('');
	let controllerUrl = $state('');
	let created = $state<NewRemoteWorker | null>(null);
	let copied = $state(false);

	async function load() {
		list = (await act.run(() => api.remoteWorkers())) ?? list;
	}
	$effect(() => {
		load();
	});

	async function add(e: SubmitEvent) {
		e.preventDefault();
		copied = false;
		const r = await act.run(() =>
			api.addRemoteWorker({ name: name.trim(), controller_url: controllerUrl.trim() || null })
		);
		if (r) {
			created = r;
			name = '';
			await load();
		}
	}

	async function revoke(id: number, label: string) {
		if (!confirm(`Revoke the token of ${label}? The worker is refused from then on.`)) return;
		await act.run(() => api.revokeRemoteWorker(id));
		if (created?.worker.id === id) created = null;
		await load();
	}

	async function copy() {
		if (!created) return;
		try {
			await navigator.clipboard.writeText(created.compose);
			copied = true;
		} catch {
			act.error = 'Copying needs https or localhost, so select the text and copy it instead';
		}
	}
</script>

<section class="card stack">
	<h2>Remote workers</h2>
	<p class="small muted">
		A worker runs MakeMKV on another machine's drives (docs/workers.md). Each gets its own token. The
		MakeMKV key, private data and firmware dumps are fetched from here when it connects. Without
		https (a reverse proxy, or a VPN such as WireGuard or Tailscale) they cross the network unencrypted.
	</p>

	{#if list}
		{#each list.workers as w (w.id)}
			<div class="spread">
				<div>
					<strong>{w.name}</strong>
					<span class="small muted mono">{w.worker_id}</span>
					{#if w.online}<span class="chip">online</span>{/if}
					<div class="small muted">
						added {when(w.created_at)} · last seen {w.last_seen_at ? when(w.last_seen_at) : 'never'}
					</div>
				</div>
				<button type="button" onclick={() => revoke(w.id, w.name)} disabled={act.busy}>Revoke</button>
			</div>
		{:else}
			<p class="small muted">None added yet.</p>
		{/each}
		{#if list.shared_token}
			<p class="small muted">
				A shared token (RIPSHELF_WORKER_TOKEN or the token file) is set too, and works for any worker,
				such as the per-drive workers on this host. For workers on other machines, prefer one token each.
			</p>
		{/if}
	{/if}

	<form class="stack" onsubmit={add}>
		<label class="field">Name <input bind:value={name} placeholder="Den PC" maxlength="60" required /></label>
		<label class="field">
			Controller address, as the worker reaches it (optional)
			<input bind:value={controllerUrl} placeholder="defaults to the public URL setting, else this page's address" />
		</label>
		<div class="row">
			<button type="submit" disabled={act.busy || !name.trim()}>Add remote worker</button>
			{#if act.error}<span class="small error-text">{act.error}</span>{/if}
		</div>
	</form>

	{#if created}
		<div class="stack new">
			<p class="small">
				<strong>Token for {created.worker.name}, shown only now:</strong>
				<span class="mono">{created.token}</span>
			</p>
			<p class="small muted">
				Save this as <span class="mono">compose.yaml</span> on the worker, change the lines marked
				&lt;--, then <span class="mono">docker compose up -d</span>.
			</p>
			<pre class="mono">{created.compose}</pre>
			<div class="row">
				<button type="button" onclick={copy}>Copy</button>
				<button type="button" onclick={() => (created = null)}>Done</button>
				{#if copied}<span class="small muted">Copied</span>{/if}
			</div>
		</div>
	{/if}
</section>

<style>
	p {
		margin: 0;
	}
	pre {
		margin: 0;
		padding: 10px;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		overflow-x: auto;
		white-space: pre;
		max-width: 100%;
	}
	.new {
		border-top: 1px solid var(--border);
		padding-top: 10px;
		min-width: 0;
	}
</style>
