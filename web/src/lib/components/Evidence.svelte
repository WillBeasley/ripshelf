<script lang="ts">
	import type { Evidence } from '../api-types';
	let { items }: { items: Evidence[] } = $props();
</script>

{#if items.length}
	<ul>
		{#each items as e, i (i)}
			<li class={e.ok === true ? 'ok' : e.ok === false ? 'bad' : 'neutral'}>
				<span class="mark" aria-hidden="true">{e.ok === true ? '✓' : e.ok === false ? '✗' : '•'}</span>
				{e.text}
			</li>
		{/each}
	</ul>
{/if}

<style>
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 3px;
		font-size: 0.87rem;
	}
	li {
		display: flex;
		gap: 7px;
	}
	.mark {
		width: 1em;
		flex: none;
		font-weight: 700;
	}
	.ok .mark {
		color: var(--ok);
	}
	.bad .mark {
		color: var(--bad);
	}
	.neutral .mark {
		color: var(--muted);
	}
</style>
