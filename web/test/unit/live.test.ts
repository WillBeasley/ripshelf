import { beforeEach, describe, expect, it } from 'vitest';
import type { Drive, Job, RipProgress } from '#lib/api-types.ts';
import { isAfterDrive, live } from '#lib/live.svelte.ts';

let next = 1;
function job(state: Job['state'], over: Record<string, unknown> = {}): Job {
	const id = next++;
	const t = `2026-11-0${id}T10:00:00Z`;
	return { id, state, drive_id: 'fake-pioneer', device: '/dev/sr0', created_at: t, updated_at: t, finished_at: null, rip: { state: 'done', progress: null }, ...over } as unknown as Job;
}

function put(...jobs: Job[]) {
	for (const j of jobs) live.upsertJob(j);
	return jobs;
}

beforeEach(() => {
	next = 1;
	live.jobs = {};
	live.progress = {};
	live.drives = [];
});

describe('live state', () => {
	it('lists the jobs waiting for you, oldest first', () => {
		const [clash, , review, checking] = put(job('clash'), job('ripping'), job('review'), job('checking'), job('done'));
		live.jobs[review.id].created_at = '2026-10-01T00:00:00Z';
		expect(live.reviewJobs.map((j) => j.id)).toEqual([review.id, clash.id, checking.id]);
	});

	it('lists finished jobs newest first, by when they finished', () => {
		const [done, failed, cancelled] = put(
			job('done', { finished_at: '2026-11-09T12:00:00Z' }),
			job('failed'),
			job('cancelled', { finished_at: '2026-11-01T12:00:00Z' }),
			job('review')
		);
		expect(live.finishedJobs.map((j) => j.id)).toEqual([done.id, failed.id, cancelled.id]);
	});

	it('knows when a job is off the drive', () => {
		expect(isAfterDrive(job('transcoding'))).toBe(true);
		expect(isAfterDrive(job('finalising'))).toBe(true);
		expect(isAfterDrive(job('transcoding', { rip: { state: 'running' } }))).toBe(false);
		expect(isAfterDrive(job('ripping'))).toBe(false);
		const [a] = put(job('transcoding'), job('ripping', { rip: { state: 'running' } }));
		expect(live.afterDriveJobs.map((j) => j.id)).toEqual([a.id]);
	});

	it('keeps rip progress only while the rip runs', () => {
		const p = { fraction: 0.5 } as RipProgress;
		const [running] = put(job('ripping', { rip: { state: 'running', progress: null } }));
		live.progress[running.id] = p;
		expect(live.progressFor(live.jobs[running.id])).toEqual(p);
		live.upsertJob({ ...running, rip: { ...running.rip, state: 'running' } } as Job);
		expect(live.progress[running.id]).toEqual(p);
		live.upsertJob({ ...running, state: 'review', rip: { ...running.rip, state: 'done' } } as Job);
		expect(live.progress[running.id]).toBeUndefined();
	});

	it('forgets deleted jobs', () => {
		const [a, b] = put(job('done'), job('done'));
		live.progress[a.id] = {} as RipProgress;
		live.removeJobs([a.id]);
		expect(Object.keys(live.jobs)).toEqual([String(b.id)]);
		expect(live.progress).toEqual({});
	});

	it('names a job by its drive, else the device', () => {
		const [j] = put(job('ripping'));
		expect(live.driveName(j)).toBe('/dev/sr0');
		live.upsertDrive({ id: 'fake-pioneer', display_name: 'Top bay', job_id: j.id } as Drive);
		expect(live.driveName(j)).toBe('Top bay');
		expect(live.jobFor(live.drives[0])?.id).toBe(j.id);
		live.upsertDrive({ id: 'fake-pioneer', display_name: 'Bottom bay', job_id: null } as Drive);
		expect(live.drives).toHaveLength(1);
		expect(live.jobFor(live.drives[0])).toBeNull();
	});
});
