import { describe, expect, it } from 'vitest';
import { mirror, mirrorDown, shownBox, turnLeft, turnRight, type Box } from '../../src/lib/crop';

const box: Box = { l: 0.1, t: 0.2, r: 0.5, b: 0.6 };
const close = (a: Box, b: Box) => Object.keys(a).forEach((k) => expect(a[k as keyof Box]).toBeCloseTo(b[k as keyof Box]));

describe('crop boxes', () => {
	it('shows a crop kept in the original pixels, turned and mirrored', () => {
		// [20, 40, 100, 120] on a 200 x 200 picture.
		const crop = [20, 40, 100, 120];
		close(shownBox(crop, 200, 200, 0), box);
		close(shownBox(crop, 200, 200, 90), turnRight(box));
		close(shownBox(crop, 200, 200, 180), turnRight(turnRight(box)));
		close(shownBox(crop, 200, 200, 270), turnLeft(box));
		close(shownBox(crop, 200, 200, 90, true), mirror(turnRight(box)));
		expect(shownBox(null, 200, 200, 90)).toEqual({ l: 0, t: 0, r: 1, b: 1 });
	});

	it('turns and mirrors back to where it was', () => {
		close(turnLeft(turnRight(box)), box);
		close(turnRight(turnRight(turnRight(turnRight(box)))), box);
		close(mirror(mirror(box)), box);
		// Mirrored top to bottom is mirrored left to right and turned upside down.
		close(mirrorDown(box), turnRight(turnRight(mirror(box))));
	});
});
