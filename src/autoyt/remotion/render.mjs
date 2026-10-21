import {bundle} from '@remotion/bundler';
import {ensureBrowser, openBrowser, renderMedia, renderStill, selectComposition} from '@remotion/renderer';
import {createHash} from 'node:crypto';
import {readFile, mkdir, writeFile, stat} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:net';

const root = path.dirname(fileURLToPath(import.meta.url));
if (process.argv.includes('--setup')) {
  await ensureBrowser();
  console.log('Remotion browser installed. Local rendering is ready.');
} else {
  const job = JSON.parse(await readFile(process.argv[2], 'utf8'));
  const cache = path.resolve(job.bundleCache);
  await mkdir(cache, {recursive: true});
  const hash = createHash('sha256');
  for (const file of ['index.jsx', 'render.mjs', 'package-lock.json']) hash.update(await readFile(path.join(root, file)));
  const bundlePath = path.join(cache, hash.digest('hex').slice(0, 24));
  try {await stat(path.join(bundlePath, '.bundle-complete')); await stat(path.join(bundlePath, 'index.html'));}
  catch {
    await bundle({entryPoint: path.join(root, 'index.jsx'), outDir: bundlePath, publicDir: null});
    await writeFile(path.join(bundlePath, '.bundle-complete'), 'bundled');
  }
  console.log('Opening local render browser');
  const browser = await openBrowser('chrome', {logLevel: 'error'});
  try {
    // Avoid another local app answering on the renderer's default port (IPv4/IPv6).
    const port = await new Promise((resolve, reject) => {
      const server = createServer();
      server.once('error', reject);
      server.listen(0, '::', () => {const value = server.address().port; server.close(() => resolve(value));});
    });
    for (const scene of job.scenes) {
      let cached = false;
      try {cached = (await stat(scene.output)).size > 1000 && (await stat(scene.output+'.complete')).isFile();} catch {}
      if (!cached) {
        const inputProps = {...scene, width: job.width, height: job.height, fps: job.fps};
        console.log(`Rendering scene ${scene.index+1}/${scene.total}`);
        const composition = await selectComposition({serveUrl: bundlePath, id: 'TechScene', inputProps, puppeteerInstance: browser, port});
        await renderMedia({serveUrl: bundlePath, composition, inputProps, puppeteerInstance: browser, port,
          outputLocation: scene.output, codec: 'h264', pixelFormat: 'yuv420p', crf: job.crf,
          concurrency: job.concurrency, timeoutInMilliseconds: 120000, logLevel: 'error'});
        await writeFile(scene.output+'.complete', 'rendered');
        if (job.stills) await renderStill({serveUrl: bundlePath, composition, inputProps,
          puppeteerInstance: browser, port, frame: Math.min(Math.floor(scene.frames/2), Math.round(job.fps)),
          output: scene.output + '.png', logLevel: 'error'});
      }
    }
    await writeFile(job.receipt, JSON.stringify({completed: job.scenes.map(s => s.output)}));
  } finally {await browser.close({silent: true});}
}
