"""Script to capture header and footer screenshots verifying the InvoiceDesk branding."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

ARTIFACT_DIR = Path(r"C:\Users\yashp\.gemini\antigravity\brain\9ebcf397-e5f5-48a1-bb84-6d7ce109d86f")
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE_URL = "http://localhost:8000"


async def capture_screenshots():
    print(f"Connecting to {BASE_URL}...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME_PATH,
            headless=True,
        )
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        await page.goto(BASE_URL, wait_until="networkidle")
        await page.wait_for_timeout(1000)

        # 1. Header Screenshot
        header = page.locator("header")
        header_path = ARTIFACT_DIR / "header_invoicedesk.png"
        await header.screenshot(path=str(header_path))
        print(f"Captured header screenshot -> {header_path}")

        # 2. Footer Screenshot
        footer = page.locator("footer")
        footer_path = ARTIFACT_DIR / "footer_branding.png"
        await footer.screenshot(path=str(footer_path))
        print(f"Captured footer screenshot -> {footer_path}")

        # 3. Rules & Assumptions Tab Screenshot showing InvoiceDesk branding
        await page.click('#tab-nav [data-tab="rules"]')
        await page.wait_for_timeout(500)
        rules_tab_path = ARTIFACT_DIR / "rules_tab_invoicedesk.png"
        await page.screenshot(path=str(rules_tab_path))
        print(f"Captured rules tab screenshot -> {rules_tab_path}")

        await browser.close()
        print("Branding screenshot capture complete.")


if __name__ == "__main__":
    asyncio.run(capture_screenshots())
