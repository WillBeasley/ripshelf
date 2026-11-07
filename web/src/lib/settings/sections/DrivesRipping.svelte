<script lang="ts">
	// Settings → Drives and ripping: MakeMKV's key, what's ripped, each drive (grouped by the
	// machine it's on), and MakeMKV's private data.
	import type { Drive } from '#lib/api-types.ts';
	import { live } from '#lib/live.svelte.ts';
	import DriveName from '#lib/components/DriveName.svelte';
	import TestButton from '#lib/components/TestButton.svelte';
	import Advanced from '../Advanced.svelte';
	import Field from '../Field.svelte';
	import PrivateData from './PrivateData.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	type RipMode = 'titles' | 'backup';
	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const view = $derived(form.view!);

	// This machine's drives first, then each worker's.
	const machines = $derived.by(() => {
		const by = new Map<string, Drive[]>();
		for (const d of live.drives) {
			const k = d.worker ?? '';
			by.set(k, [...(by.get(k) ?? []), d]);
		}
		return [...by.entries()].sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)));
	});

	// '' follows the default rip mode; otherwise the drive's own.
	function setDriveMode(id: string, mode: string) {
		const { [id]: _, ...rest } = f.drive_modes;
		f.drive_modes = mode ? { ...rest, [id]: mode as RipMode } : rest;
	}

	function toggleDrive(id: string, on: boolean) {
		f.disabled_drives = on ? f.disabled_drives.filter((d) => d !== id) : [...f.disabled_drives, id];
	}
	const kinds: Record<string, string> = { uhd: 'UHD', bluray: 'Blu-ray', dvd: 'DVD' };
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
	<div class="setting stack" id="setting-disabled_drives">
		{#if !live.drives.length}
			<p class="small muted">No drives found.</p>
		{/if}
		{#each machines as [worker, drives] (worker)}
			<div class="stack tight">
				<h3>{worker ? `Worker ${worker}` : 'This machine'}</h3>
				{#each drives as d (d.id)}
					{@const on = !f.disabled_drives.includes(d.id)}
					<div class="drive" class:off={!on} id="setting-drive_modes-{d.id}">
						<label class="check enable">
							<input type="checkbox" checked={on} aria-label="Use {d.display_name}"
								onchange={(e) => toggleDrive(d.id, e.currentTarget.checked)} />
						</label>
						<div class="about">
							<DriveName drive={d} />
							<div class="small muted">
								{#if d.name}{`${d.vendor} ${d.model}`.replace(/\s+/g, ' ')} · {/if}<span class="mono">{d.device}</span>
							</div>
							<div class="chips">
								{#each d.capabilities as c (c)}<span class="chip" class:uhd={c === 'uhd'}>{kinds[c] ?? c}</span>{/each}
								{#if view.makemkv_drive_data[d.id]}<span class="chip">private data</span>{/if}
								{#if view.makemkv_drive_dumps[d.id]}<span class="chip">firmware dump</span>{/if}
							</div>
						</div>
						<select class="mode" aria-label="Rip mode for {d.display_name}" value={f.drive_modes[d.id] ?? ''}
							onchange={(e) => setDriveMode(d.id, e.currentTarget.value)} disabled={!on}>
							<option value="">Default ({f.rip_mode === 'backup' ? 'full backup' : 'titles'})</option>
							<option value="titles">Titles</option>
							<option value="backup">Full backup</option>
						</select>
					</div>
				{/each}
			</div>
		{/each}
		<p class="small muted">Untick a drive for ripshelf to leave it alone. Click a name to rename it.</p>
	</div>
</section>

<PrivateData {form} />

<style>
	p {
		margin: 0;
	}
	h3 {
		margin: 4px 0 0;
		font-size: 0.95rem;
	}
	.tight {
		gap: 8px;
	}
	.two {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 10px;
	}
	.drive {
		display: grid;
		grid-template-columns: auto 1fr auto;
		align-items: center;
		gap: 6px 12px;
		padding: 10px 12px;
		border: 1px solid var(--border);
		border-radius: 10px;
		background: var(--surface-2);
	}
	.drive.off .about {
		opacity: 0.6;
	}
	.enable input {
		width: 20px;
		height: 20px;
	}
	.about {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		margin-top: 4px;
	}
	.mode {
		width: auto;
	}
	@media (max-width: 480px) {
		.two {
			grid-template-columns: 1fr;
		}
		.drive {
			grid-template-columns: auto 1fr;
		}
		.mode {
			grid-column: 1 / -1;
			width: 100%;
		}
	}
</style>
