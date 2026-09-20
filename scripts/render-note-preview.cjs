// Development helper: npm packages playwright and marked; installed Edge required.
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require('playwright');
(async () => {
  const { marked } = await import(pathToFileURL(require.resolve('marked')).href);
  const root = path.resolve(__dirname, '..');
  const assets = path.join(root, 'docs', 'assets');
  const content = marked.parse(fs.readFileSync(path.join(assets, 'note-preview.md'), 'utf8'));
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1000, height: 1000}, deviceScaleFactor: 2});
    await page.setContent(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>
      *{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#24292f;font-family:"Microsoft YaHei",system-ui,sans-serif;font-size:16px;line-height:1.8;padding:10px}
      article{background:#fff;border:1px solid #dce2e9;border-radius:10px;padding:36px 48px 40px}
      .label{font-size:13px;color:#687587;letter-spacing:.4px;margin-bottom:22px;border-bottom:1px solid #e5e9ef;padding-bottom:14px}
      h1{font-size:32px;line-height:1.4;margin:0 0 24px;padding-bottom:14px;border-bottom:1px solid #d8dee4}
      h2{font-size:23px;line-height:1.5;margin:26px 0 12px;padding-bottom:8px;border-bottom:1px solid #e8ecf0}
      p{margin:10px 0 16px}ul{padding-left:26px;margin:8px 0 18px}a{color:#0969da;text-decoration:none}hr{border:0;border-top:1px solid #d8dee4;margin:28px 0 18px}
      hr+p{font-size:13px;color:#687587}
    </style><article><div class="label">VideoNotes · Markdown 笔记预览</div>${content}</article></html>`);
    await page.evaluate(() => document.fonts.ready);
    const height = await page.evaluate(() => Math.ceil(document.body.getBoundingClientRect().height));
    await page.setViewportSize({width: 1000, height});
    await page.screenshot({path: path.join(assets, 'note-preview.png'), fullPage: true});
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
