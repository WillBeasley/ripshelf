import { expect, test, type Page } from '@playwright/test';

// A made-up case sheet's outside, drawn in the browser: a blue back, a yellow spine and a
// red front, with a white mark near the top left to tell a flip by (`n` moves it, for
// another sheet: the shelf skips one it has already).
async function sheet(page: Page, n = 0): Promise<Buffer> {
	const url = await page.evaluate((n) => {
		const c = document.createElement('canvas');
		c.width = 600;
		c.height = 300;
		const g = c.getContext('2d')!;
		g.fillStyle = '#2830c8';
		g.fillRect(0, 0, 600, 300);
		g.fillStyle = '#f0dc00';
		g.fillRect(285, 0, 30, 300);
		g.fillStyle = '#c80a00';
		g.fillRect(315, 0, 285, 300);
		g.fillStyle = '#ffffff';
		g.fillRect(30 + n * 5, 30, 80, 40);
		return c.toDataURL('image/png');
	}, n);
	return Buffer.from(url.split(',')[1], 'base64');
}

async function upload(page: Page, n = 0) {
	await page.goto('/covers');
	await page.getByLabel('Both sides of each sheet').uncheck();
	const before = (await (await page.request.get('/api/covers')).json()).covers.length;
	await page
		.locator('label', { hasText: 'Upload pictures or PDFs' })
		.locator('input[type=file]')
		.setInputFiles({ name: `sheet${n}.png`, mimeType: 'image/png', buffer: await sheet(page, n) });
	await expect(page.locator('button.cover')).toHaveCount(before + 1);
}

const newest = async (page: Page) => (await (await page.request.get('/api/covers')).json()).covers[0];

test('a cover on the shelf is edited by hand and put back as it came', async ({ page }) => {
	await upload(page);
	await page.locator('button.cover').first().click();
	const dialog = page.getByRole('dialog');

	await dialog.getByRole('button', { name: 'Edit…' }).click();
	await dialog.getByRole('button', { name: 'Flip ↔' }).click();
	await dialog.getByLabel('Straighten').fill('4');
	await expect(dialog.getByText('Straighten: +4.0°')).toBeVisible();
	await dialog.getByRole('button', { name: 'Save' }).click();
	await expect(dialog.getByRole('button', { name: 'Edit…' })).toBeVisible();

	let side = (await newest(page)).outside;
	expect([side.flip, side.straighten, side.by_hand]).toEqual([true, -4, true]);

	await dialog.getByRole('button', { name: 'Edit…' }).click();
	await expect(dialog.getByText('Straighten: +4.0°')).toBeVisible();
	await dialog.getByRole('button', { name: 'As it came' }).click();
	await expect(dialog.getByText('Straighten: 0.0°')).toBeVisible();
	side = (await newest(page)).outside;
	expect([side.flip, side.straighten, side.by_hand]).toEqual([false, 0, false]);
});

test('cancelling the editor keeps nothing of what was done in it', async ({ page }) => {
	await upload(page, 1);
	const before = (await newest(page)).outside;
	await page.locator('button.cover').first().click();
	const dialog = page.getByRole('dialog');
	await dialog.getByRole('button', { name: 'Edit…' }).click();
	await dialog.getByRole('button', { name: 'Turn right' }).click();
	await dialog.getByRole('button', { name: 'Flip ↕' }).click();
	await dialog.getByLabel('Straighten').fill('3');
	await dialog.getByRole('button', { name: 'Cancel' }).click();
	await expect(dialog.getByRole('button', { name: 'Edit…' })).toBeVisible();
	const after = (await newest(page)).outside;
	expect([after.rotate, after.flip, after.straighten, after.by_hand, after.crop]).toEqual([
		before.rotate, before.flip, before.straighten, before.by_hand, before.crop
	]);

	// Turned in the editor and saved, it's kept.
	await dialog.getByRole('button', { name: 'Edit…' }).click();
	await dialog.getByRole('button', { name: 'Turn right' }).click();
	await dialog.getByRole('button', { name: 'Save' }).click();
	await expect(dialog.getByRole('button', { name: 'Edit…' })).toBeVisible();
	expect((await newest(page)).outside.rotate).toBe((before.rotate + 90) % 360);
});

test('the open cover turns from its own buttons', async ({ page }) => {
	await upload(page, 2);
	const before = (await newest(page)).outside.rotate;
	await page.locator('button.cover').first().click();
	const dialog = page.getByRole('dialog');
	const turned = async (by: number) =>
		expect.poll(async () => (await newest(page)).outside.rotate).toBe((((before + by) % 360) + 360) % 360);
	await dialog.getByRole('button', { name: 'Turn right' }).click();
	await turned(90);
	await dialog.getByRole('button', { name: 'Turn upside down' }).click();
	await turned(270);
	await dialog.getByRole('button', { name: 'Turn left' }).click();
	await turned(180);
});

test('covers picked by holding one are deleted together', async ({ page }) => {
	await upload(page, 3);
	await upload(page, 4);
	await upload(page, 5);
	const covers = page.locator('button.cover');
	const count = await covers.count();
	const keep = (await (await page.request.get('/api/covers')).json()).covers[2].id;

	// Press and hold the first, then tap the second.
	const box = (await covers.nth(0).boundingBox())!;
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	await page.waitForTimeout(700);
	await page.mouse.up();
	await expect(page.getByRole('dialog')).toBeHidden();
	await expect(page.getByText('1 selected')).toBeVisible();
	await covers.nth(1).click();
	await expect(page.getByText('2 selected')).toBeVisible();
	await covers.nth(1).click();
	await covers.nth(1).click({ modifiers: ['Control'] });
	await expect(page.getByText('2 selected')).toBeVisible();

	page.once('dialog', (d) => d.accept());
	await page.getByRole('button', { name: 'Delete selected' }).click();
	await expect(covers).toHaveCount(count - 2);
	await expect(page.getByText('selected')).toBeHidden();
	expect((await newest(page)).id).toBe(keep);
});
