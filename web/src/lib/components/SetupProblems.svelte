<script lang="ts">
	import { live } from '../live.svelte';
	import Command from './Command.svelte';
	// Only these problems (by code), as in a Settings section. Settings itself needs no link to Settings.
	let { codes, link = !codes }: { codes?: string[]; link?: boolean } = $props();
	const problems = $derived((live.status?.problems ?? []).filter((p) => !codes || codes.includes(p.code)));
</script>

{#if problems.length}
	<section class="card setup">
		<h2>Setup</h2>
		<ul>
			{#each problems as p, i (i)}
				<li class:serious={['makemkv_eula', 'worker_makemkv_eula', 'drive_device', 'volume_key_unknown', 'decrypt_failed', 'makemkv_key', 'makemkv_missing'].includes(p.code)}>
					{p.message}
					{#if p.command}<Command text={p.command} />{/if}
				</li>
			{/each}
		</ul>
		{#if link}<a href="/settings">Open settings</a>{/if}
	</section>
{/if}

<style>
	.setup {
		background: var(--warn-bg);
		border-color: transparent;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	ul {
		margin: 0;
		padding-left: 18px;
		font-size: 0.9rem;
	}
	.serious {
		color: var(--bad);
		font-weight: 600;
	}
</style>
