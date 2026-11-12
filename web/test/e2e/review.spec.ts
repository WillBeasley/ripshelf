import { expect, test } from '@playwright/test';

test('a film with no candidates is searched for by hand and confirmed', async ({ page }) => {
	await page.goto('/review');
	const card = page.locator('article.review').filter({ hasText: 'MOONLIT_ORCHARD' });
	await expect(card.getByText('No candidates.')).toBeVisible();

	await card.getByRole('button', { name: 'Search TMDB' }).click();
	await card.getByLabel('Title').fill('The Hollow');
	await card.getByLabel('Year').fill('1982');
	await card.getByRole('button', { name: 'Search TMDB' }).click();
	await expect(card.getByRole('heading', { name: 'The Hollow (1982)' })).toBeVisible();

	await card.getByRole('button', { name: 'Confirm The Hollow (1982)' }).click();
	await expect(card).toBeHidden();
	await page.getByRole('link', { name: 'Finished' }).click();
	await expect(page.getByRole('button', { name: /^The Hollow \(1982\) Done/ })).toBeVisible();
});

test('a TV disc shows its show, and episodes are picked title by title', async ({ page }) => {
	await page.goto('/review');
	const card = page.locator('article.review').filter({ hasText: 'PIPPA_&_GOOSE_KITE_TRAM' });
	await expect(card.getByText('Pippa & Goose (2013)')).toBeVisible();
	await expect(card.getByText('Order verified against the play-all')).toBeVisible();
	await expect(card.getByText('9 titles still need an episode')).toBeVisible();
	await expect(card.getByRole('button', { name: 'Confirm' })).toBeDisabled();

	await card.getByRole('button', { name: 'Change the episode of title 2' }).click();
	const picker = page.getByRole('dialog');
	await picker.getByLabel('Search episodes by name or code').fill('s1e14');
	await picker.getByRole('checkbox', { name: /S01E14\s*Doubles/ }).check();
	await picker.getByRole('button', { name: 'Use S01E14' }).click();
	await expect(picker).toBeHidden();
	await expect(card.getByText('8 titles still need an episode')).toBeVisible();
	await expect(card.getByText('Doubles')).toBeVisible();
});
