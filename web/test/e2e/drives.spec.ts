import { expect, test } from '@playwright/test';

test('a scan that hangs is shown as a problem, and the disc can be ejected and put back', async ({ page }) => {
	await page.goto('/');
	const drive = page.locator('article.drive').filter({ hasText: '/dev/fake-sr3' });
	await expect(drive.getByText('MakeMKV stopped responding while scanning')).toBeVisible();
	await expect(drive.getByRole('button', { name: 'Retry scan' })).toBeVisible();

	await drive.getByRole('button', { name: 'Eject', exact: true }).click();
	const disc = drive.getByRole('button', { name: /PAPER_KITES/ });
	await expect(disc).toBeHidden();
	await drive.getByRole('button', { name: 'Insert disc (fake)' }).click();
	await expect(disc).toBeVisible();
	await expect(drive.getByText('MakeMKV stopped responding while scanning')).toBeVisible();
});

test('a film found with full confidence is ripped and filed without review', async ({ page }) => {
	await page.goto('/finished');
	await expect(page.getByRole('button', { name: /^The Brass Heron \(2024\) Done/ })).toBeVisible({ timeout: 30_000 });
});
