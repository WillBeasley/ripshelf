// A sound in the browser when something new needs attention (review, clash, failure).
// The switch is per browser (localStorage), because it is about the speakers in front of you.
import { live } from './live.svelte';

const KEY = 'ripshelf.sound_alert';

function load(): boolean {
	try {
		return localStorage.getItem(KEY) === '1';
	} catch {
		return false;
	}
}

class Alerts {
	#sound = $state(false);
	#seen: Set<string> | null = null;
	#ctx: AudioContext | null = null;

	get sound() {
		return this.#sound;
	}

	set sound(on: boolean) {
		this.#sound = on;
		try {
			localStorage.setItem(KEY, on ? '1' : '0');
		} catch {
			// private window, so on for this page only
		}
		if (on) this.chime();
	}

	// Everything that needs attention now, as stable keys.
	#keys(): Set<string> {
		const out = new Set<string>();
		for (const j of live.reviewJobs) out.add(`review:${j.id}:${j.state}`);
		for (const j of Object.values(live.jobs)) if (j.state === 'failed') out.add(`failed:${j.id}`);
		for (const d of live.drives) if (d.problem) out.add(`drive:${d.id}:${d.problem.code}`);
		return out;
	}

	// Call from a component (an effect re-runs as live state changes).
	start() {
		this.#sound = load();
		$effect(() => {
			if (!live.loaded) return;
			const now = this.#keys();
			const fresh = this.#seen != null && [...now].some((k) => !this.#seen!.has(k));
			this.#seen = now; // the first load is the baseline, so no sound for what was already waiting
			if (fresh && this.#sound) this.chime();
		});
	}

	// Two short rising tones, made in the browser (no sound file).
	chime() {
		try {
			this.#ctx ??= new AudioContext();
			const ctx = this.#ctx;
			if (ctx.state === 'suspended') ctx.resume();
			const t0 = ctx.currentTime + 0.02;
			[660, 880].forEach((hz, i) => {
				const osc = ctx.createOscillator();
				const gain = ctx.createGain();
				const t = t0 + i * 0.18;
				osc.type = 'sine';
				osc.frequency.value = hz;
				gain.gain.setValueAtTime(0.0001, t);
				gain.gain.exponentialRampToValueAtTime(0.3, t + 0.02);
				gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.25);
				osc.connect(gain).connect(ctx.destination);
				osc.start(t);
				osc.stop(t + 0.3);
			});
		} catch {
			// no Web Audio, so stay silent
		}
	}
}

export const alerts = new Alerts();
