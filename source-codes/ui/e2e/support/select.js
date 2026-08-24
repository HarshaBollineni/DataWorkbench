export async function selectPopoverOption(page, label, optionName) {
  await page.getByLabel(label, { exact: true }).click();
  const option = optionName
    ? page.getByRole("option", { name: optionName, exact: true })
    : page.getByRole("option").first();
  await option.click();
}
