const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-setuid-sandbox'] });
  const page = await browser.newPage();
  try {
    // Assuming Vite is running on port 5173
    await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
    await page.screenshot({ path: 'latest_screenshot.png' });
    console.log('Screenshot saved to latest_screenshot.png');
  } catch (error) {
    console.error('Failed to take screenshot:', error);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
