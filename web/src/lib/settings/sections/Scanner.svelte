<script lang="ts">
	// Settings: The network scanner, reading the box, and what's filed from it.
	import TestButton from '#lib/components/TestButton.svelte';
	import SecretField from '../SecretField.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const view = $derived(form.view!);
</script>

<section class="card stack" id="scanner">
	<h2>Scanner</h2>
	<p class="small muted">
		A network scanner or printer that speaks eSCL (AirScan), for scanning a disc's box from its job. Most
		network printers with a scanner do; ripshelf needs no driver for it.
	</p>
	<label class="check">
		<input type="checkbox" bind:checked={f.scanner_enabled} /> Scan from a network scanner
	</label>
	<label class="field">
		Address
		<input class="mono" bind:value={f.scanner_url} placeholder="http://192.168.1.20/eSCL" />
	</label>
	<p class="small muted">
		The printer's address is enough; <span class="mono">/eSCL</span> is added. Use <span class="mono">http://</span>:
		some printers (Brother's among them) accept a secure connection and then never answer on it.
	</p>
	<div class="grid">
		<label class="field">
			Resolution
			<select bind:value={f.scanner_dpi}>
				{#each [100, 150, 200, 300, 400, 600] as dpi (dpi)}<option value={dpi}>{dpi} dpi{dpi === 300 ? ' (recommended)' : ''}</option>{/each}
			</select>
		</label>
		<label class="field">
			Scan from
			<select bind:value={f.scanner_source}>
				<option value="platen">The glass</option>
				<option value="feeder">The feeder</option>
			</select>
		</label>
	</div>
	<p class="small muted">Insert pages are scanned from the feeder when the scanner has one, whatever this says.</p>
	<label class="check">
		<input type="checkbox" bind:checked={f.scanner_trim} /> Cut away the empty glass around a scan
	</label>
	<TestButton check="scanner" values={form.body} label="Test the scanner" />
	<h3>Reading the box</h3>
	<label class="check">
		<input type="checkbox" bind:checked={f.scan_read_text} /> Read the text on scans and list what's on the disc
	</label>
	<label class="field">
		Languages (Tesseract's codes, joined with +)
		<input class="mono" bind:value={f.ocr_languages} placeholder="eng" />
	</label>
	<p class="small muted">
		Uses Tesseract, which the image includes. Elsewhere, install it (and zbar for barcodes): see the
		cover scans guide. Each language needs its data installed, such as tesseract-ocr-fra for <span class="mono">fra</span>.
	</p>
	<TestButton check="ocr" values={form.body} label="Test text reading" />
	<h3>Vision model</h3>
	<p class="small muted">
		A vision model reads the box too, beside Tesseract: the region in its globe, PAL logos, small print and
		stylised titles that Tesseract misses. What only the model reads is marked so you can check it.
	</p>
	<label class="field">
		Read scans with it
		<select bind:value={f.vision_mode}>
			<option value="off">Never</option>
			<option value="struggles">Only when Tesseract struggles (recommended)</option>
			<option value="every">Every scan</option>
			<option value="manual">Only when I press the button on a scan</option>
		</select>
	</label>
	{#if f.vision_mode !== 'off'}
		<label class="field">
			Where it runs
			<select bind:value={f.vision_provider}>
				<option value="local">On my network (Ollama or another OpenAI-compatible server)</option>
				<option value="openai">A cloud service (OpenAI-compatible)</option>
				<option value="anthropic">Anthropic's API</option>
			</select>
		</label>
		{#if f.vision_provider !== 'anthropic'}
			<label class="field">
				Address
				<input class="mono" bind:value={f.vision_url}
					placeholder={f.vision_provider === 'local' ? 'http://ollama:11434' : 'https://api.example.com/v1'} />
			</label>
		{/if}
		<label class="field">
			Model
			<input class="mono" bind:value={f.vision_model}
				placeholder={f.vision_provider === 'local' ? 'qwen3-vl:4b-instruct' : 'the model name from the service'} />
		</label>
		{#if f.vision_provider === 'local'}
			<p class="small muted">
				On a desktop CPU, qwen3-vl:4b-instruct takes a minute or two a picture (qwen2.5vl:7b reads more
				carefully, at twice the time). It waits while a transcode runs. Use an instruct model, not a
				"thinking" one.
			</p>
		{/if}
		<SecretField {form} name="vision_api_key" label={f.vision_provider === 'local' ? 'Key (if the server needs one)' : 'API key'} />
		{#if f.vision_provider !== 'local'}
			<p class="small warn-note">
				Scans are sent to this service, outside your network. Only pictures of the box are sent.
			</p>
		{/if}
		<TestButton check="vision" values={form.body} label="Test the vision model" />
	{/if}
	<h3>Extras the box names</h3>
	<label class="check">
		<input type="checkbox" bind:checked={f.extras_emby_folders} /> File named extras into Emby's folders
	</label>
	<p class="small muted">
		An extra the cover scan matches to a title (with more than low confidence) is filed under its name in Emby's
		folder for its type: featurettes, behind the scenes, deleted scenes, interviews, trailers, scenes, shorts
		or extras, beside the film or in the show's folder. A film's are ripped with it.
	</p>
	<label class="field">
		A film's other titles
		<select bind:value={f.film_unnamed_extras}>
			<option value="skip">Leave them out</option>
			<option value="extras">Rip them into extras/ as "Title N"</option>
		</select>
	</label>
	<label class="field">
		Bonus episodes of other shows
		<select bind:value={f.tv_bonus_episodes}>
			<option value="extra">An extra of this show, named "Other show - Episode"</option>
			<option value="own_show">An episode in the other show's own folder</option>
		</select>
	</label>
	<p class="small muted">
		When a TV disc's box lists a bonus episode of a different show ("Includes bonus episode", "Plus meet…"),
		it's found in that show's episode list and ripped. As an extra it goes in the show's
		<span class="mono">extras</span> folder; in its own show, as
		<span class="mono">Other show (year)/Season 01/Other show - S01E24 - Episode.mkv</span>.
	</p>
	<h3>In the library</h3>
	<label class="check">
		<input type="checkbox" bind:checked={f.scans_to_library} /> Copy scans to the library
	</label>
	<p class="small muted">
		Into a <span class="mono">scans</span> folder in the film's or show's folder, once the disc is filed, as
		they were scanned. A file already there is never replaced, and the names (front.jpg, inside-full.jpg…)
		aren't ones Emby, Jellyfin or Plex take for artwork.
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
	.warn-note {
		color: var(--warn);
	}
</style>
