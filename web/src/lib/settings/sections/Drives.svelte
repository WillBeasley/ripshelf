<script lang="ts">
	// Settings: MakeMKV, the drives, the folders and database backups.
	type RipMode = 'titles' | 'backup';
	import { live } from '#lib/live.svelte.ts';
	import DriveName from '#lib/components/DriveName.svelte';
	import FolderStatus from '#lib/components/FolderStatus.svelte';
	import TestButton from '#lib/components/TestButton.svelte';
	import ListInput from '../ListInput.svelte';
	import SecretField from '../SecretField.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const view = $derived(form.view!);

	// The path separator this machine's folders use (for the placeholders).
	const sep = $derived(view.library_dir.includes('\\') ? '\\' : '/');

	// '' follows the default rip mode; otherwise the drive's own.
	function setDriveMode(id: string, mode: string) {
		const { [id]: _, ...rest } = f.drive_modes;
		f.drive_modes = mode ? { ...rest, [id]: mode as RipMode } : rest;
	}

	function toggleDrive(id: string, on: boolean) {
		f.disabled_drives = on ? f.disabled_drives.filter((d) => d !== id) : [...f.disabled_drives, id];
	}
</script>

<section class="card stack">
	<h2>Drives and MakeMKV</h2>
	<SecretField {form} name="makemkv_key" label={'MakeMKV registration key'} />
	<TestButton check="makemkv" values={form.body} label="Test the key" />
	<p class="small muted">
		MakeMKV is paid software; the free beta key is a courtesy while it's in beta. If ripshelf is useful to
		you, please <a href="https://www.makemkv.com/buy/" target="_blank" rel="noreferrer">buy a licence</a>.
		The current beta key is on the <a href="https://forum.makemkv.com/forum/viewtopic.php?t=1053" target="_blank" rel="noreferrer">MakeMKV forum</a>.
	</p>
	<label class="field">
		What to rip
		<select bind:value={f.rip_mode}>
			<option value="titles">Titles, the film (or every episode) as MKV</option>
			<option value="backup">Full disc backup, the whole Blu-ray or UHD, decrypted (BDMV folder)</option>
		</select>
	</label>
	<p class="small muted">
		A full backup keeps menus, extras and every playlist, at the size of the disc (up to ~100 GB for UHD),
		and goes to <span class="mono">{view.disc_backups_dir}</span>. DVDs are ripped as titles either way.
		Each drive can have its own mode below.
	</p>
	<label class="check"><input type="checkbox" bind:checked={f.auto_eject} /> Eject when the rip finishes</label>
	<div class="two">
		<label class="field">Scan stall timeout (s) <input type="number" min="30" max="3600" bind:value={f.scan_timeout_s} /></label>
		<label class="field">Scan time limit (s) <input type="number" min="300" max="43200" bind:value={f.scan_max_s} /></label>
	</div>
	<p class="small muted">
		A scan is stopped only when MakeMKV has said nothing for the stall timeout, so a damaged disc that is
		slowly being read keeps going, up to the time limit.
	</p>
	<div class="two">
		<label class="field">Poll interval (s) <input type="number" min="1" max="60" step="0.5" bind:value={f.poll_interval_s} /></label>
	</div>
	<label class="field">
		UHD-capable drive models (one per line, "VENDOR MODEL")
		<ListInput multiline bind:value={f.uhd_models} />
	</label>
	{#if live.drives.length}
		<div class="stack tight">
			<span class="small">Enabled drives</span>
			{#each live.drives as d (d.id)}
				<label class="check">
					<input
						type="checkbox"
						checked={!f.disabled_drives.includes(d.id)}
						onchange={(e) => toggleDrive(d.id, e.currentTarget.checked)}
					/>
					<DriveName drive={d} />
					{#if d.name}<span class="muted">{`${d.vendor} ${d.model}`.replace(/\s+/g, ' ')}</span>{/if}
					<span class="muted mono">{d.device}</span>
					{#if view.makemkv_drive_data[d.id]}<span class="chip">private data</span>{/if}
					{#if view.makemkv_drive_dumps[d.id]}<span class="chip">firmware dump</span>{/if}
					<select
						class="mode"
						aria-label="Rip mode for {d.display_name}"
						value={f.drive_modes[d.id] ?? ''}
						onchange={(e) => setDriveMode(d.id, e.currentTarget.value)}
					>
						<option value="">Default ({f.rip_mode === 'backup' ? 'full backup' : 'titles'})</option>
						<option value="titles">Titles</option>
						<option value="backup">Full backup</option>
					</select>
				</label>
			{/each}
		</div>
	{/if}
	<h3>Folders</h3>
	<label class="field">
		Library
		<input class="mono" bind:value={f.library_dir} required />
	</label>
	<FolderStatus path={f.library_dir} />
	<label class="field">
		Films (leave empty for the Movies folder in the library)
		<input class="mono" bind:value={f.movies_dir} placeholder={`${f.library_dir}${sep}Movies`} />
	</label>
	<FolderStatus path={f.movies_dir} />
	<label class="field">
		TV (leave empty for the TV folder in the library)
		<input class="mono" bind:value={f.tv_dir} placeholder={`${f.library_dir}${sep}TV`} />
	</label>
	<FolderStatus path={f.tv_dir} />
	<label class="field">
		Full disc backups (leave empty for the Disc backups folder in the library)
		<input class="mono" bind:value={f.disc_backups_dir} placeholder={`${f.library_dir}${sep}Disc backups`} />
	</label>
	<FolderStatus path={f.disc_backups_dir} />
	<label class="field">
		Scratch (rips in progress, where a UHD disc needs about 100 GB)
		<input class="mono" bind:value={f.scratch_dir} required />
	</label>
	<FolderStatus path={f.scratch_dir} />
	<p class="small muted">
		Full paths, as this machine sees them. The library folders must already exist ("Create it" makes one
		that doesn't). The scratch folder can't move while jobs are in progress.
	</p>
	<TestButton check="paths" values={form.body} label="Check the folders are writable" />
	<h3>Database backups</h3>
	<label class="field">
		Daily backups to keep (0 turns them off)
		<input type="number" min="0" max="365" bind:value={f.backup_keep} />
	</label>
	<p class="small muted">
		A copy of the database (jobs, collections and statistics) is made once a day in
		<span class="mono">{view?.backups_dir}</span>. The oldest are deleted once there are more than this.
	</p>
</section>

<style>
	p {
		margin: 0;
	}
	h3 {
		margin: 8px 0 0;
		font-size: 1rem;
	}
	.two {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 10px;
	}
	.tight {
		gap: 6px;
	}
	.mode {
		width: auto;
		margin-left: auto;
	}
</style>
