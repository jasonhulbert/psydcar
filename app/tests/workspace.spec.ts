import { expect, test, type Page } from '@playwright/test';

const mac = process.platform === 'darwin';
const documentStart = mac ? 'Meta+ArrowUp' : 'Control+Home';
const documentEnd = mac ? 'Meta+ArrowDown' : 'Control+End';
const selectLine = mac ? 'Shift+Meta+ArrowRight' : 'Shift+End';

async function openFile(page: Page, name: string): Promise<void> {
  await page.getByRole('button', { name, exact: true }).click();
  await expect(page.locator('.cm-content')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: /Product knowledge/ }).click();
});

test('navigate, select, edit, undo, find, and save Markdown in both editing modes', async ({ page }) => {
  await openFile(page, 'notes.md');
  const editor = page.locator('.cm-content');
  const modes = page.getByRole('group', { name: 'Markdown view' });
  for (const mode of ['Edit', 'Source']) {
    await modes.getByRole('button', { name: mode, exact: true }).click();
    await editor.focus();
    await page.keyboard.press(documentStart);
    for (let line = 1; line < 7; line++) await page.keyboard.press('ArrowDown');
    await page.keyboard.press(selectLine);
    await expect.poll(() => page.evaluate(() => window.getSelection()?.toString())).toBe('First paragraph.');
    await page.keyboard.type('Revised paragraph.');
    await page.keyboard.press('ControlOrMeta+z');
    await page.keyboard.press('ControlOrMeta+Shift+z');
    await page.keyboard.press('ControlOrMeta+s');
    await expect(page.getByRole('status')).toContainText('Saved to disk.');
    await page.keyboard.press('ControlOrMeta+f');
    await expect(page.locator('.cm-search')).toBeVisible();
    await page.keyboard.press('Escape');
    await editor.focus();
    await page.keyboard.press('Tab');
    await expect(editor).not.toBeFocused();
    // Restore the same text before exercising the other mode.
    await editor.focus();
    await page.keyboard.press('ControlOrMeta+z');
    await page.keyboard.press('ControlOrMeta+s');
    await expect(page.getByRole('status')).toContainText('Saved to disk.');
  }
});

test('switch rendered and source views with the keyboard while preserving a draft', async ({ page }) => {
  await openFile(page, 'notes.md');
  const editor = page.locator('.cm-content');
  await editor.focus();
  await page.keyboard.press(documentEnd);
  await page.keyboard.type('\nDraft note.');
  const modes = page.getByRole('group', { name: 'Markdown view' });
  await modes.getByRole('button', { name: 'Edit', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('.cm-md-table-grid')).toBeVisible();
  await page.keyboard.press('ArrowRight');
  await expect(editor).toContainText('Draft note.');
  await expect(page.getByText('Unsaved changes', { exact: true })).toBeVisible();
});

test('review and approve an agent proposal with focus returning to its trigger', async ({ page }) => {
  const trigger = page.getByRole('button', { name: /Review proposals/ });
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: 'Review proposals' });
  await expect(dialog.getByRole('region', { name: 'Line-by-line changes' })).toContainText('+Updated decision.');
  await dialog.getByRole('button', { name: 'Approve and save' }).click();
  await expect(dialog.getByRole('status')).toHaveText('Edit approved and saved to disk.');
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(trigger).toBeFocused();
});

test('edit a large document and keep its draft while browsing on a narrow screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: 'dark' });
  await openFile(page, 'large.md');
  const editor = page.locator('.cm-content');
  await editor.focus();
  await page.keyboard.press(documentStart);
  for (let line = 0; line < 6; line++) await page.keyboard.press('ArrowDown');
  await expect(page.getByText(/^Ln 3, Col \d+$/)).toBeVisible();
  await page.keyboard.press(documentEnd);
  await page.keyboard.type('A new discovery.');
  await page.getByRole('button', { name: 'Show files', exact: true }).click();
  await page.getByRole('button', { name: 'large.md', exact: true }).click();
  await expect(editor).toContainText('A new discovery.');
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});
