<script lang="ts">
	// Settings: Metadata providers and their keys, and contributing to TheDiscDB.
	import ProviderList from '#lib/components/ProviderList.svelte';
	import TestButton from '#lib/components/TestButton.svelte';
	import ProviderSite from '../ProviderSite.svelte';
	import SecretField from '../SecretField.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const view = $derived(form.view!);
</script>

<section class="card stack">
	<h2>Metadata providers</h2>
	<p class="small muted">
		Where ripshelf looks discs, films and shows up. Each list is asked in order; untick a provider to
		leave it out. Providers that need a key are skipped until it is set below.
	</p>
	<ProviderList
		title="Films"
		hint="TheDiscDB recognises the disc itself; every ticked search is asked and their results merged."
		bind:rows={f.metadata_providers.movie}
		info={form.providerInfo}
		keyed={form.keyed}
	/>
	<ProviderList
		title="TV shows"
		hint="Asked in order until one finds the show. TMDB names everything, so it needs a TMDB key either way."
		bind:rows={f.metadata_providers.tv}
		info={form.providerInfo}
		keyed={form.keyed}
	/>
	<ProviderList
		title="TV episodes"
		hint="A season's episode list comes from the first provider that has it (TMDB's if none does)."
		bind:rows={f.metadata_providers.episodes}
		info={form.providerInfo}
		keyed={form.keyed}
	/>

	<h3>TMDB</h3>
	<SecretField {form} name="tmdb_api_key" label={'TMDB API key (v3)'} />
	<div class="row">
		<TestButton check="tmdb" values={form.body} label="Test connection" />
		<ProviderSite {form} name="tmdb" />
	</div>
	<label class="field">TMDB language <input bind:value={f.tmdb_language} /></label>

	<h3>TheTVDB</h3>
	<SecretField {form} name="tvdb_api_key" label={'TheTVDB API key (v4)'} />
	<SecretField {form} name="tvdb_pin" label={'Subscriber PIN (only for user-supported keys)'} />
	<label class="field">
		Episode order
		<select bind:value={f.tvdb_episode_order}>
			<option value="aired">Aired order</option>
			<option value="dvd">DVD order (falls back to aired)</option>
		</select>
	</label>
	<p class="small muted">
		DVD order follows the discs, but Emby numbers episodes in aired order unless the TV library is set to
		DVD order.
	</p>
	<div class="row">
		<TestButton check="tvdb" values={form.body} label="Test connection" />
		<ProviderSite {form} name="tvdb" />
	</div>

	<h3>OMDb</h3>
	<SecretField {form} name="omdb_api_key" label={'OMDb API key'} />
	<div class="row">
		<TestButton check="omdb" values={form.body} label="Test connection" />
		<ProviderSite {form} name="omdb" />
	</div>

	<h3>TheDiscDB and TVmaze</h3>
	<p class="small muted">Free, no key. TheDiscDB only knows discs someone has added, and you can add yours on its site.</p>
	<div class="row">
		<TestButton check="thediscdb" values={form.body} label="Test TheDiscDB" />
		<TestButton check="tvmaze" values={form.body} label="Test TVmaze" />
	</div>

	<h3>Contribute to TheDiscDB</h3>
	<p class="small muted">
		Send discs you have confirmed, and TheDiscDB doesn't know yet, so others' rips recognise them. What's sent is the
		disc's content hash, label and type, what it is (TMDB id, season) and its titles (playlist, length, size,
		which film or episode), with MakeMKV's title list. Never your drives, folders or host name. You finish
		each release on TheDiscDB's site, signed in with GitHub, before it is reviewed.
		<a href="https://thediscdb.com/help/contributing" target="_blank" rel="noreferrer">How it works →</a>
	</p>
	<label class="field">
		Contribute confirmed discs
		<select bind:value={f.thediscdb_contribute}>
			<option value="off">Off</option>
			<option value="ask">Ask, with a Send button on each job</option>
			<option value="auto">Automatically, once I confirm a disc</option>
		</select>
	</label>
	{#if f.thediscdb_contribute !== 'off'}
		<label class="field">
			TheDiscDB address
			<input bind:value={f.thediscdb_contribute_url} placeholder="https://thediscdb.com" />
		</label>
		<SecretField {form} name="thediscdb_api_key" label={'API key (optional; only if TheDiscDB gave you one)'} />
	{/if}
</section>

<style>
	p {
		margin: 0;
	}
	h3 {
		margin: 8px 0 0;
		font-size: 1rem;
	}
</style>
