<script lang="ts">
	// Settings → System: this UI's address, database backups, and about ripshelf.
	import Field from '../Field.svelte';
	import About from './About.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const view = $derived(form.view!);
</script>

<section class="card stack" aria-labelledby="system">
	<h2 id="system">This machine</h2>
	<Field {form} name="public_url">
		{#if !form.f.public_url.trim()}
			<p class="small warn-note">Not set, so notifications have no link to their job.</p>
		{/if}
	</Field>
	<Field {form} name="backup_keep">
		{#snippet more()}
			A copy of the database (jobs, collections and statistics) is made once a day in
			<span class="mono">{view.backups_dir}</span>. The oldest are deleted once there are more than this.
		{/snippet}
	</Field>
	<p class="small"><a href="/setup">Run the setup again</a> · <a href="/logs">ripshelf log →</a></p>
</section>

<About {form} />

<style>
	p {
		margin: 0;
	}
	.warn-note {
		color: var(--warn);
	}
	.mono {
		overflow-wrap: anywhere;
	}
</style>
