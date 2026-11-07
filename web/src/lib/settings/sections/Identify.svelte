<script lang="ts">
	// Settings → Identify: where discs, films and shows are looked up, the keys for that,
	// when a match is confirmed without review, web search, and contributing to TheDiscDB.
	import ProviderList from '#lib/components/ProviderList.svelte';
	import TestButton from '#lib/components/TestButton.svelte';
	import Advanced from '../Advanced.svelte';
	import Field from '../Field.svelte';
	import ProviderSite from '../ProviderSite.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
</script>

<section class="card stack" aria-labelledby="providers">
	<h2 id="providers">Where discs are looked up</h2>
	<div class="setting stack" id="setting-metadata_providers">
		<p class="small muted">
			Each list is asked in order; untick a provider to leave it out. Providers that need a key are skipped until it's
			set below.
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
	</div>
</section>

<section class="card stack" aria-labelledby="keys">
	<h2 id="keys">Keys</h2>
	<Field {form} name="tmdb_api_key" />
	<div class="row">
		<TestButton check="tmdb" values={form.body} label="Test TMDB" />
		<ProviderSite {form} name="tmdb" />
	</div>

	<Field {form} name="tvdb_api_key" />
	<Field {form} name="tvdb_episode_order" />
	<div class="row">
		<TestButton check="tvdb" values={form.body} label="Test TheTVDB" />
		<ProviderSite {form} name="tvdb" />
	</div>

	<Field {form} name="omdb_api_key" />
	<div class="row">
		<TestButton check="omdb" values={form.body} label="Test OMDb" />
		<ProviderSite {form} name="omdb" />
	</div>

	<p class="small muted">TheDiscDB and TVmaze are free and need no key.</p>
	<div class="row">
		<TestButton check="thediscdb" values={form.body} label="Test TheDiscDB" />
		<TestButton check="tvmaze" values={form.body} label="Test TVmaze" />
	</div>

	<Advanced>
		<Field {form} name="tmdb_language" />
		<Field {form} name="tvdb_pin" />
	</Advanced>
</section>

<section class="card stack" aria-labelledby="matching">
	<h2 id="matching">Confirming a match</h2>
	<Field {form} name="auto_confirm_threshold" />
	<Field {form} name="tv_auto_confirm" />
	<Field {form} name="tv_auto_threshold" disabled={!f.tv_auto_confirm}>
		{#snippet more()}
			It also needs the label to give the season and disc (or the disc continues your collection), every episode
			runtime to match, and the order verified against the play-all.
		{/snippet}
	</Field>
	<Field {form} name="verify_order" />
</section>

<section class="card stack" aria-labelledby="web-search">
	<h2 id="web-search">Web search</h2>
	<Field {form} name="searxng_url">
		{#snippet more()}
			Review can search the web for a disc nothing identified, and give the names it finds to TMDB. SearXNG's JSON
			output must be on: add <code>json</code> to <code>search.formats</code> in its settings.yml.
		{/snippet}
	</Field>
	<Field {form} name="catalogue_search">
		{#snippet more()}
			A web search for the number finds shop listings; the title several of them agree on is searched on TMDB, and
			its runtime still has to fit.
		{/snippet}
	</Field>
	<TestButton check="catalogue" values={form.body} label="Test (looks up EO51710)" />
</section>

<section class="card stack" aria-labelledby="contribute">
	<h2 id="contribute">Contribute to TheDiscDB</h2>
	<Field {form} name="thediscdb_contribute"
		help="Send discs you've confirmed, that TheDiscDB doesn't know yet, so others' rips recognise them.">
		{#snippet more()}
			What's sent is the disc's content hash, label and type, what it is (TMDB id, season) and its titles (playlist,
			length, size, which film or episode), with MakeMKV's title list. Never your drives, folders or host name. You
			finish each release on TheDiscDB's site, signed in with GitHub, before it's reviewed.
			<a href="https://thediscdb.com/help/contributing" target="_blank" rel="noreferrer">How it works →</a>
		{/snippet}
	</Field>
	{#if f.thediscdb_contribute !== 'off'}
		<Advanced>
			<Field {form} name="thediscdb_contribute_url" />
			<Field {form} name="thediscdb_api_key" />
		</Advanced>
	{/if}
</section>

<style>
	p {
		margin: 0;
	}
</style>
