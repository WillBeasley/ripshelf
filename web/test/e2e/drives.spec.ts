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

test('a scan can be given longer while it runs', async ({ page }) => {
	await page.goto('/');
	const drive = page.locator('article.drive').filter({ hasText: '/dev/fake-sr3' });
	await drive.getByRole('button', { name: 'Retry scan' }).click();
	// The backend stops a scan after 5 s of silence, so give it longer straight away.
	await drive.getByRole('button', { name: '+5 min' }).click();
	await expect(drive.getByText('given 5 min more')).toBeVisible();
	await expect(drive.getByText(/Stops in .* if it stays quiet/)).toBeVisible();
	await page.waitForTimeout(6_000);
	await expect(drive.getByText('MakeMKV stopped responding while scanning')).toBeHidden();
	await drive.getByRole('button', { name: 'Cancel' }).click();
	await expect(drive.getByRole('button', { name: '+5 min' })).toBeHidden();
});

test('a film found with full confidence is ripped and filed without review', async ({ page }) => {
	await page.goto('/finished');
	await expect(page.getByRole('button', { name: /^The Brass Heron \(2024\) Done/ })).toBeVisible({ timeout: 30_000 });
});
