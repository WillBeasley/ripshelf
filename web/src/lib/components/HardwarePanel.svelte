<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '../api';
	import type { Hardware } from '../api-types';

	// Bindable, so Settings can offer the GPUs found here.
	let { hw = $bindable(null) }: { hw?: Hardware | null } = $props();
	let busy = $state(false);
	let error = $state<string | null>(null);
	let showAll = $state(false);

	async function load(refresh = false) {
		busy = true;
		try {
			hw = await api.hardware(refresh);
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
		}
	}

	onMount(() => {
		load();
	});

	const working = $derived(hw?.encoders.filter((e) => e.works) ?? []);
	const others = $derived(hw?.encoders.filter((e) => !e.works) ?? []);
	const CODEC: Record<string, string> = { hevc: 'H.265', h264: 'H.264', av1: 'AV1' };
</script>

<div class="stack hw">
	<div class="spread">
		<h3>Hardware on this machine</h3>
		<button type="button" onclick={() => load(true)} disabled={busy}>{busy ? 'Checking…' : '↻ Check again'}</button>
	</div>
	{#if error}<p class="small error-text">{error}</p>{/if}
	{#if hw}
		<p class="small">
			Transcoding now uses <strong>{hw.encoder_note}</strong>
		</p>
		{#if hw.devices.length}
			<ul class="small">
				{#each hw.devices as d (d.path)}
					<li>
						<code>{d.path}</code>
						{[d.vendor, d.model, d.driver && `driver ${d.driver}`].filter(Boolean).join(' · ')}
						{#if !d.accessible}<span class="error-text"> · no permission (add the container to its group)</span>{/if}
					</li>
				{/each}
			</ul>
		{:else}
			<p class="small muted">No GPU found. Pass <code>/dev/dri</code> (Intel/AMD) or an NVIDIA GPU into the container to use one.</p>
		{/if}
		{#if !hw.ffmpeg_found}
			<p class="small error-text">{hw.encoder_note}</p>
		{:else if working.length}
			<div class="encoders">
				{#each working as e (e.name + e.device)}
					<span class="chip ok" title={e.description}>
						{CODEC[e.codec] ?? e.codec} · {e.method}{e.ten_bit ? ' · 10-bit' : ''}{e.used_by_ripshelf ? ' · used' : ''}
					</span>
				{/each}
			</div>
		{:else}
			<p class="small muted">No hardware encoder works here, so transcodes run on the CPU.</p>
		{/if}
		{#if others.length}
			<button type="button" class="link small" onclick={() => (showAll = !showAll)}>
				{showAll ? 'Hide' : 'Show'} {others.length} that ffmpeg has but don't work here
			</button>
			{#if showAll}
				<ul class="small muted">
					{#each others as e (e.name + e.device)}
						<li><code>{e.name}</code>{e.device ? ` on ${e.device}` : ''}: {e.error ?? 'failed'}</li>
					{/each}
				</ul>
			{/if}
		{/if}
		<p class="small muted">ripshelf encodes H.265 with VAAPI (Intel/AMD) or x265 on the CPU; other methods are listed for information.</p>
	{:else if !error}
		<p class="small muted">Testing the encoders…</p>
	{/if}
</div>

<style>
	.hw {
		border-top: 1px solid var(--border);
		padding-top: 12px;
	}
	h3 {
		margin: 0;
		font-size: 1rem;
	}
	.encoders {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.chip.ok {
		background: var(--ok-bg);
		color: var(--ok);
		border-color: transparent;
		text-transform: none;
	}
	ul {
		margin: 0;
		padding-left: 18px;
	}
	code {
		overflow-wrap: anywhere;
	}
</style>
