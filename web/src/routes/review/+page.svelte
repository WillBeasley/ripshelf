<script lang="ts">
	import { live } from '$lib/live.svelte';
	import ReviewCard from '$lib/components/ReviewCard.svelte';
	import TvReviewCard from '$lib/components/TvReviewCard.svelte';
	import ClashCard from '$lib/components/ClashCard.svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';

	// Once the last item here is dealt with, go back to the drives.
	let hadItems = false;
	$effect(() => {
		const n = live.reviewJobs.length;
		if (!live.loaded) return;
		if (n) hadItems = true;
		else if (hadItems) goto('/');
	});

	// Notification links like /review#job-12 point at one card. It only exists once the
	// jobs have loaded, so the browser's own jump to the anchor misses it.
	let scrolledTo = '';
	$effect(() => {
		const hash = page.url.hash;
		if (!live.loaded || !live.reviewJobs.length || !hash.startsWith('#job-') || hash === scrolledTo) return;
		const card = document.getElementById(hash.slice(1));
		if (!card) return;
		scrolledTo = hash;
		card.scrollIntoView({ block: 'start' });
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
	.grid :global(article[id]) {
		scroll-margin-top: 4.5rem;
	}
	.wide {
		grid-column: 1 / -1;
		max-width: 760px;
	}
</style>
