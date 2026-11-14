<script lang="ts">
	// Settings → Scanner and covers: the network scanner, reading the box (Tesseract and a
	// vision model), and copying scans to the library.
	import { untrack } from 'svelte';
	import TestButton from '#lib/components/TestButton.svelte';
	import Advanced from '../Advanced.svelte';
	import Field from '../Field.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const local = $derived(f.vision_provider === 'local');
	// Blank is the provider's default model; a hosted OpenAI-compatible service has none.
	const defaults = $derived(form.view?.vision_models ?? {});
	const defaultModel = $derived(defaults[f.vision_provider] ?? '');
	// Another provider's default model isn't one this provider has (Ollama's name sent
	// to Anthropic), so switching clears it and the new provider's default shows instead.
	$effect(() => {
		const provider = f.vision_provider;
		untrack(() => {
			const typed = f.vision_model.trim();
			if (typed && Object.values(defaults).includes(typed) && typed !== defaults[provider]) form.f.vision_model = '';
		});
	});
</script>

<section class="card stack" aria-labelledby="scanner">
	<h2 id="scanner">Scanner</h2>
	<Field {form} name="scanner_enabled"
		help="A network scanner or printer that speaks eSCL (AirScan), for scanning a disc's box from its job." />
	<Field {form} name="scanner_url" disabled={!f.scanner_enabled}>
		{#snippet more()}
			Some printers (Brother's among them) accept a secure connection and then never answer on it. Most network
			printers with a scanner speak eSCL; ripshelf needs no driver.
		{/snippet}
	</Field>
	<div class="two">
		<Field {form} name="scanner_dpi" disabled={!f.scanner_enabled} />
		<Field {form} name="scanner_source" disabled={!f.scanner_enabled} />
	</div>
	<Field {form} name="scanner_trim" disabled={!f.scanner_enabled} />
	<TestButton check="scanner" values={form.body} label="Test the scanner" />
</section>

<section class="card stack" aria-labelledby="reading">
	<h2 id="reading">Reading the box</h2>
	<Field {form} name="scan_read_text" help="With Tesseract, which the image includes.">
		{#snippet more()}
			Elsewhere, install Tesseract (and zbar for barcodes): see the cover scans guide. Each language needs its data
			installed, such as tesseract-ocr-fra for <span class="mono">fra</span>.
		{/snippet}
	</Field>
	<TestButton check="ocr" values={form.body} label="Test text reading" />

	<h3>Vision model</h3>
	<Field {form} name="vision_mode" help="Reads what Tesseract misses: region globes, logos, small print, stylised titles.">
		{#snippet more()}
			What only the model reads is marked on the scan, so you can check it.
		{/snippet}
	</Field>
	{#if f.vision_mode !== 'off' || f.label_ai !== 'off'}
		{#if f.vision_mode === 'off'}
			<p class="small muted">The connection is also used to read cryptic labels (Settings → Identify).</p>
		{/if}
		<Field {form} name="vision_provider">
			{#if !local}
				<p class="small warn-note" role="note">
					⚠ Scans are sent to this service, outside your network. Only pictures of the box are sent.
				</p>
			{/if}
		</Field>
		{#if f.vision_provider !== 'anthropic'}
			<Field {form} name="vision_url" placeholder={local ? 'http://ollama:11434' : 'https://api.example.com/v1'} />
		{/if}
		<Field {form} name="vision_model" placeholder={defaultModel || 'the model name from the service'}
			help={defaultModel ? `Blank uses ${defaultModel}.` : 'The service has no default: name one of its models.'}>
			{#snippet more()}
				{#if local}
					On a desktop CPU, qwen3-vl:4b-instruct takes a minute or two a picture (qwen2.5vl:7b reads more carefully, at
					twice the time). It waits while a transcode runs. Use an instruct model, not a "thinking" one.
				{:else if f.vision_provider === 'anthropic'}
					Any Claude model reads pictures. claude-haiku-5-5 costs less a scan but misses more of the small print;
					claude-opus-5-5 costs about twice as much as the default.
				{:else}
					Use a model the service lists as reading images.
				{/if}
			{/snippet}
		</Field>
		<Field {form} name="vision_api_key" label={local ? 'Key (if the server needs one)' : 'API key'} />
		<TestButton check="vision" values={form.body} label="Test the vision model" />
	{/if}

	<Advanced>
		<Field {form} name="ocr_languages" />
	</Advanced>
</section>

<section class="card stack" aria-labelledby="scans-library">
	<h2 id="scans-library">In the library</h2>
	<Field {form} name="scans_to_library">
		{#snippet more()}
			They're copied as scanned, and a file already there is never replaced. The names (front.jpg,
			inside-full.jpg…) aren't ones Emby, Jellyfin or Plex take for artwork.
		{/snippet}
	</Field>
	<p class="small muted">How the extras a box names are filed is under <a href="/settings/library">Library and filing</a>.</p>
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
	.warn-note {
		color: var(--warn);
		background: var(--warn-bg);
		border-radius: 8px;
		padding: 6px 10px;
	}
	@media (max-width: 480px) {
		.two {
			grid-template-columns: 1fr;
		}
	}
</style>
