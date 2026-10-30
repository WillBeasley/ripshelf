// Live application state, loaded once, then kept current by the /api/events stream.
import { api } from './api';
import type {
	Drive,
	DriveUpdated,
	Job,
	JobEvent,
	JobEventAdded,
	JobUpdated,
	JobsDeleted,
	RipProgress,
	RipProgressEvent,
	Status,
	StatusChanged
} from './api-types';

type ServerEvent =
	| JobUpdated
	| DriveUpdated
	| RipProgressEvent
	| JobEventAdded
	| StatusChanged
	| JobsDeleted;

export function isAfterDrive(j: Job): boolean {
	return (j.state === 'transcoding' || j.state === 'finalising') && j.rip.state === 'done';
}

class Live {
	drives = $state<Drive[]>([]);
	jobs = $state<Record<number, Job>>({});
	progress = $state<Record<number, RipProgress>>({});
	status = $state<Status | null>(null);
	threshold = $state(0.9);
	// False on a fresh install, when the layout sends the browser to the setup page.
	setupComplete = $state(true);
	connected = $state(false);
	loaded = $state(false);
	error = $state<string | null>(null);

	#eventListeners = new Set<(e: JobEvent) => void>();
	#source: EventSource | null = null;

	get reviewJobs(): Job[] {
		return Object.values(this.jobs)
			.filter((j) => j.state === 'review' || j.state === 'clash' || j.state === 'checking')
			.sort((a, b) => a.created_at.localeCompare(b.created_at));
	}

	get finishedJobs(): Job[] {
		return Object.values(this.jobs)
			.filter((j) => j.state === 'done' || j.state === 'failed' || j.state === 'cancelled')
			.sort((a, b) => (b.finished_at ?? b.updated_at).localeCompare(a.finished_at ?? a.updated_at));
	}

	// Ripped and off the drive, now transcoding or copying to the library while
	// the drive takes the next disc.
	get afterDriveJobs(): Job[] {
		return Object.values(this.jobs)
			.filter(isAfterDrive)
			.sort((a, b) => a.created_at.localeCompare(b.created_at));
	}

	removeJobs(ids: number[]) {
		for (const id of ids) {
			delete this.jobs[id];
			delete this.progress[id];
		}
	}

	upsertDrive(drive: Drive) {
		const i = this.drives.findIndex((d) => d.id === drive.id);
		if (i >= 0) this.drives[i] = drive;
		else this.drives.push(drive);
	}

	// What to call the drive a job is on. Its name, else its model (else the device).
	driveName(job: Job): string {
		return this.drives.find((d) => d.id === job.drive_id)?.display_name || job.device || job.drive_id;
	}

	jobFor(drive: Drive): Job | null {
		return drive.job_id != null ? (this.jobs[drive.job_id] ?? null) : null;
	}

	progressFor(job: Job): RipProgress | null {
		return this.progress[job.id] ?? job.rip.progress ?? null;
	}

	start() {
		if (this.#source) return;
		this.#connect();
	}

	onJobEvent(fn: (e: JobEvent) => void): () => void {
		this.#eventListeners.add(fn);
		return () => this.#eventListeners.delete(fn);
	}

	upsertJob(job: Job) {
		this.jobs[job.id] = job;
		if (job.rip.state !== 'running') delete this.progress[job.id];
	}

	async refresh() {
		try {
			const [status, drives, jobs, settings] = await Promise.all([
				api.status(),
				api.drives(),
				api.jobs(),
				api.settings()
			]);
			this.status = status;
			this.drives = drives;
			const map: Record<number, Job> = {};
			for (const j of jobs) map[j.id] = j;
			this.jobs = map;
			this.threshold = settings.auto_confirm_threshold;
			this.setupComplete = settings.setup_complete ?? true;
			// Drive jobs older than the list window.
			for (const d of drives) {
				if (d.job_id != null && !map[d.job_id]) {
					api.job(d.job_id).then((j) => this.upsertJob(j)).catch(() => {});
				}
			}
			this.error = null;
			this.loaded = true;
		} catch (e) {
			this.error = e instanceof Error ? e.message : String(e);
		}
	}

	#connect() {
		const es = new EventSource('/api/events');
		this.#source = es;
		es.onopen = () => {
			this.connected = true;
			// Resync after every (re)connect, because events may have been missed.
			this.refresh();
		};
		es.onerror = () => {
			this.connected = false;
		};
		es.onmessage = (m) => {
			let ev: ServerEvent;
			try {
				ev = JSON.parse(m.data);
			} catch {
				return;
			}
			this.#apply(ev);
		};
	}

	#apply(ev: ServerEvent) {
		switch (ev.type) {
			case 'job':
				this.upsertJob(ev.job);
				break;
			case 'drive': {
				this.upsertDrive(ev.drive);
				if (ev.drive.job_id != null && !this.jobs[ev.drive.job_id]) {
					api.job(ev.drive.job_id).then((j) => this.upsertJob(j)).catch(() => {});
				}
				break;
			}
			case 'progress':
				this.progress[ev.job_id] = ev.progress;
				break;
			case 'event':
				for (const fn of this.#eventListeners) fn(ev.event);
				break;
			case 'status':
				this.status = ev.status;
				break;
			case 'jobs_deleted':
				this.removeJobs(ev.ids);
				break;
		}
	}
}

export const live = new Live();
