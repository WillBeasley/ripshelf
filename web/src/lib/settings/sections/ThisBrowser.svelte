<script lang="ts">
	// Settings: Settings kept in this browser only.
	import { alerts } from '#lib/alerts.svelte.ts';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const view = $derived(form.view!);
</script>

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

<style>
	p {
		margin: 0;
	}
</style>
