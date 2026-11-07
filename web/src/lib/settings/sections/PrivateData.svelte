<script lang="ts">
	// Settings: MakeMKV's private data and firmware dumps, imported here and kept for workers.
	import type { MakeMKVFile } from '#lib/api-types.ts';
	import { api } from '#lib/api.ts';
	import { Action } from '#lib/actions.svelte.ts';
	import { live } from '#lib/live.svelte.ts';
	import More from '../More.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();

	const importAct = new Action();
	let importNote = $state<string | null>(null);
	let importPath = $state('');
	let importDrives = $state('all');
	let fileInput = $state<HTMLInputElement | null>(null);

	// Imported files, kept for remote workers.
	const filesAct = new Action();
	let mkFiles = $state<MakeMKVFile[]>([]);
	async function loadFiles() {
		mkFiles = (await filesAct.run(() => api.makemkvFiles())) ?? mkFiles;
	}
	$effect(() => {
		loadFiles();
	});
	async function deleteFile(f: MakeMKVFile) {
		if (!confirm(`Stop offering ${f.name} (${f.sha256.slice(0, 12)}…) to workers? Drives that have it keep it.`)) return;
		await filesAct.run(() => api.deleteMakemkvFile(f.sha256, f.name));
		await loadFiles();
	}
	const driveLabel = (id: string) => live.drives.find((d) => d.id === id)?.display_name ?? id;
	const kb = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`);

	async function importData(e: SubmitEvent) {
		e.preventDefault();
		importNote = null;
		const data = new FormData();
		data.set('drive_ids', importDrives);
		const file = fileInput?.files?.[0];
		if (file) data.set('file', file);
		else if (importPath.trim()) data.set('path', importPath.trim());
		else {
			importAct.error = 'Choose a file or give a path on the server';
			return;
		}
		const r = await importAct.run(() => api.importPrivateData(data));
		if (r) {
			importNote = `Imported into ${r.drive_ids.length} drive(s). Retry any scan that failed or timed out.`;
			await form.refresh();
			await loadFiles();
		}
	}
</script>

<form class="card stack instant setting" id="setting-private_data" onsubmit={importData}>
	<div class="spread">
		<h2>MakeMKV private data</h2>
		<span class="chip" title="Imported when you press Import, not with Save">Saved straight away</span>
	</div>
	<p class="small muted">
		A drive's <span class="mono">_private_data.tar</span>, or a firmware dump (<span class="mono">dump_*.tgz</span>),
		from an existing MakeMKV install. Remote workers fetch them when they connect.
		<More>
			The private data has LibreDrive and per-drive keys: without it some UHD discs fail with “volume key unknown”.
			Older drives such as the LG GGC-H20N hang in MakeMKV 1.17.7 and later without a firmware dump.
		</More>
	</p>
	<label class="field">Upload <input type="file" accept=".tar,.tgz" bind:this={fileInput} /></label>
	<label class="field">…or a path on the server <input bind:value={importPath} placeholder="/path/to/_private_data.tar" /></label>
	<label class="field">
		For
		<select bind:value={importDrives}>
			<option value="all">All drives</option>
			{#each live.drives as d (d.id)}
				<option value={d.id}>{d.display_name} ({d.device})</option>
			{/each}
		</select>
	</label>
	<div class="row">
		<button type="submit" disabled={importAct.busy}>Import</button>
		{#if importNote}<span class="small muted">{importNote}</span>{/if}
		{#if importAct.error}<span class="small error-text">{importAct.error}</span>{/if}
	</div>
	{#if mkFiles.length}
		<h3>Kept for workers</h3>
		{#each mkFiles as mf (mf.sha256 + mf.name)}
			<div class="spread">
				<div class="small">
					<span class="mono">{mf.name}</span> · {kb(mf.size)} ·
					{mf.drives ? mf.drives.map(driveLabel).join(', ') : 'all drives'}
					<div class="mono muted sha" title="sha256">{mf.sha256}</div>
				</div>
				<button type="button" onclick={() => deleteFile(mf)} disabled={filesAct.busy}>Delete</button>
			</div>
		{/each}
	{/if}
	{#if filesAct.error}<span class="small error-text">{filesAct.error}</span>{/if}
</form>

<style>
	.sha {
		word-break: break-all;
	}
	.chip {
		white-space: nowrap;
	}
	p {
		margin: 0;
	}
	h3 {
		margin: 8px 0 0;
		font-size: 1rem;
	}
</style>
