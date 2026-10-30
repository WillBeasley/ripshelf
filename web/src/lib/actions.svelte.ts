// Run an API action with a busy flag and a visible error.
import { live } from './live.svelte';

export class Action {
	busy = $state(false);
	error = $state<string | null>(null);

	async run<T>(fn: () => Promise<T>): Promise<T | undefined> {
		this.busy = true;
		this.error = null;
		try {
			const out = await fn();
			if (out && typeof out === 'object' && 'state' in out && 'drive_id' in out) {
				live.upsertJob(out as never);
			}
			return out;
		} catch (e) {
			this.error = e instanceof Error ? e.message : String(e);
		} finally {
			this.busy = false;
		}
	}
}
