<script lang="ts">
	import { live } from '$lib/live.svelte';
	import ReviewCard from '$lib/components/ReviewCard.svelte';
	import TvReviewCard from '$lib/components/TvReviewCard.svelte';
	import ClashCard from '$lib/components/ClashCard.svelte';
	import { goto } from '$app/navigation';

	// Once the last item here is dealt with, go back to the drives.
	let hadItems = false;
	$effect(() => {
		const n = live.reviewJobs.length;
		if (!live.loaded) return;
		if (n) hadItems = true;
		else if (hadItems) goto('/');
	});
</script>

<svelte:head><title>Review · ripshelf</title></svelte:head>

<div class="stack">
	<h1>Review</h1>
	{#if live.loaded && !live.reviewJobs.length}
		<p class="muted">Nothing needs review. Discs that can't be identified with confidence, and every TV disc, show up here.</p>
	{/if}
	<div class="grid">
		{#each live.reviewJobs as job (job.id)}
			{#if job.state === 'clash' || (job.state === 'checking' && !(job.media === 'tv' && job.checking_from === 'review'))}
				<ClashCard {job} />
			{:else if job.media === 'tv'}
				<div class="wide"><TvReviewCard {job} /></div>
			{:else}
				<ReviewCard {job} />
			{/if}
		{/each}
	</div>
</div>

<style>
	.wide {
		grid-column: 1 / -1;
		max-width: 760px;
	}
</style>
