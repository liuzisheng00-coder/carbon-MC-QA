const fs = require('fs');
const path = require('path');
const {chromium} = require('playwright');

async function main() {
  const root = __dirname;
  const svgFiles = fs.readdirSync(root).filter(file => /^fig_.*\.svg$/.test(file)).sort();
  const browserCandidates = [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    chromium.executablePath()
  ];
  const executablePath = browserCandidates.find(candidate => fs.existsSync(candidate));
  if (!executablePath) throw new Error('No Chromium-compatible browser was found for PDF export.');
  const browser = await chromium.launch({headless: true, executablePath});
  const page = await browser.newPage();
  for (const file of svgFiles) {
    const source = fs.readFileSync(path.join(root, file), 'utf8');
    const width = (source.match(/<svg[^>]*\bwidth="(\d+)"/) || [])[1];
    const height = (source.match(/<svg[^>]*\bheight="(\d+)"/) || [])[1];
    if (!width || !height) throw new Error(`Cannot read SVG dimensions: ${file}`);
    await page.setViewportSize({width: +width, height: +height});
    await page.setContent(`<style>@page { size: ${width}px ${height}px; margin: 0; } html, body { margin: 0; width: ${width}px; height: ${height}px; } svg { display: block; }</style>${source}`);
    await page.pdf({path: path.join(root, file.replace(/\.svg$/, '.pdf')), width: `${width}px`, height: `${height}px`, margin: {top: 0, right: 0, bottom: 0, left: 0}, printBackground: true});
    console.log(`Saved ${file.replace(/\.svg$/, '.pdf')}`);
  }
  await browser.close();
}
main().catch(error => { console.error(error); process.exit(1); });
