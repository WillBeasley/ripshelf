<script lang="ts">
	// A list of strings typed as text: one per line, or separated by commas.
	let {
		value = $bindable(),
		multiline = false,
		...rest
	}: { value: string[]; multiline?: boolean; [k: string]: unknown } = $props();

	const join = (v: string[]) => v.join(multiline ? '\n' : ', ');
	const split = (s: string) => s.split(multiline ? '\n' : /[\s,]+/).map((x) => x.trim()).filter(Boolean);
	let text = $state(join(value));
	// Follow a value changed from outside (loaded, discarded), but not one this typing made.
	$effect(() => {
		if (join(split(text)) !== join(value)) text = join(value);
	});
	function input(s: string) {
		text = s;
		value = split(s);
	}
</script>

{#if multiline}
	<textarea rows="2" {...rest} value={text} oninput={(e) => input(e.currentTarget.value)}></textarea>
{:else}
	<input {...rest} value={text} oninput={(e) => input(e.currentTarget.value)} />
{/if}
