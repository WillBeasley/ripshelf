<script lang="ts">
	// "Test" next to a connection setting tries it with the values as typed, without saving.
	import type { SettingsTestRequest, SettingsTestResult, SettingsUpdate } from '$lib/api-types';
	import { api } from '$lib/api';
	import { Action } from '$lib/actions.svelte';

	let {
		check,
		values,
		label = 'Test'
	}: { check: SettingsTestRequest['check']; values: () => SettingsUpdate; label?: string } =
		$props();
	const act = new Action();
	let result = $state<SettingsTestResult | null>(null);

	async function run() {
		result = null;
		result = (await act.run(() => api.testSettings({ check, values: values() }))) ?? null;
	}
</script>

<div class="test">
	<button type="button" onclick={run} disabled={act.busy}>{act.busy ? 'Testing…' : label}</button>
	{#if result}
		<span class="small" class:ok={result.ok} class:error-text={!result.ok}>{result.ok ? '✓' : '✗'} {result.message}</span>
		{#each result.details as d (d)}<span class="small muted detail">{d}</span>{/each}
	{/if}
	{#if act.error}<span class="small error-text">{act.error}</span>{/if}
</div>

<style>
	.test {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 10px;
	}
	.ok {
		color: var(--ok);
	}
	.detail {
		flex-basis: 100%;
	}
</style>
