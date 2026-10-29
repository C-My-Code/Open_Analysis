// SPDX-License-Identifier: GPL-3.0-or-later
import { expect, test, type Page } from '@playwright/test';

const url = () => process.env.OA_E2E_URL!;

async function openApp(page: Page, errors: string[]) {
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(url());
  await expect(page.getByRole('status')).toContainText('Opened board', { timeout: 30_000 });
  await expect(page.getByTestId('board-view')).toBeVisible();
}

async function choose(page: Page, kind: string) {
  await expect(page.getByLabel('Analysis').locator('option')).toHaveCount(4, { timeout: 15_000 });
  await page.getByLabel('Analysis').selectOption(kind);
}

test('microstrip board: ERC job, impedance profile plots, accuracy and provenance', async ({ page }) => {
  const errors: string[] = [];
  await openApp(page, errors);

  // Setup form generated from the erc config schema, dry run, then run.
  await choose(page, 'erc');
  await expect(page.getByTestId('setup-form')).toBeVisible();
  await page.getByRole('button', { name: 'Dry run' }).click();
  await expect(page.getByTestId('dry-run')).toContainText('valid');
  await page.getByRole('button', { name: 'Run erc' }).click();
  await expect(page.getByTestId('job-state').first()).toHaveText('succeeded', { timeout: 60_000 });
  await expect(page.getByTestId('violation-count')).toContainText('0 violation(s)');
  await expect(page.getByTestId('accuracy-badge').first()).toContainText('accuracy:');
  await expect(page.getByTestId('provenance')).toContainText('erc v1');

  // Impedance profile of the three microstrip nets; config via the JSON editor (a NetSelector globbing MS_*).
  await choose(page, 'impedance.profile');
  await page.getByRole('button', { name: 'Edit as JSON' }).click();
  await page.getByLabel('Setup JSON').fill('{"nets": {"names": ["MS_*"]}}');
  await page.getByRole('button', { name: 'Apply JSON' }).click();
  await page.getByRole('button', { name: 'Run impedance.profile' }).click();
  await expect(page.getByTestId('profile-view')).toHaveCount(3, { timeout: 60_000 });
  // Plotly drew an SVG chart.
  await expect(page.getByTestId('profile-plot').first().locator('.main-svg').first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('accuracy-badge').first()).toBeVisible();
  await expect(page.getByTestId('provenance')).toContainText('impedance.profile');
  expect(errors).toEqual([]);
});
