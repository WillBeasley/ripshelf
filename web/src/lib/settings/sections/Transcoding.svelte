<script lang="ts">
	// Settings: Transcoding after ripping.
	import type { Hardware } from '#lib/api-types.ts';
	import HardwarePanel from '#lib/components/HardwarePanel.svelte';
	import ListInput from '../ListInput.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const view = $derived(form.view!);

	let hw = $state<Hardware | null>(null);
	const renderNodes = $derived(hw?.devices.filter((d) => d.path.includes('renderD')) ?? []);
	const discKinds: [string, string][] = [
		['dvd', 'DVD'],
		['bluray', 'Blu-ray'],
		['uhd', '4K UHD']
	];
</script>

<section class="card stack">
	<h2>Transcoding</h2>
	<label class="check"><input type="checkbox" bind:checked={f.transcode_enabled} /> Transcode after ripping</label>
	<label class="field">
		Video codecs to re-encode to H.265 (ffprobe names, comma separated)
		<ListInput bind:value={f.transcode_codecs} placeholder="vc1" disabled={!f.transcode_enabled} />
	</label>
	<label class="field">
		Crop black bars
		<select bind:value={f.transcode_crop} disabled={!f.transcode_enabled}>
			<option value="off">Never</option>
			<option value="when_transcoding">When transcoding anyway</option>
			<option value="always">Always (re-encodes any letterboxed film)</option>
		</select>
	</label>
	<div class="sources">
		{#each discKinds as [kind, label] (kind)}
			{@const src = f.transcode_sources[kind]}
			{#if src}
				<fieldset class="stack tight" disabled={!f.transcode_enabled}>
					<legend>{label}</legend>
					<label class="check"><input type="checkbox" bind:checked={f.transcode_sources[kind].transcode} /> Transcode</label>
					<label class="check">
						<input type="checkbox" bind:checked={f.transcode_sources[kind].crop} disabled={!src.transcode || f.transcode_crop === 'off'} /> Crop black bars
					</label>
				</fieldset>
			{/if}
		{/each}
	</div>
	<p class="small muted">
		Which discs are transcoded, and which have their black bars cropped (when the setting above crops at all).
		HDR and Dolby Vision video is still left alone unless allowed below, even on a 4K UHD disc that's ticked.
	</p>
	<div class="two">
		<label class="field">
			Encoder
			<select bind:value={f.transcode_encoder} disabled={!f.transcode_enabled}>
				<option value="auto">Auto (Quick Sync, VAAPI, NVENC, else CPU)</option>
				<option value="qsv">GPU (Intel Quick Sync)</option>
				<option value="vaapi">GPU (VAAPI)</option>
				<option value="nvenc">GPU (NVIDIA NVENC)</option>
				<option value="x265">CPU (x265)</option>
			</select>
		</label>
		<label class="field">
			Quality (QP/CRF, lower = better) <input type="number" min="10" max="40" bind:value={f.transcode_quality} disabled={!f.transcode_enabled} />
		</label>
	</div>
	<label class="field">
		Transcodes at once <input type="number" min="1" max="8" bind:value={f.transcode_parallel} disabled={!f.transcode_enabled} />
	</label>
	<p class="small muted">
		How many jobs transcode side by side, whichever drive or worker ripped them; the rest wait their turn.
		One is best for x265, which already uses every CPU core. A GPU can often take two or three, though some
		consumer cards cap how many encodes run at once. A new limit applies to the jobs still waiting.
	</p>
	<label class="check"><input type="checkbox" bind:checked={f.transcode_keep_original} disabled={!f.transcode_enabled} /> Keep the original rip too (“… - original.mkv”)</label>
	<label class="check"><input type="checkbox" bind:checked={f.transcode_hdr} disabled={!f.transcode_enabled} /> Also touch HDR and Dolby Vision sources</label>
	<label class="field">
		GPU for Quick Sync and VAAPI
		{#if renderNodes.length > 1}
			<select bind:value={f.vaapi_device} disabled={!f.transcode_enabled}>
				{#if !renderNodes.some((d) => d.path === f.vaapi_device)}<option value={f.vaapi_device}>{f.vaapi_device}</option>{/if}
				{#each renderNodes as d (d.path)}
					<option value={d.path}>{d.path}{d.vendor ? ` (${[d.vendor, d.model].filter(Boolean).join(' ')})` : ''}</option>
				{/each}
			</select>
		{:else}
			<input bind:value={f.vaapi_device} disabled={!f.transcode_enabled} />
		{/if}
	</label>
	<p class="small muted">Audio, subtitles, chapters and metadata are copied untouched. If a transcode fails, the rip goes to the library as it is.</p>
	<HardwarePanel bind:hw />
</section>

<style>
	p {
		margin: 0;
	}
	.two {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 10px;
	}
	.tight {
		gap: 6px;
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
</style>
