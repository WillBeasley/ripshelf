<script lang="ts">
	import type { DriveStatus, JobState } from '../api-types';
	import { STATE_LABEL } from '../format';

	let { state, drive }: { state?: JobState | null; drive?: DriveStatus } = $props();

	const DRIVE_LABEL: Record<DriveStatus, string> = {
		empty: 'Empty',
		tray_open: 'Tray open',
		not_ready: 'Reading…',
		disc: 'Disc in',
		error: 'Drive error'
	};

	const tone = $derived.by(() => {
		if (state) {
			if (state === 'failed') return 'bad';
			if (state === 'review' || state === 'clash' || state === 'checking') return 'warn';
			if (state === 'done') return 'ok';
			if (state === 'cancelled') return 'idle';
			return 'busy';
		}
		return drive === 'error' ? 'bad' : 'idle';
	});
	const text = $derived(state ? STATE_LABEL[state] : drive ? DRIVE_LABEL[drive] : '');
</script>

<span class="pill {tone}">{text}</span>

<style>
	.pill {
		font-size: 0.78rem;
		font-weight: 600;
		padding: 3px 10px;
		border-radius: 999px;
		white-space: nowrap;
		background: var(--surface-2);
		color: var(--muted);
	}
	.busy {
		background: var(--info-bg);
		color: var(--info);
	}
	.warn {
		background: var(--warn-bg);
		color: var(--warn);
	}
	.ok {
		background: var(--ok-bg);
		color: var(--ok);
	}
	.bad {
		background: var(--bad-bg);
		color: var(--bad);
	}
</style>
