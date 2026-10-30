<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { live } from '$lib/live.svelte';
	import { alerts } from '$lib/alerts.svelte';
	import JobPanel from '$lib/components/JobPanel.svelte';

	let { children } = $props();

	live.start();
	alerts.start();

	const jobParam = $derived(page.url.searchParams.get('job'));
	const jobId = $derived(jobParam && /^\d+$/.test(jobParam) ? Number(jobParam) : null);
	const reviewCount = $derived(live.reviewJobs.length || live.status?.review_count || 0);

	const tabs = [
		{ href: '/', label: 'Overview', icon: '◎' },
		{ href: '/review', label: 'Review', icon: '?' },
		{ href: '/finished', label: 'Finished', icon: '✓' },
		{ href: '/collections', label: 'Shows', icon: '▦' },
		{ href: '/settings', label: 'Settings', icon: '⚙' }
	];
	// A fresh install starts at the setup page (once, because its links to Settings must work).
	let sentToSetup = false;
	$effect(() => {
		if (live.loaded && !live.setupComplete && !sentToSetup && page.url.pathname !== '/setup') {
			sentToSetup = true;
			goto('/setup', { replaceState: true });
		}
	});
	const isActive = (href: string) =>
		href === '/' ? page.url.pathname === '/' : page.url.pathname.startsWith(href);
</script>

<div class="app">
	<header class="top">
		<a class="brand" href="/"><img src="/brand/logo.svg" alt="" width="22" height="22" />ripshelf</a>
		<nav class="tabs desktop" aria-label="Main">
			{#each tabs as t (t.href)}
				<a href={t.href} class:active={isActive(t.href)} class:attention={t.href === '/review' && reviewCount > 0} aria-current={isActive(t.href) ? 'page' : undefined}>
					{t.label}
					{#if t.href === '/review' && reviewCount}<span class="badge">{reviewCount}</span>{/if}
				</a>
			{/each}
		</nav>
		<span class="conn" class:on={live.connected} title={live.connected ? 'Live' : 'Reconnecting…'}></span>
	</header>

	{#if live.status?.fake_hardware}
		<div class="banner info">Fake hardware. These are simulated drives replaying recorded MakeMKV logs.</div>
	{/if}
	{#if live.loaded && !live.setupComplete && page.url.pathname !== '/setup'}
		<div class="banner info">Setup isn't finished, so discs wait. Nothing is scanned or ripped until it is. <a href="/setup">Continue the setup</a></div>
	{/if}
	{#if live.error && !live.loaded}
		<div class="banner bad">Cannot reach the server ({live.error})</div>
	{/if}

	<main>
		{@render children()}
	</main>

	<nav class="tabs mobile" aria-label="Main">
		{#each tabs as t (t.href)}
			<a href={t.href} class:active={isActive(t.href)} class:attention={t.href === '/review' && reviewCount > 0} aria-current={isActive(t.href) ? 'page' : undefined}>
				<span class="icon" aria-hidden="true">{t.icon}</span>
				<span>{t.label}</span>
				{#if t.href === '/review' && reviewCount}<span class="badge">{reviewCount}</span>{/if}
			</a>
		{/each}
	</nav>
</div>

{#if jobId != null}
	<JobPanel {jobId} />
{/if}

<style>
	.app {
		min-height: 100dvh;
		display: flex;
		flex-direction: column;
	}
	.top {
		position: sticky;
		top: 0;
		z-index: 10;
		display: flex;
		align-items: center;
		gap: 18px;
		padding: 10px 16px;
		padding-top: calc(10px + env(safe-area-inset-top));
		background: color-mix(in srgb, var(--bg) 92%, transparent);
		backdrop-filter: blur(8px);
		border-bottom: 1px solid var(--border);
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 7px;
		font-weight: 700;
		color: var(--text);
		text-decoration: none;
		font-size: 1.05rem;
	}
	.conn {
		margin-left: auto;
		width: 9px;
		height: 9px;
		border-radius: 50%;
		background: var(--bad);
	}
	.conn.on {
		background: var(--ok);
	}
	main {
		flex: 1;
		width: 100%;
		max-width: 1200px;
		margin: 0 auto;
		padding: 16px 16px calc(84px + env(safe-area-inset-bottom));
	}
	.banner {
		padding: 8px 16px;
		font-size: 0.85rem;
	}
	.banner.info {
		background: var(--info-bg);
		color: var(--info);
	}
	.banner.bad {
		background: var(--bad-bg);
		color: var(--bad);
	}
	.tabs a {
		position: relative;
		text-decoration: none;
		color: var(--muted);
		font-weight: 500;
	}
	.tabs a.active {
		color: var(--accent);
	}
	.tabs a.attention {
		color: var(--warn);
		font-weight: 700;
	}
	.badge {
		display: inline-grid;
		place-items: center;
		min-width: 18px;
		height: 18px;
		padding: 0 5px;
		border-radius: 999px;
		background: var(--warn);
		color: var(--bg);
		font-size: 0.72rem;
		font-weight: 700;
	}
	.desktop {
		display: none;
		gap: 18px;
	}
	.mobile {
		position: fixed;
		bottom: 0;
		left: 0;
		right: 0;
		z-index: 10;
		display: grid;
		grid-template-columns: repeat(5, 1fr);
		background: var(--surface);
		border-top: 1px solid var(--border);
		padding-bottom: env(safe-area-inset-bottom);
	}
	.mobile a {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 1px;
		padding: 8px 0 7px;
		font-size: 0.74rem;
	}
	.mobile .icon {
		font-size: 1.1rem;
		line-height: 1.2;
	}
	.mobile .badge {
		position: absolute;
		top: 4px;
		left: calc(50% + 6px);
	}
	@media (min-width: 760px) {
		.desktop {
			display: flex;
		}
		.mobile {
			display: none;
		}
		main {
			padding-bottom: 32px;
		}
	}
</style>
