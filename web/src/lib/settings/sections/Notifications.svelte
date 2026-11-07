<script lang="ts">
	// Settings → Notifications: Pushover, which events send one, and this browser's alert sound.
	import { alerts } from '#lib/alerts.svelte.ts';
	import TestButton from '#lib/components/TestButton.svelte';
	import Field from '../Field.svelte';
	import type { SettingsForm } from '../form.svelte.ts';

	let { form }: { form: SettingsForm } = $props();
	const f = $derived(form.f);

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
	const keysSet = $derived(
		(form.isSet('pushover_user_key') || !!form.secrets.pushover_user_key?.trim()) &&
			(form.isSet('pushover_app_token') || !!form.secrets.pushover_app_token?.trim())
	);
</script>

<section class="card stack" aria-labelledby="pushover">
	<h2 id="pushover">Pushover</h2>
	<p class="small muted">
		Notifications to your phone with <a href="https://pushover.net" target="_blank" rel="noreferrer">Pushover</a>, off
		until both keys are set.
	</p>
	<Field {form} name="pushover_user_key" />
	<Field {form} name="pushover_app_token" />
	<Field {form} name="pushover_device" />
	<TestButton check="pushover" values={form.body} label="Send a test message" />
	{#if !form.view?.public_url}
		<p class="small warn-note">
			Set <a href="/settings/system#setting-public_url">this UI's address</a> so each notification links to its job.
		</p>
	{/if}
</section>

<section class="card stack" aria-labelledby="events">
	<h2 id="events">Events</h2>
	<div class="setting" id="setting-notify_events">
		<ul class="events" class:muted-list={!keysSet}>
			{#each events as [e, label, hint] (e)}
				<li>
					<label for="event-{e}">
						<span>{label}</span>
						<span class="small muted">{hint}</span>
					</label>
					<select id="event-{e}" value={eventPriority(e)} onchange={(ev) => setEventPriority(e, ev.currentTarget.value)}>
						{#each priorities as [v, plabel] (v)}<option value={v}>{plabel}</option>{/each}
					</select>
				</li>
			{/each}
		</ul>
	</div>
	<Field {form} name="notify_batch_s" />
</section>

<section class="card stack" aria-labelledby="sound">
	<div class="spread">
		<h2 id="sound">Alert sound</h2>
		<span class="chip" title="Kept in this browser, not saved with Settings">This device only</span>
	</div>
	<div class="setting" id="setting-alert_sound">
		<label class="check">
			<input type="checkbox" checked={alerts.sound} onchange={(e) => (alerts.sound = e.currentTarget.checked)} />
			Play a sound when something needs attention
		</label>
	</div>
	<p class="small muted">
		A disc waiting for review, a clash, or a failed job, while a page is open. Browsers stay silent until you've
		clicked somewhere on the page once.
	</p>
	<div><button type="button" onclick={() => alerts.chime()}>Play the sound</button></div>
</section>

<style>
	p {
		margin: 0;
	}
	.warn-note {
		color: var(--warn);
	}
	.events {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.events li {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 8px 0;
		border-top: 1px solid var(--border);
	}
	.events li:first-child {
		border-top: none;
	}
	.events label {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 0;
	}
	.events select {
		width: auto;
		min-width: 11em;
		flex-shrink: 0;
	}
	.muted-list {
		opacity: 0.75;
	}
	.chip {
		white-space: nowrap;
	}
	@media (max-width: 480px) {
		.events li {
			flex-direction: column;
			align-items: stretch;
			gap: 6px;
		}
		.events select {
			width: 100%;
		}
	}
</style>
