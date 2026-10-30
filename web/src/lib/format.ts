import type { Job, JobState } from './api-types';

// Binary units labelled GB, as MakeMKV prints them.
export function gb(bytes: number | null | undefined, digits = 1): string {
	if (bytes == null) return '–';
	return `${(bytes / 1024 ** 3).toFixed(digits)} GB`;
}

export function size(bytes: number | null | undefined): string {
	if (bytes == null) return '–';
	if (bytes >= 1024 ** 4) return `${(bytes / 1024 ** 4).toFixed(2)} TB`;
	if (bytes >= 1024 ** 3) return gb(bytes);
	return `${Math.round(bytes / 1024 ** 2)} MB`;
}

export function gbPerMin(bytesPerS: number | null | undefined): string {
	if (!bytesPerS) return '–';
	return `${((bytesPerS * 60) / 1024 ** 3).toFixed(2)} GB/min`;
}

export function duration(s: number | null | undefined): string {
	if (s == null) return '–';
	const h = Math.floor(s / 3600);
	const m = Math.floor((s % 3600) / 60);
	const sec = Math.floor(s % 60);
	return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
}

export function eta(s: number | null | undefined): string {
	if (s == null || !isFinite(s)) return '–';
	if (s < 60) return '< 1 min';
	const m = Math.round(s / 60);
	return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}

export function percent(f: number | null | undefined): string {
	return `${Math.floor((f ?? 0) * 100)}%`;
}

export function when(iso: string | null | undefined): string {
	if (!iso) return '–';
	const d = new Date(iso);
	const today = new Date();
	const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
	if (d.toDateString() === today.toDateString()) return time;
	return `${d.toLocaleDateString([], { day: 'numeric', month: 'short' })} ${time}`;
}

export const STATE_LABEL: Record<JobState, string> = {
	detected: 'Detected',
	scanning: 'Scanning',
	identifying: 'Identifying',
	review: 'Needs review',
	clash: 'Already in library',
	checking: 'Ejected to check',
	ripping: 'Ripping',
	transcoding: 'Transcoding',
	finalising: 'Finalising',
	done: 'Done',
	failed: 'Problem',
	cancelled: 'Cancelled'
};

export function jobTitle(job: Job): string {
	if (job.identity) return job.identity.year ? `${job.identity.title} (${job.identity.year})` : job.identity.title;
	return job.disc.name ?? job.disc.label ?? `Job ${job.id}`;
}

export function kindLabel(kind: string | null | undefined): string {
	return { dvd: 'DVD', bluray: 'Blu-ray', uhd: 'UHD Blu-ray', other: 'Disc' }[kind ?? ''] ?? 'Disc';
}

// The confirmed identity, else the best guess (TV show, or top film candidate), else the label.
export function guessTitle(job: Job): string {
	if (job.identity) return jobTitle(job);
	const show = job.tv?.show;
	if (show) return show.year ? `${show.name} (${show.year})` : show.name;
	const best = job.candidates.find((c) => !c.excluded);
	if (best) return best.year ? `${best.title} (${best.year})` : best.title;
	return jobTitle(job);
}

const PROVIDERS: Record<string, string> = {
	thediscdb: 'TheDiscDB',
	tmdb: 'TMDB',
	tvdb: 'TheTVDB',
	tvmaze: 'TVmaze',
	omdb: 'OMDb'
};

// "via TMDB · OMDb", the metadata providers that found a candidate.
export function via(providers: string[] | null | undefined): string {
	return providers?.length ? `via ${providers.map((p) => PROVIDERS[p] ?? p).join(' · ')}` : '';
}

/** What a transcode ran on, by ripshelf's encoder name. */
export const ENCODER_NAMES: Record<string, string> = {
	qsv: 'GPU, Quick Sync',
	vaapi: 'GPU, VAAPI',
	nvenc: 'GPU, NVENC',
	x265: 'x265'
};
