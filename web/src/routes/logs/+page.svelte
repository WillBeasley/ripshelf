<script lang="ts">
	// ripshelf's own log (not a job's MakeMKV log), newest at the bottom, followed live.
	import type { LogEntry } from '$lib/api-types';
	import { api } from '$lib/api';
	import { when } from '$lib/format';

	const MAX = 3000;
	let level = $state('INFO');
	let entries = $state<LogEntry[]>([]);
	let last = 0;
	let paused = $state(false);
	let follow = $state(true);
	let file = $state<string | null>(null);
	let error = $state<string | null>(null);
	let box = $state<HTMLElement | null>(null);

	async function poll() {
		if (paused) return;
		try {
			const page = await api.logs(last, level);
			file = page.file ?? null;
			error = null;
			if (page.entries.length) {
				entries = [...entries, ...page.entries].slice(-MAX);
				last = page.last_seq;
				if (follow) queueMicrotask(() => box?.scrollTo({ top: box.scrollHeight }));
			} else {
				last = page.last_seq;
			}
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	$effect(() => {
		level;
		entries = [];
		last = 0;
		poll();
		const t = setInterval(poll, 2000);
		return () => clearInterval(t);
	});

	function onScroll() {
		if (box) follow = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
	}
</script>

<svelte:head><title>Log · ripshelf</title></svelte:head>

<div class="stack page">
	<div class="spread">
		<h1>ripshelf log</h1>
		<a class="small" href="/settings">← Settings</a>
	</div>
	<div class="row">
		<label class="field inline">
			Show
			<select bind:value={level}>
				<option value="DEBUG">Everything</option>
				<option value="INFO">Info and above</option>
				<option value="WARNING">Warnings and errors</option>
				<option value="ERROR">Errors only</option>
			</select>
		</label>
		<button onclick={() => (paused = !paused)}>{paused ? '▶ Resume' : '❚❚ Pause'}</button>
		{#if file}<a class="button" href="/api/logs/file" download>Download log file</a>{/if}
	</div>
	<p class="small muted">
		The newest lines since ripshelf started{file ? `; the full log is in ${file} (rotated at 5 MB)` : ''}. A job's
		MakeMKV output is in its own panel.
	</p>
	{#if error}<p class="small error-text">{error}</p>{/if}
	<div class="log" bind:this={box} onscroll={onScroll}>
		{#each entries as e (e.seq)}
			<div class="line {e.level.toLowerCase()}">
				<time>{when(e.ts)}</time>
				<span class="lvl">{e.level}</span>
				<span class="src">{e.logger}</span>
				<span class="msg">{e.message}</span>
			</div>
		{:else}
			<p class="small muted">Nothing at this level yet.</p>
		{/each}
	</div>
	{#if !follow}<button class="small" onclick={() => { follow = true; box?.scrollTo({ top: box.scrollHeight }); }}>↓ Jump to the newest</button>{/if}
</div>

<style>
	.page {
		max-width: 1100px;
	}
	.inline {
		flex-direction: row;
		align-items: center;
		gap: 8px;
	}
	.log {
		font-family: var(--mono, ui-monospace, monospace);
		font-size: 0.78rem;
		line-height: 1.45;
		background: var(--card, var(--bg));
		border: 1px solid var(--border);
		border-radius: 9px;
		padding: 8px 10px;
		height: min(70vh, 900px);
		overflow: auto;
	}
	.line {
		display: flex;
		gap: 8px;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	time,
	.src {
		color: var(--muted);
		flex: none;
	}
	.lvl {
		flex: none;
		width: 4.5em;
	}
	.msg {
		min-width: 0;
	}
	.warning .lvl,
	.warning .msg {
		color: var(--warn, #b45309);
	}
	.error .lvl,
	.error .msg,
	.critical .msg {
		color: var(--bad, #b91c1c);
	}
	@media (max-width: 600px) {
		.src {
			display: none;
		}
	}
</style>
