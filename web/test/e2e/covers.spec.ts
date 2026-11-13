import { expect, test, type Page } from '@playwright/test';

// A made-up case sheet's outside, drawn in the browser: a blue back, a yellow spine and a
// red front, with a white mark near the top left to tell a flip by.
async function sheet(page: Page): Promise<Buffer> {
	const url = await page.evaluate(() => {
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
		g.fillRect(30, 30, 80, 40);
		return c.toDataURL('image/png');
	});
	return Buffer.from(url.split(',')[1], 'base64');
}

test('a cover on the shelf is edited by hand and put back as it came', async ({ page }) => {
	await page.goto('/covers');
	await page.getByLabel('Both sides of each sheet').uncheck();
	await page
		.locator('label', { hasText: 'Upload pictures or PDFs' })
		.locator('input[type=file]')
		.setInputFiles({ name: 'sheet.png', mimeType: 'image/png', buffer: await sheet(page) });
	await page.locator('button.cover').first().click();
	const dialog = page.getByRole('dialog');

	await dialog.getByRole('button', { name: 'Edit…' }).click();
	await dialog.getByRole('button', { name: 'Flip ↔' }).click();
	await expect(dialog.getByText('Edited by hand.')).toBeVisible();
	await dialog.getByLabel('Straighten').fill('4');
	await expect(dialog.getByText('Straighten: +4.0°')).toBeVisible();
	await dialog.getByRole('button', { name: 'Save' }).click();
	await expect(dialog.getByRole('button', { name: 'Edit…' })).toBeVisible();

	let side = (await (await page.request.get('/api/covers')).json()).covers[0].outside;
	expect([side.flip, side.straighten, side.by_hand]).toEqual([true, -4, true]);

	await dialog.getByRole('button', { name: 'Edit…' }).click();
	await expect(dialog.getByText('Straighten: +4.0°')).toBeVisible();
	await dialog.getByRole('button', { name: 'As it came' }).click();
	await expect(dialog.getByText('Straighten: 0.0°')).toBeVisible();
	side = (await (await page.request.get('/api/covers')).json()).covers[0].outside;
	expect([side.flip, side.straighten, side.by_hand]).toEqual([false, 0, false]);
});
