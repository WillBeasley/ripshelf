<script lang="ts">
	// The release (the box) on the TheDiscDB panel: UPC, ASIN, date, locale, region and
	// covers. TheDiscDB's import takes the UPC and the covers; the rest is typed into its
	// form when finishing, so they're listed there, ready to copy.
	import type { ContributionView, ReleaseDetails, ReleaseLookup, ReleaseProduct } from '../api-types';
	import { api } from '../api';
	import { Action } from '../actions.svelte';
	import Command from './Command.svelte';

	type Side = 'front' | 'back';
	let {
		jobId,
		view,
		onview
	}: { jobId: number; view: ContributionView; onview: (v: ContributionView) => void } = $props();
	const act = new Action();

	const c = $derived(view.contribution);
	const saved = $derived(c?.details);
	const sending = $derived(c?.status === 'sending');
	const sent = $derived(c?.status === 'sent');

	const blank = (): ReleaseDetails => ({
		upc: null,
		asin: null,
		asin_unavailable: false,
		release_date: null,
		locale: null,
		region_code: null
	});
	let form = $state<ReleaseDetails>(blank());
	let editing = $state(false);
	let found = $state<ReleaseLookup | null>(null);
	let coverV = $state(Date.now());

	// A cover set elsewhere (from a scan) is a new picture at the same address.
	$effect(() => {
		c?.covers.join();
		coverV = Date.now();
	});

	// Keep the form in step with what is saved, unless the user is typing.
	$effect(() => {
		const s = saved;
		if (!editing) form = { ...blank(), ...(s ?? {}) };
	});

	const dirty = $derived(JSON.stringify({ ...blank(), ...(saved ?? {}) }) !== JSON.stringify(form));
	const upcOk = $derived(!form.upc || /^\d{12,13}$/.test(form.upc.trim()));
	const asinOk = $derived(!form.asin || /^[A-Za-z0-9]{10}$/.test(form.asin.trim()));

	function clean(d: ReleaseDetails): ReleaseDetails {
		const t = (v: string | null) => (v && v.trim() ? v.trim() : null);
		return { ...d, upc: t(d.upc), asin: t(d.asin), locale: t(d.locale), region_code: t(d.region_code) };
	}

	async function save() {
		const v = await act.run(() => api.contributionDetails(jobId, clean(form)));
		if (v) {
			editing = false;
			onview(v);
		}
	}

	async function lookup() {
		const upc = form.upc && /^\d{12,13}$/.test(form.upc.trim()) ? form.upc.trim() : null;
		const r = await act.run(() => api.releaseLookup(jobId, upc));
		if (r) found = r;
	}

	// Suggestions only fill what's empty; a product fills its UPC and ASIN.
	function useSuggested() {
		if (!found) return;
		editing = true;
		const s = found.suggested;
		form = {
			...form,
			upc: form.upc || s.upc,
			asin: form.asin || s.asin,
			release_date: form.release_date || s.release_date,
			locale: form.locale || s.locale,
			region_code: form.region_code || s.region_code
		};
	}

	function useProduct(p: ReleaseProduct) {
		editing = true;
		form = { ...form, upc: p.upc ?? form.upc, asin: p.asin ?? form.asin, asin_unavailable: false };
		useSuggested();
	}

	async function upload(side: Side, e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		if (!file) return;
		const v = await act.run(() => api.uploadCover(jobId, side, file));
		if (v) {
			coverV = Date.now();
			onview(v);
		}
	}

	async function coverFrom(url: string) {
		const v = await act.run(() => api.coverFromUrl(jobId, 'front', url));
		if (v) {
			coverV = Date.now();
			onview(v);
		}
	}

	async function removeCover(side: Side) {
		const v = await act.run(() => api.deleteCover(jobId, side));
		if (v) onview(v);
	}

	// What TheDiscDB's "finish" form asks for, in its order.
	const toCopy = $derived(
		[
			['Release date', saved?.release_date],
			['ASIN', saved?.asin_unavailable ? null : saved?.asin],
			['UPC', saved?.upc],
			['Locale', saved?.locale],
			['Region code', saved?.region_code]
		].filter((x): x is [string, string] => !!x[1])
	);
</script>

<div class="release">
	<h4>Release</h4>
	<p class="small muted">
		The box rather than the disc. TheDiscDB's form asks for all of this when you finish; the UPC and the covers go
		with the disc, and the rest is shown there for you to copy.
	</p>
	<div class="grid">
		<label>
			<span>UPC / EAN</span>
			<input
				inputmode="numeric"
				placeholder="Barcode on the box"
				maxlength="13"
				bind:value={form.upc}
				oninput={() => (editing = true)}
				class:bad={!upcOk}
			/>
		</label>
		<div class="field">
			<label for="asin-{jobId}">ASIN</label>
			<input
				id="asin-{jobId}"
				placeholder="Amazon's 10-character id"
				maxlength="10"
				bind:value={form.asin}
				disabled={form.asin_unavailable}
				oninput={() => (editing = true)}
				class:bad={!asinOk}
			/>
			<label class="check">
				<input type="checkbox" bind:checked={form.asin_unavailable} onchange={() => (editing = true)} />
				<span class="small">Not sold on Amazon</span>
			</label>
		</div>
		<label>
			<span>Release date</span>
			<input type="date" bind:value={form.release_date} oninput={() => (editing = true)} />
		</label>
		<label>
			<span>Locale</span>
			<input placeholder="en-GB" maxlength="16" bind:value={form.locale} oninput={() => (editing = true)} />
		</label>
		<label>
			<span>Region code</span>
			<input
				placeholder="2, B or Region free"
				maxlength="20"
				bind:value={form.region_code}
				oninput={() => (editing = true)}
			/>
		</label>
	</div>
	<div class="row">
		<button class:primary={dirty} disabled={act.busy || sending || !dirty || !upcOk || !asinOk} onclick={save}
			>Save details</button
		>
		<button disabled={act.busy || sending} onclick={lookup}>{act.busy ? 'Looking…' : 'Look up'}</button>
		{#if dirty}
			<button
				class="link small"
				onclick={() => {
					editing = false;
					form = { ...blank(), ...(saved ?? {}) };
				}}>Undo changes</button
			>
		{/if}
	</div>
	{#if found}
		<div class="found">
			{#if found.products.length}
				<p class="small"><strong>Listings that may be this release</strong> (check against the box):</p>
				<ul class="products">
					{#each found.products as p, i (i)}
						<li>
							{#if p.images[0]}<img src={p.images[0]} alt="" referrerpolicy="no-referrer" loading="lazy" />{/if}
							<div class="what">
								<div>{p.title}{p.brand ? ` · ${p.brand}` : ''}</div>
								<div class="small muted mono">
									{p.upc ? `UPC ${p.upc}` : 'no UPC'}{p.asin ? ` · ASIN ${p.asin}` : ''}
								</div>
								<div class="row">
									<button class="link small" onclick={() => useProduct(p)}>Use this</button>
									{#if p.images[0] && !sending}
										<button class="link small" disabled={act.busy} onclick={() => coverFrom(p.images[0])}
											>Use its picture as the front cover</button
										>
									{/if}
								</div>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
			{#if Object.values(found.suggested).some((v) => v)}
				<button class="link small" onclick={useSuggested}>Fill the empty fields with the suggestions</button>
			{/if}
			<ul class="notes small muted">
				{#each found.notes as n, i (i)}<li>{n}</li>{/each}
			</ul>
			<p class="small muted">Nothing is saved until you press Save details.</p>
		</div>
	{/if}

	<h4>Covers</h4>
	<p class="small muted">
		A photo or scan of this release's box (JPEG, PNG or WebP, up to 20 MB), not the film's poster.
	</p>
	<div class="covers">
		{#each ['front', 'back'] as const as side (side)}
			<div class="cover">
				{#if c?.covers.includes(side)}
					<img src={api.coverUrl(jobId, side, coverV)} alt="{side} cover" />
				{:else}
					<div class="empty small muted">No {side} cover</div>
				{/if}
				<div class="row">
					<label class="button small" class:disabled={act.busy || sending}>
						{c?.covers.includes(side) ? 'Replace' : `Add ${side}`}
						<input
							type="file"
							accept="image/jpeg,image/png,image/webp"
							disabled={act.busy || sending}
							onchange={(e) => upload(side, e)}
						/>
					</label>
					{#if c?.covers.includes(side)}
						<button class="link small" disabled={act.busy || sending} onclick={() => removeCover(side)}
							>Remove</button
						>
					{/if}
				</div>
			</div>
		{/each}
	</div>

	{#if sent}
		<h4>Finishing on TheDiscDB</h4>
		{#if toCopy.length}
			<p class="small muted">Its form asks for these. Copy each one across:</p>
			{#each toCopy as [label, value] (label)}
				<div class="copy"><span class="small">{label}</span><Command text={value} /></div>
			{/each}
		{/if}
		<p class="small muted">
			After you create the contribution there, add the audio track names (such as commentaries), subtitle names and
			chapter names on its edit page. TheDiscDB's import has no fields for them yet.
		</p>
	{/if}
	{#if act.error}<p class="small error-text">{act.error}</p>{/if}
</div>

<style>
	.release {
		display: flex;
		flex-direction: column;
		gap: 6px;
		margin-top: 6px;
	}
	h4 {
		margin: 6px 0 0;
		font-size: 0.85rem;
	}
	p {
		margin: 0;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
		gap: 8px;
	}
	.grid > label,
	.grid .field {
		display: flex;
		flex-direction: column;
		gap: 2px;
		font-size: 0.8rem;
	}
	.grid label.check {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.grid input.bad {
		border-color: var(--danger, #c33);
	}
	.found {
		display: flex;
		flex-direction: column;
		gap: 4px;
		padding: 8px;
		border-radius: 8px;
		background: var(--surface-2);
	}
	.products,
	.notes {
		margin: 0;
		padding: 0;
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.notes {
		padding-left: 1em;
		list-style: disc;
		gap: 2px;
	}
	.products li {
		display: flex;
		gap: 8px;
		align-items: flex-start;
	}
	.products img {
		width: 48px;
		height: 60px;
		object-fit: contain;
		border-radius: 4px;
		background: var(--bg);
	}
	.what {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.covers {
		display: flex;
		flex-wrap: wrap;
		gap: 12px;
	}
	.cover {
		display: flex;
		flex-direction: column;
		gap: 4px;
		width: 140px;
	}
	.cover img,
	.cover .empty {
		width: 140px;
		height: 180px;
		object-fit: contain;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--bg);
	}
	.cover .empty {
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.cover label.button {
		position: relative;
		cursor: pointer;
	}
	.cover label.button.disabled {
		opacity: 0.5;
		pointer-events: none;
	}
	.cover input[type='file'] {
		position: absolute;
		inset: 0;
		opacity: 0;
		width: 100%;
		cursor: pointer;
	}
	.copy {
		display: grid;
		grid-template-columns: 90px 1fr;
		align-items: center;
		gap: 6px;
	}
</style>
