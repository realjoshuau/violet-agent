from __future__ import annotations


async def screenshot(url: str) -> bytes:
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install dependencies and run `playwright install chromium`."
        ) from exc

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            return await page.screenshot(type="png", full_page=True)
        finally:
            await browser.close()
