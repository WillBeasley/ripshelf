import { expect, test } from '@playwright/test';

test('a fresh install starts at setup, and skipping it shows the drives', async ({ page }) => {
	await page.goto('/');
	await expect(page).toHaveURL(/\/setup$/);
	await expect(page.getByRole('heading', { name: 'Set up ripshelf' })).toBeVisible();
	await expect(page.getByText('/dev/fake-sr2')).toBeVisible();

	await page.getByRole('button', { name: 'Skip setup' }).click();
	await expect(page).toHaveURL(/\/$/);
	await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();
	await expect(page.locator('article.drive')).toHaveCount(4);

	// Setup stays done.
	await page.goto('/');
	await expect(page).toHaveURL(/\/$/);
});
