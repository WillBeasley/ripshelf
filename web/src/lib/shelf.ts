// Small helpers for showing covers on the shelf.
import type { ShelfCover } from './api-types';
import { kindLabel } from './format';

// What the cover is, as far as its reading says: its title, else what its barcode names.
export function coverTitle(c: ShelfCover): string {
	const r = c.reading;
	if (r.title) return r.year ? `${r.title} (${r.year})` : r.title;
	if (r.barcode_name) return r.barcode_name;
	if (r.state === 'waiting' || r.state === 'reading') return 'Reading…';
	return 'Not read';
}

// The smaller facts under the title: format and barcode.
export function coverFacts(c: ShelfCover): string {
	const r = c.reading;
	return [r.format ? kindLabel(r.format) : null, r.barcode, r.catalogue_number]
		.filter(Boolean)
		.join(' · ');
}

export const READ_STATE: Record<ShelfCover['reading']['state'], string> = {
	waiting: 'Waiting to be read',
	reading: 'Reading…',
	done: 'Read',
	failed: 'Reading failed',
	unavailable: "Can't be read here"
};

export const VISION_STATE: Record<NonNullable<ShelfCover['reading']['vision_state']>, string> = {
	queued: 'Waiting for the vision model',
	reading: 'The vision model is reading it',
	done: 'Read by the vision model',
	failed: "The vision model couldn't read it"
};
