import { build, context } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

process.chdir(fileURLToPath(new URL('.', import.meta.url)));
const options = {
  absWorkingDir: fileURLToPath(new URL('.', import.meta.url)),
  entryPoints: ['src/main.jsx'], bundle: true, minify: true,
  outfile: '../src/wildfire_data/web/static/app.js',
  format: 'esm', target: ['es2022'], jsx: 'automatic',
  define: { 'process.env.NODE_ENV': '"production"' },
  loader: { '.png': 'dataurl' }, legalComments: 'linked',
};
const notices = await Promise.all(['react', 'react-dom', 'scheduler', 'leaflet'].map(async name =>
  `${name}\n${await readFile(`node_modules/${name}/LICENSE`, 'utf8')}`));
await writeFile('../src/wildfire_data/web/static/THIRD_PARTY_LICENSES.txt', notices.join('\n\n'));
if (process.argv.includes('--watch')) {
  const watcher = await context(options);
  await watcher.watch();
} else await build(options);
