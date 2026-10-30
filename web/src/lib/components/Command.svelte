<script lang="ts">
	// A command to run, on its own line, with a button that copies it.
	let { text }: { text: string } = $props();
	let copied = $state(false);

	// The clipboard API only works over https (or on localhost); plain http to another
	// machine falls back to copying a selected textarea.
	function fallbackCopy(): boolean {
		const ta = document.createElement('textarea');
		ta.value = text;
		ta.setAttribute('readonly', '');
		ta.style.position = 'fixed';
		ta.style.opacity = '0';
		document.body.appendChild(ta);
		ta.select();
		let ok = false;
		try {
			ok = document.execCommand('copy');
		} catch {
			ok = false;
		}
		ta.remove();
		return ok;
	}

	async function copy() {
		let ok = false;
		if (navigator.clipboard && window.isSecureContext) {
			try {
				await navigator.clipboard.writeText(text);
				ok = true;
			} catch {
				ok = false;
			}
		}
		if (!ok) ok = fallbackCopy();
		copied = ok;
		if (ok) setTimeout(() => (copied = false), 1500);
	}
</script>

<div class="cmd">
	<code>{text}</code>
	<button type="button" onclick={copy} aria-label="Copy the command">{copied ? 'Copied' : 'Copy'}</button>
</div>

<style>
	.cmd {
		display: flex;
		align-items: center;
		gap: 8px;
		margin: 4px 0;
		padding: 6px 6px 6px 10px;
		border: 1px solid var(--border);
		border-radius: 8px;
		background: var(--bg);
		max-width: 100%;
	}
	code {
		flex: 1;
		font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
		font-size: 13px;
		overflow-x: auto;
		white-space: nowrap;
	}
	button {
		padding: 2px 10px;
		font-size: 13px;
	}
</style>
