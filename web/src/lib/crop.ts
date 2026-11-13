// A crop box drawn on a picture as shown, as fractions of it, and how it moves when the
// picture is turned or mirrored.
export type Box = { l: number; t: number; r: number; b: number };

export const WHOLE: Box = { l: 0, t: 0, r: 1, b: 1 };

// A crop kept in the original's pixels (before the turn), as fractions of the picture as
// shown: turned clockwise by `rotate`, then mirrored left to right with `flip`.
export function shownBox(crop: number[] | null | undefined, width: number, height: number, rotate: number, flip = false): Box {
	if (!crop) return { ...WHOLE };
	const [L, T, R, B] = [crop[0] / width, crop[1] / height, crop[2] / width, crop[3] / height];
	let box: Box;
	switch (rotate) {
		case 90:
			box = { l: 1 - B, t: L, r: 1 - T, b: R };
			break;
		case 180:
			box = { l: 1 - R, t: 1 - B, r: 1 - L, b: 1 - T };
			break;
		case 270:
			box = { l: T, t: 1 - R, r: B, b: 1 - L };
			break;
		default:
			box = { l: L, t: T, r: R, b: B };
	}
	return flip ? mirror(box) : box;
}

// The same part of the picture once it's turned a quarter clockwise.
export const turnRight = (x: Box): Box => ({ l: 1 - x.b, t: x.l, r: 1 - x.t, b: x.r });
// ...anticlockwise.
export const turnLeft = (x: Box): Box => ({ l: x.t, t: 1 - x.r, r: x.b, b: 1 - x.l });
// ...mirrored left to right.
export const mirror = (x: Box): Box => ({ l: 1 - x.r, t: x.t, r: 1 - x.l, b: x.b });
// ...mirrored top to bottom.
export const mirrorDown = (x: Box): Box => ({ l: x.l, t: 1 - x.b, r: x.r, b: 1 - x.t });
