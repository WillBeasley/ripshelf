<script lang="ts">
	// Settings: When a disc is confirmed without review, and the TV checks.
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const view = $derived(form.view!);
</script>

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

<style>
	p {
		margin: 0;
	}
</style>
