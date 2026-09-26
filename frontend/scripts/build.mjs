import { mkdir, copyFile, cp } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
for (const name of ['app.js', 'api.js', 'data.js', 'icons.js', 'artwork.js']) {
  execFileSync(process.execPath, ['--check', resolve(root, 'src', name)], { stdio: 'inherit' });
}
await mkdir(resolve(root, 'dist'), { recursive: true });
await copyFile(resolve(root, 'index.html'), resolve(root, 'dist/index.html'));
await cp(resolve(root, 'src'), resolve(root, 'dist/src'), { recursive: true });
await cp(resolve(root, 'public'), resolve(root, 'dist/public'), { recursive: true });
console.log('Frontend built to frontend/dist. Run npm run preview to open it.');
