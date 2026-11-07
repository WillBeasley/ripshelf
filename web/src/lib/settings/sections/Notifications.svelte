<script lang="ts">
	// Settings: Pushover notifications, and which events send them.
	import TestButton from '#lib/components/TestButton.svelte';
	import SecretField from '../SecretField.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);
	const view = $derived(form.view!);

	const priorities: [string, string][] = [
		['off', 'Off'],
		['-2', 'Lowest (no alert)'],
		['-1', 'Low (quiet)'],
		['0', 'Normal'],
		['1', 'High (bypasses quiet hours)']
	];
	const events: [string, string, string][] = [
		['disc', 'Disc inserted', 'A disc goes into a drive'],
		['review', 'Needs review', 'It could not be identified for sure'],
		['exists', 'Already in library', 'A copy is already there, so you decide'],
		['ripped', 'Rip finished', 'MakeMKV is done, and whether it transcodes or copies next'],
		['transcoded', 'Transcode finished', 'With how many files were transcoded'],
		['copied', 'Copied to library', 'The files are in the library, with where'],
		['done', 'Done', 'Everything is finished and the media servers are told'],
		['failed', 'Failed', 'A job stopped with a problem'],
		['setup', 'Setup problem', 'Something in Settings needs fixing']
	];
	type Event = keyof typeof f.notify_events;
	const eventPriority = (e: string) => {
		const p = f.notify_events[e as Event];
		return p === null || p === undefined ? 'off' : String(p);
	};
	const setEventPriority = (e: string, v: string) => {
		f.notify_events = { ...f.notify_events, [e]: v === 'off' ? null : Number(v) };
	};
</script>

<section class="card stack">
	<h2>Notifications (Pushover)</h2>
	<label class="field">
		This UI's address from a phone, so every notification links to its job
		<input bind:value={f.public_url} placeholder="http://192.168.1.10:8380" />
	</label>
	{#if !f.public_url.trim()}
		<p class="small error-text">Not set, so notifications will have no link to the job.</p>
	{/if}
	<p class="small muted">Off until both keys are set.</p>
	<SecretField {form} name="pushover_user_key" label={'User key'} />
	<SecretField {form} name="pushover_app_token" label={'Application API token'} />
	<label class="field">Device (optional, else all your devices) <input bind:value={f.pushover_device} /></label>
	<div class="events">
		{#each events as [e, label, hint] (e)}
			<label class="field">
				{label}
				<span class="small muted">{hint}</span>
				<select value={eventPriority(e)} onchange={(ev) => setEventPriority(e, ev.currentTarget.value)}>
					{#each priorities as [v, plabel] (v)}<option value={v}>{plabel}</option>{/each}
				</select>
			</label>
		{/each}
	</div>
	<label class="field">
		Batch window in seconds. More of the same kind within this time come as one summary (0 sends every one at once)
		<input type="number" min="0" max="3600" bind:value={f.notify_batch_s} />
	</label>
	<TestButton check="pushover" values={form.body} label="Send a test message" />
</section>

<style>
	p {
		margin: 0;
	}
	.events {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
		gap: 10px;
	}
	.events select {
		margin-top: auto; /* level across a row whatever the hint's length */
	}
</style>
