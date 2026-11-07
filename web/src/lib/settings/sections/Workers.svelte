<script lang="ts">
	// Settings → Workers: other machines whose drives rip for this one.
	import RemoteWorkers from '#lib/components/RemoteWorkers.svelte';
</script>

<RemoteWorkers />

<section class="card stack" aria-labelledby="what-workers-use">
	<h2 id="what-workers-use">What workers use</h2>
	<p class="small muted">
		A worker only runs MakeMKV on its drives. It fetches the MakeMKV key, private data and firmware dumps from here
		when it connects, and its rips come back here to be identified, transcoded and filed, with these settings.
	</p>
	<p class="small muted">
		Settings marked <span class="chip">This machine only</span> aren't sent to workers: a worker reads its own
		(such as <span class="mono">RIPSHELF_UHD_MODELS</span>) from its environment. See docs/workers.md.
	</p>
</section>

<style>
	p {
		margin: 0;
	}
</style>
