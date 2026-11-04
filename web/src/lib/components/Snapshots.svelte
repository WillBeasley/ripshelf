<script lang="ts">
	// Frames from the disc's ripped titles, to help tell what the disc is. They are
	// made on the server when first asked for, so nothing loads until this is opened.
	import type { Job, Snapshots } from '../api-types';
	import { api } from '../api';
	import { duration, percent } from '../format';

	let { job, open = false }: { job: Job; open?: boolean } = $props();

	let shown = $state(false);
	let data = $state<Snapshots | null>(null);
	let error = $state<string | null>(null);
	let big = $state<{ src: string; caption: string } | null>(null);
	let failed = $state<Record<string, boolean>>({});
	let dialog = $state<HTMLDialogElement | null>(null);

	const ripping = $derived(job.rip.state === 'running');

	async function load() {
		try {
			data = await api.snapshots(job.id);
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	$effect(() => {
		if (open) shown = true;
	});

	$effect(() => {
		if (!shown) return;
		job.id;
		job.rip.state;
		load();
		// More of the title is written as the rip goes on, so more frames become possible.
		if (!ripping) return;
		const t = setInterval(load, 20000);
		return () => clearInterval(t);
	});

	function zoom(src: string, caption: string) {
		big = { src, caption };
		dialog?.showModal();
	}
</script>

<details bind:open={shown}>
	<summary class="small">Snapshots from the disc</summary>
	<div class="stack body">
		{#if error}
			<p class="small error-text">{error}</p>
		{:else if !data}
			<p class="small muted">Loading…</p>
		{:else if !data.titles.length}
			<p class="small muted">{data.note}</p>
		{:else}
			{#if job.media === 'movie' && job.mode !== 'backup'}
				<p class="small muted">Only the main feature is ripped for a film, so the frames come from it.</p>
			{/if}
			{#each data.titles as t (t.title_index)}
				<div class="title">
					<div class="small">
						<strong>Title {t.title_index}</strong>
						<span class="muted">
							{duration(t.duration_s)}{t.ripped < 1 ? ` · ${percent(t.ripped)} ripped so far` : ''}
						</span>
					</div>
					<div class="frames">
						{#each t.frames as f, i (f)}
							{@const src = api.snapshotUrl(job.id, t.title_index, f)}
							{@const key = `${t.title_index}-${f}`}
							{@const caption = `Title ${t.title_index} at ${duration(t.at_s[i])}`}
							{#if !failed[key]}
								<button class="frame" title={caption} onclick={() => zoom(src, caption)}>
									<img {src} alt={caption} loading="lazy" onerror={() => (failed[key] = true)} />
									<span class="at small">{duration(t.at_s[i])}</span>
								</button>
							{/if}
						{/each}
					</div>
				</div>
			{/each}
			{#if ripping}
				<p class="small muted">More frames appear as the rip goes on.</p>
			{/if}
		{/if}
	</div>
</details>

<dialog bind:this={dialog} onclick={() => dialog?.close()} onclose={() => (big = null)}>
	{#if big}
		<img src={big.src} alt={big.caption} />
		<p class="small muted">{big.caption}</p>
	{/if}
</dialog>

<style>
	.body {
		gap: 12px;
		margin-top: 8px;
	}
	.title {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.frames {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
		gap: 6px;
	}
	.frame {
		position: relative;
		padding: 0;
		border: 0;
		background: var(--surface-2);
		border-radius: 6px;
		overflow: hidden;
		aspect-ratio: 16 / 9;
		cursor: zoom-in;
	}
	.frame img {
		width: 100%;
		height: 100%;
		object-fit: cover;
		display: block;
	}
	.at {
		position: absolute;
		right: 4px;
		bottom: 3px;
		padding: 0 4px;
		border-radius: 4px;
		background: rgb(0 0 0 / 0.65);
		color: #fff;
	}
	dialog {
		padding: 8px;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
		color: var(--text);
		max-width: min(96vw, 1000px);
	}
	dialog::backdrop {
		background: rgb(0 0 0 / 0.7);
	}
	dialog img {
		display: block;
		width: 100%;
		height: auto;
		border-radius: 6px;
	}
	dialog p {
		margin: 6px 0 0;
	}
	p {
		margin: 0;
	}
</style>
