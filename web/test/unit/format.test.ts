import { describe, expect, it } from 'vitest';
import type { Candidate, Job } from '#lib/api-types.ts';
import { duration, eta, gb, gbPerMin, guessTitle, jobTitle, kindLabel, percent, size, via } from '#lib/format.ts';

// Just the parts of a job these read.
function job(over: Partial<Job> = {}): Job {
	return { id: 7, identity: null, candidates: [], tv: null, disc: { name: null, label: null }, ...over } as unknown as Job;
}

const film = (title: string, year: number | null, excluded = false) => ({ title, year, excluded }) as Candidate;

describe('sizes', () => {
	it('labels binary units GB, as MakeMKV does', () => {
		expect(gb(58.2 * 1024 ** 3)).toBe('58.2 GB');
		expect(gb(3.6 * 1024 ** 3, 2)).toBe('3.60 GB');
		expect(gb(null)).toBe('–');
	});

	it('picks MB, GB or TB', () => {
		expect(size(700 * 1024 ** 2)).toBe('700 MB');
		expect(size(4 * 1024 ** 3)).toBe('4.0 GB');
		expect(size(1.5 * 1024 ** 4)).toBe('1.50 TB');
		expect(size(undefined)).toBe('–');
	});

	it('gives a read speed per minute', () => {
		expect(gbPerMin(1024 ** 3 / 60)).toBe('1.00 GB/min');
		expect(gbPerMin(0)).toBe('–');
	});
});

describe('times', () => {
	it('shows hours only when there are some', () => {
		expect(duration(6101)).toBe('1:41:41');
		expect(duration(425)).toBe('7:05');
		expect(duration(0)).toBe('0:00');
		expect(duration(null)).toBe('–');
	});

	it('rounds a time left to minutes', () => {
		expect(eta(30)).toBe('< 1 min');
		expect(eta(14 * 60 + 20)).toBe('14 min');
		expect(eta(95 * 60)).toBe('1 h 35 min');
		expect(eta(Infinity)).toBe('–');
	});

	it('never rounds a percentage up to 100', () => {
		expect(percent(0.999)).toBe('99%');
		expect(percent(null)).toBe('0%');
	});
});

describe('titles', () => {
	it('names a job by its identity, then the disc', () => {
		expect(jobTitle(job({ identity: film('Moonlit Orchard', 2004) }))).toBe('Moonlit Orchard (2004)');
		expect(jobTitle(job({ identity: film('Moonlit Orchard', null) }))).toBe('Moonlit Orchard');
		expect(jobTitle(job({ disc: { name: 'The Brass Heron', label: 'THE_BRASS_HERON' } as Job['disc'] }))).toBe(
			'The Brass Heron'
		);
		expect(jobTitle(job({ disc: { name: null, label: 'THE_BRASS_HERON' } as Job['disc'] }))).toBe('THE_BRASS_HERON');
		expect(jobTitle(job())).toBe('Job 7');
	});

	it('guesses the show, then the best film not ruled out', () => {
		const tv = { show: { name: 'Pippa & Goose', year: 2011 } } as Job['tv'];
		expect(guessTitle(job({ tv }))).toBe('Pippa & Goose (2011)');
		const candidates = [film('Captain Comet', 1999, true), film('Seagull Manor', 2015)];
		expect(guessTitle(job({ candidates }))).toBe('Seagull Manor (2015)');
		expect(guessTitle(job({ identity: film('Moonlit Orchard', 2004), candidates }))).toBe('Moonlit Orchard (2004)');
		expect(guessTitle(job({ candidates: [film('Captain Comet', 1999, true)] }))).toBe('Job 7');
	});

	it('names disc kinds and providers', () => {
		expect(kindLabel('uhd')).toBe('UHD Blu-ray');
		expect(kindLabel(null)).toBe('Disc');
		expect(via(['tmdb', 'omdb', 'someday'])).toBe('via TMDB · OMDb · someday');
		expect(via([])).toBe('');
	});
});
