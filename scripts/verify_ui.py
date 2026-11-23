"""Interactive verification script for Zamp Invoice Decision Engine Frontend."""
import asyncio
import os
import sys
from pathlib import Path
from playwright.async_api import async_playwright

ARTIFACT_DIR = Path(r"C:\Users\yashp\.gemini\antigravity\brain\9ebcf397-e5f5-48a1-bb84-6d7ce109d86f")
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE_URL = "http://localhost:8000"


async def run_ui_verification():
    print(f"Starting UI verification against {BASE_URL}...")
    console_errors = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME_PATH,
            headless=True,
        )
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: console_errors.append(str(exc)))

        # 1. Load Homepage
        print("Navigating to homepage...")
        response = await page.goto(BASE_URL, wait_until="networkidle")
        assert response.status == 200, f"Expected 200, got {response.status}"
        print("Homepage loaded successfully.")

        # 2. Reset Demo Data
        print("Triggering Reset Demo Data...")
        page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
        await page.click("#btn-reset-demo")
        await page.wait_for_timeout(1000)
        print("Demo data reset confirmed.")

        # 3. Verify Sample Chips
        chips = page.locator(".sample-chip")
        count = await chips.count()
        print(f"Found {count} demo sample chips.")
        assert count == 7, f"Expected 7 sample chips, found {count}"

        # 4. Run S0 & Capture Mid-Run Screenshot
        print("Launching Scenario S0...")
        await page.locator('[data-sample-id="S0"]').click()
        # Wait a short moment while streaming stages
        await page.wait_for_timeout(1500)

        mid_run_path = ARTIFACT_DIR / "mid_run_view.png"
        await page.screenshot(path=str(mid_run_path))
        print(f"Captured mid-run screenshot -> {mid_run_path}")

        # Wait for S0 completion (decision card visible)
        await page.wait_for_selector("#decision-card-container:not(.hidden)", state="visible", timeout=60000)
        print("Scenario S0 completed.")

        # 5. Run remaining scenarios in demo order: S1, E1a, E1b, E2, E4, E3
        scenarios = ["S1", "E1a", "E1b", "E2", "E4", "E3"]
        for s_id in scenarios:
            print(f"Launching Scenario {s_id}...")
            await page.locator(f'[data-sample-id="{s_id}"]').click()
            await page.wait_for_timeout(1000)
            # Wait for decision card to become visible
            await page.wait_for_selector("#decision-card-container:not(.hidden)", state="visible", timeout=60000)
            await page.wait_for_timeout(500)
            print(f"Scenario {s_id} completed.")

            if s_id == "E4":
                # Verify E4 CRITICAL tag and Call Script
                print("Inspecting E4 Decision Card...")
                await page.wait_for_selector('#decision-card-container span:has-text("CRITICAL")', state="visible", timeout=10000)
                await page.wait_for_selector('#decision-card-container :has-text("Internal Call Script")', state="visible", timeout=10000)
                e4_path = ARTIFACT_DIR / "e4_decision_card.png"
                decision_card = page.locator("#decision-card-container")
                await decision_card.screenshot(path=str(e4_path))
                print(f"Captured E4 decision card screenshot -> {e4_path}")

        # 6. Dashboard Tab
        print("Switching to Dashboard tab...")
        await page.click('#tab-nav [data-tab="dashboard"]')
        await page.wait_for_timeout(1000)
        await page.wait_for_selector("#kpi-strip", timeout=10000)

        dash_path = ARTIFACT_DIR / "dashboard_view.png"
        await page.screenshot(path=str(dash_path))
        print(f"Captured Dashboard screenshot -> {dash_path}")

        # 7. Check Review Queue Tab
        print("Checking Review Queue tab...")
        await page.click('#tab-nav [data-tab="review"]')
        await page.wait_for_timeout(500)
        held_items = page.locator("#review-queue-container > div")
        held_count = await held_items.count()
        print(f"Review Queue items: {held_count}")
        assert held_count > 0, "Review queue should have held items"

        # 8. Check Rules & Assumptions Tab
        print("Checking Rules & Assumptions tab...")
        await page.click('#tab-nav [data-tab="rules"]')
        await page.wait_for_timeout(500)
        rules_rows = page.locator("#config-rules-table tr")
        print(f"Rules table rows: {await rules_rows.count()}")

        # 9. Check Master Data Tab
        print("Checking Master Data tab...")
        await page.click('#tab-nav [data-tab="data"]')
        await page.wait_for_timeout(500)
        vendors = page.locator("#master-vendors-container tbody tr")
        pos = page.locator("#master-pos-container > div > div")
        print(f"Master vendors: {await vendors.count()}, POs: {await pos.count()}")

        # 10. Check Console Errors
        print(f"Console errors collected: {len(console_errors)}")
        if console_errors:
            print("Console Errors found:")
            for err in console_errors:
                print(f"  - {err}")
        assert len(console_errors) == 0, f"Found unexpected console errors: {console_errors}"

        await browser.close()
        print("UI Verification successfully finished!")


if __name__ == "__main__":
    asyncio.run(run_ui_verification())
