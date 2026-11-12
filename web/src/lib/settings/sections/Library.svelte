<script lang="ts">
	// Settings → Library and filing: where rips go, and how extras are filed.
	import FolderStatus from '#lib/components/FolderStatus.svelte';
	import TestButton from '#lib/components/TestButton.svelte';
	import Advanced from '../Advanced.svelte';
	import Field from '../Field.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const view = $derived(form.view!);
	// The path separator this machine's folders use (for the placeholders).
	const sep = $derived(view.library_dir.includes('\\') ? '\\' : '/');
</script>

<section class="card stack" aria-labelledby="folders">
	<h2 id="folders">Folders</h2>
	<p class="small muted">Full paths, as this machine sees them. Library folders must already exist ("Create it" makes one).</p>
	<Field {form} name="library_dir"><FolderStatus path={f.library_dir} /></Field>
	<Field {form} name="movies_dir" placeholder={`${f.library_dir}${sep}Movies`}><FolderStatus path={f.movies_dir} /></Field>
	<Field {form} name="tv_dir" placeholder={`${f.library_dir}${sep}TV`}><FolderStatus path={f.tv_dir} /></Field>
	<Advanced>
		<Field {form} name="scratch_dir" help="Rips in progress; a UHD disc needs about 100 GB. Can't move while jobs run.">
			<FolderStatus path={f.scratch_dir} />
		</Field>
		<Field {form} name="failed_scratch_days" />
		<Field {form} name="disc_backups_dir" placeholder={`${f.library_dir}${sep}Disc backups`}>
			<FolderStatus path={f.disc_backups_dir} />
		</Field>
	</Advanced>
	<TestButton check="paths" values={form.body} label="Check the folders are writable" />
</section>

<section class="card stack" aria-labelledby="filing">
	<h2 id="filing">TV extras</h2>
	<Field {form} name="tv_rip_extras" />
	<Field {form} name="tv_unmatched" />
	<Field {form} name="tv_bonus_episodes">
		{#snippet more()}
			When a TV disc's box lists a bonus episode of a different show ("Includes bonus episode", "Plus meet…"), it's
			found in that show's episode list and ripped. As an extra it goes in the show's
			<span class="mono">extras</span> folder; in its own show, as
			<span class="mono">Other show (year)/Season 01/Other show - S01E24 - Episode.mkv</span>.
		{/snippet}
	</Field>
</section>

<section class="card stack" aria-labelledby="named-extras">
	<h2 id="named-extras">Extras the box names</h2>
	<Field {form} name="extras_emby_folders">
		{#snippet more()}
			An extra the cover scan matches to a title (with more than low confidence) is filed under its name in the folder
			for its type: featurettes, behind the scenes, deleted scenes, interviews, trailers, scenes, shorts or extras,
			beside the film or in the show's folder. A film's are ripped with it.
		{/snippet}
	</Field>
	<Field {form} name="film_unnamed_extras" />
</section>

<style>
	p {
		margin: 0;
	}
</style>
