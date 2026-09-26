import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { dirname, resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const project = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const root = process.argv.includes('--dist') ? resolve(project, 'dist') : project;
const portIndex = process.argv.indexOf('--port');
const port = Number(portIndex >= 0 ? process.argv[portIndex + 1] : process.env.PORT || 5173);
const mime = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon' };

const server = createServer(async (req, res) => {
  try {
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    const relative = pathname.replace(/^\/+/, '') || 'index.html';
    const file = resolve(root, relative);
    const allowed = relative === 'index.html' || relative.startsWith('src/') || relative.startsWith('public/');
    if (!allowed || (file !== root && !file.startsWith(root + sep))) {
      res.writeHead(403); res.end('Forbidden'); return;
    }
    if (!(await stat(file)).isFile()) { res.writeHead(404); res.end('Not found'); return; }
    const body = await readFile(file);
    res.writeHead(200, { 'Content-Type': mime[extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff' });
    res.end(body);
  } catch (error) {
    res.writeHead(error.code === 'ENOENT' ? 404 : 400);
    res.end(error.code === 'ENOENT' ? 'Not found' : 'Invalid request');
  }
});
server.on('error', error => { console.error(error.message); process.exitCode = 1; });
server.listen(port, '127.0.0.1', () => console.log(`NeonFind is ready at http://localhost:${port}`));
