import { describe, expect, it } from 'vitest';
import {
	applyView,
	flipDownView,
	flipView,
	mirror,
	mirrorDown,
	NO_CHANGE,
	shownBox,
	turnLeft,
	turnRight,
	turnView,
	type Box,
	type View
} from '../../src/lib/crop';

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

	it('saves turns and mirrors made in the editor as the side shows them', () => {
		// Each edit as shown, with what it does to the box and to the view.
		const edits: [(x: Box) => Box, (v: View) => View][] = [
			[turnRight, (v) => turnView(v, 90)],
			[turnLeft, (v) => turnView(v, -90)],
			[mirror, flipView],
			[mirrorDown, flipDownView]
		];
		const crop = [20, 40, 100, 120];
		for (const rotate of [0, 90, 180, 270])
			for (const flip of [false, true])
				for (const seq of [[0], [1, 2], [2, 0], [3], [2, 0, 0, 3], [0, 2, 1]]) {
					let shown = shownBox(crop, 200, 160, rotate, flip);
					let view = NO_CHANGE;
					for (const i of seq) {
						shown = edits[i][0](shown);
						view = edits[i][1](view);
					}
					const saved = applyView(rotate, flip, view);
					// The crop, kept on the original, shows where the edits put it.
					close(shownBox(crop, 200, 160, saved.rotate, saved.flip), shown);
				}
		expect(applyView(90, false, NO_CHANGE)).toEqual({ rotate: 90, flip: false });
	});
});
