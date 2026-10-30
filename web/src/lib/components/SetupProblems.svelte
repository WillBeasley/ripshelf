<script lang="ts">
	import { live } from '../live.svelte';
	import Command from './Command.svelte';
	const problems = $derived(live.status?.problems ?? []);
</script>

{#if problems.length}
	<section class="card setup">
		<h2>Setup</h2>
		<ul>
			{#each problems as p, i (i)}
				<li class:serious={['makemkv_eula', 'worker_makemkv_eula', 'volume_key_unknown', 'decrypt_failed', 'makemkv_key', 'makemkv_missing'].includes(p.code)}>
					{p.message}
					{#if p.command}<Command text={p.command} />{/if}
				</li>
			{/each}
		</ul>
		<a href="/settings">Open settings</a>
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
