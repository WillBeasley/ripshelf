import { expect, test } from '@playwright/test';

test('a changed setting is saved and kept', async ({ page }) => {
	await page.goto('/settings/identify');
	const searxng = page.locator('#setting-searxng_url').getByRole('textbox');
	await expect(searxng).toHaveValue('');

	await searxng.fill('http://searxng.local:8080');
	const bar = page.getByRole('region', { name: 'Save changes' });
	await bar.getByRole('button', { name: 'Save' }).click();
	await expect(bar.getByText('Saved')).toBeVisible();

	await page.reload();
	await expect(searxng).toHaveValue('http://searxng.local:8080');
});

test('discarding puts a setting back as saved', async ({ page }) => {
	await page.goto('/settings/identify');
	const searxng = page.locator('#setting-searxng_url').getByRole('textbox');
	const saved = await searxng.inputValue();
	await searxng.fill('http://elsewhere.local');
	await page.getByRole('region', { name: 'Save changes' }).getByRole('button', { name: 'Discard' }).click();
	await expect(searxng).toHaveValue(saved);
});

test('settings search opens the section a setting is on', async ({ page }) => {
	await page.goto('/settings/system');
	await page.getByPlaceholder('Search settings').fill('searxng');
	await page.getByRole('option', { name: /SearXNG/ }).first().click();
	await expect(page).toHaveURL(/\/settings\/identify/);
	await expect(page.locator('#setting-searxng_url')).toBeInViewport();
});
