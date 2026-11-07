<script lang="ts">
	// Settings → Drives and ripping: MakeMKV's key, what's ripped, each drive (grouped by the
	// machine it's on), and MakeMKV's private data.
	import TestButton from '#lib/components/TestButton.svelte';
	import Advanced from '../Advanced.svelte';
	import Field from '../Field.svelte';
	import DriveList from '../DriveList.svelte';
	import PrivateData from './PrivateData.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const view = $derived(form.view!);
</script>

<section class="card stack" aria-labelledby="makemkv">
	<h2 id="makemkv">MakeMKV</h2>
	<Field {form} name="makemkv_key" />
	<p class="small muted">
		MakeMKV is paid software; the free beta key is a courtesy while it's in beta.
		<a href="https://www.makemkv.com/buy/" target="_blank" rel="noreferrer">Buy a licence</a> ·
		<a href="https://forum.makemkv.com/forum/viewtopic.php?t=1053" target="_blank" rel="noreferrer">current beta key</a>
	</p>
	<TestButton check="makemkv" values={form.body} label="Test the key" />
	<Field {form} name="rip_mode">
		{#snippet more()}
			A full backup keeps menus, extras and every playlist, at the size of the disc (up to ~100 GB for UHD), and
			goes to <span class="mono">{view.disc_backups_dir}</span>.
		{/snippet}
	</Field>
	<Field {form} name="auto_eject" />
	<Advanced>
		<div class="two">
			<Field {form} name="scan_timeout_s" />
			<Field {form} name="scan_max_s" />
		</div>
		<Field {form} name="poll_interval_s" />
		<Field {form} name="uhd_models" multiline />
	</Advanced>
</section>

<section class="card stack" aria-labelledby="drives">
	<h2 id="drives">Drives</h2>
	<DriveList {form} />
</section>

<PrivateData {form} />

<style>
	p {
		margin: 0;
	}
	.two {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 10px;
	}
	@media (max-width: 480px) {
		.two {
			grid-template-columns: 1fr;
		}
	}
</style>
