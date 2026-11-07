<script lang="ts">
	// Settings → Transcoding: re-encoding rips to H.265 (on this machine; workers only rip).
	import type { Hardware } from '#lib/api-types.ts';
	import HardwarePanel from '#lib/components/HardwarePanel.svelte';
	import Advanced from '../Advanced.svelte';
	import Field from '../Field.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const off = $derived(!f.transcode_enabled);

	let hw = $state<Hardware | null>(null);
	const renderNodes = $derived(hw?.devices.filter((d) => d.path.includes('renderD')) ?? []);
	const discKinds = [
		['dvd', 'DVD'],
		['bluray', 'Blu-ray'],
		['uhd', '4K UHD']
	] as const;
</script>

<section class="card stack" aria-labelledby="transcode">
	<h2 id="transcode">Transcoding</h2>
	<Field {form} name="transcode_enabled" help="Audio, subtitles and chapters are copied untouched. A failed transcode files the rip as it is." />
	<div class="setting stack tight" id="setting-transcode_sources">
		<span class="small">Which discs</span>
		<div class="sources">
			{#each discKinds as [kind, label] (kind)}
				{#if f.transcode_sources[kind]}
					<fieldset class="stack tight" disabled={off}>
						<legend>{label}</legend>
						<label class="check"><input type="checkbox" bind:checked={f.transcode_sources[kind].transcode} /> Transcode</label>
						<label class="check">
							<input type="checkbox" bind:checked={f.transcode_sources[kind].crop}
								disabled={!f.transcode_sources[kind].transcode || f.transcode_crop === 'off'} /> Crop black bars
						</label>
					</fieldset>
				{/if}
			{/each}
		</div>
		<p class="small muted">HDR and Dolby Vision are left alone unless allowed below, even on a ticked 4K UHD disc.</p>
	</div>
	<div class="two">
		<Field {form} name="transcode_encoder" disabled={off} />
		<Field {form} name="transcode_quality" disabled={off} />
	</div>
	<Field {form} name="transcode_hdr" disabled={off} />
	<Field {form} name="transcode_keep_original" disabled={off} />
	<Advanced>
		<Field {form} name="transcode_codecs" disabled={off} />
		<Field {form} name="transcode_crop" disabled={off} />
		<Field {form} name="transcode_parallel" disabled={off}>
			{#snippet more()}
				How many jobs transcode side by side, whichever drive or worker ripped them; the rest wait their turn. x265
				already uses every CPU core. Some consumer GPUs cap how many encodes run at once. A new limit applies to the
				jobs still waiting.
			{/snippet}
		</Field>
		{#if renderNodes.length > 1}
			<div class="setting" id="setting-vaapi_device">
				<label class="field">
					<span>{form.fields.vaapi_device.title} <span class="chip">This machine only</span></span>
					<select bind:value={f.vaapi_device} disabled={off}>
						{#if !renderNodes.some((d) => d.path === f.vaapi_device)}<option value={f.vaapi_device}>{f.vaapi_device}</option>{/if}
						{#each renderNodes as d (d.path)}
							<option value={d.path}>{d.path}{d.vendor ? ` (${[d.vendor, d.model].filter(Boolean).join(' ')})` : ''}</option>
						{/each}
					</select>
				</label>
			</div>
		{:else}
			<Field {form} name="vaapi_device" disabled={off} />
		{/if}
	</Advanced>
</section>

<section class="card stack setting" id="setting-hardware">
	<HardwarePanel bind:hw />
</section>

<style>
	p {
		margin: 0;
	}
	.tight {
		gap: 6px;
	}
	.two {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 10px;
	}
	.sources {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 10px;
	}
	.sources fieldset {
		margin: 0;
		padding: 8px 10px;
		border: 1px solid var(--border);
		border-radius: 9px;
	}
	.sources legend {
		padding: 0 4px;
	}
	.chip {
		vertical-align: middle;
		margin-left: 4px;
	}
	@media (max-width: 480px) {
		.two {
			grid-template-columns: 1fr;
		}
	}
</style>
