<script lang="ts">
	// Scanning a stack of sheets for the shelf, and a stack held between its two passes
	// (or because the passes didn't pair up).
	import type { ScannerInfo, ShelfStack, StackPage } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';

	let { stack, scanner, onchange }: { stack: ShelfStack | null; scanner: ScannerInfo | null; onchange: () => void } =
		$props();
	const act = new Action();

	type Mode = ShelfStack['mode'];
	const MODES: [Mode, string, string][] = [
		['duplex', 'Both sides at once', 'The feeder scans both sides of each sheet (duplex).'],
		['two_pass', 'Two passes (turn the stack over)', 'For a feeder without duplex: one side, then the other.'],
		['one_side', 'One side only', 'Each sheet makes a cover with just its outside.']
	];
	// The last choice is remembered in this browser (two passes, say, for a scanner whose
	// duplex doesn't work although it says it has one).
	const KEY = 'ripshelf.shelf-mode';
	function remembered(): Mode {
		try {
			const m = localStorage.getItem(KEY);
			if (m && MODES.some(([k]) => k === m)) return m as Mode;
		} catch {
			/* no storage here */
		}
		return 'duplex';
	}
	let mode = $state<Mode>(remembered());
	$effect(() => {
		if (scanner?.ok && !scanner.duplex && mode === 'duplex') mode = 'two_pass';
	});
	$effect(() => {
		try {
			localStorage.setItem(KEY, mode);
		} catch {
			/* no storage here */
		}
	});

	const scanning = $derived(stack?.state === 'scanning');
	const feeder = $derived(scanner?.sources.includes('feeder') ?? false);
	const busyElsewhere = $derived(scanner?.busy_job != null);

	async function run(fn: () => Promise<unknown>) {
		await act.run(fn);
		onchange();
	}

	const passName = (n: number) => (n === 1 ? 'first pass (outsides)' : 'second pass (insides)');
	const passes = $derived<{ title: string; pages: StackPage[]; second: boolean }[]>(
		stack
			? [
					{ title: 'First pass (outsides)', pages: stack.first, second: false },
					{ title: 'Second pass (insides, as they came out)', pages: stack.second, second: true }
				]
			: []
	);
</script>

<div class="stack-panel stack">
	{#if !stack}
		{#if scanner?.ok && feeder}
			<fieldset class="modes">
				<legend class="small muted">Scan a stack from the feeder</legend>
				{#each MODES as [m, label, hint] (m)}
					<label class="check small" title={hint}>
						<input type="radio" name="shelf-mode" value={m} bind:group={mode} disabled={m === 'duplex' && !scanner.duplex} />
						{label}{#if m === 'duplex' && !scanner.duplex}<span class="muted"> (the scanner has no duplex)</span>{/if}
					</label>
				{/each}
			</fieldset>
			<div class="row">
				<button class="primary" disabled={act.busy || busyElsewhere} onclick={() => run(() => api.shelfScan({ mode }))}>
					Scan a stack
				</button>
				{#if busyElsewhere}<span class="small muted">The scanner is busy with job {scanner.busy_job}.</span>{/if}
			</div>
		{:else if scanner?.ok}
			<p class="small muted">The scanner has no feeder, so stacks can't be scanned. Upload the pages instead.</p>
		{:else if scanner}
			<p class="small muted">{scanner.message ?? "The scanner isn't set up."} You can still upload pictures and PDFs.</p>
		{/if}
	{:else}
		<div class="card held" role="status">
			{#if scanning}
				<p><span class="spinner" aria-hidden="true"></span>
					{#if stack.mode === 'two_pass'}Scanning the {passName(stack.pass_no)}: {stack.pages} page{stack.pages === 1 ? '' : 's'} so far…
					{:else}Scanning: {stack.pages} page{stack.pages === 1 ? '' : 's'}, {stack.covers} cover{stack.covers === 1 ? '' : 's'} so far…{/if}
				</p>
				<div class="row"><button disabled={act.busy} onclick={() => run(() => api.shelfCancelStack())}>Stop</button></div>
			{:else if stack.state === 'flip'}
				<p><strong>Now turn the stack over and scan the other side.</strong></p>
				<p class="small muted">
					{stack.first.length} sheet{stack.first.length === 1 ? '' : 's'} scanned. Put the stack back in the feeder face down, in the same order, without
					shuffling it.
				</p>
				<div class="row">
					<button class="primary" disabled={act.busy || busyElsewhere} onclick={() => run(() => api.shelfScanSecond())}>Scan the other side</button>
					<button class="danger" disabled={act.busy} onclick={() => run(() => api.shelfCancelStack())}>Cancel and discard</button>
				</div>
			{:else if stack.state === 'mismatch'}
				<p>{stack.message}</p>
				<p class="small muted">Nothing is paired until both passes have as many pages, so one sheet fed twice can't shift every cover after it.</p>
				<div class="passes">
					{#each passes as pass (pass.title)}
						<section>
							<h3 class="small">{pass.title} · {pass.pages.length}</h3>
							<ol class="pages">
								{#each pass.pages as p, i (p.id)}
									{@const label = pass.second ? `Page ${i + 1} (sheet ${pass.pages.length - i})` : `Page ${i + 1}`}
									<li>
										<img src={api.shelfPageUrl(p.id)} alt={label} loading="lazy" />
										<span class="small">{label}{p.blank ? ' · blank' : ''}</span>
										<button class="link small danger" disabled={act.busy} onclick={() => run(() => api.shelfDropPage(p.id))}>Leave out</button>
									</li>
								{/each}
							</ol>
						</section>
					{/each}
				</div>
				<div class="row">
					<button class="primary" disabled={act.busy || stack.first.length !== stack.second.length} onclick={() => run(() => api.shelfPairStack())}>
						Make the covers
					</button>
					{#if stack.source === 'scanner'}
						<button disabled={act.busy || busyElsewhere} onclick={() => run(() => api.shelfScanSecond())}>Scan the second pass again</button>
					{/if}
					<button class="danger" disabled={act.busy} onclick={() => run(() => api.shelfCancelStack())}>Cancel and discard</button>
				</div>
			{:else}
				<p class="error-text">{stack.message ?? 'The scan failed'}</p>
				<div class="row">
					{#if stack.mode === 'two_pass' && stack.pass_no === 2}
						<button class="primary" disabled={act.busy} onclick={() => run(() => api.shelfScanSecond())}>Scan the second pass again</button>
					{:else}
						<button class="primary" disabled={act.busy} onclick={() => run(() => api.shelfScan({ mode: stack!.mode }))}>Scan again</button>
					{/if}
					<button disabled={act.busy} onclick={() => run(() => api.shelfCancelStack())}>{stack.first.length ? 'Discard' : 'Dismiss'}</button>
				</div>
			{/if}
		</div>
	{/if}
	{#if act.error}<p class="small error-text">{act.error}</p>{/if}
</div>

<style>
	.stack-panel {
		gap: 8px;
	}
	p {
		margin: 0;
	}
	.modes {
		border: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.modes legend {
		padding: 0;
		margin-bottom: 4px;
	}
	.held {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.passes {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr));
		gap: 10px;
	}
	h3 {
		margin: 0 0 6px;
	}
	.pages {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
		gap: 6px;
	}
	.pages li {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.pages img {
		width: 100%;
		aspect-ratio: 16 / 10;
		object-fit: contain;
		background: var(--surface-2);
		border-radius: 4px;
	}
</style>
