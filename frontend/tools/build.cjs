// Vercel exposes server build variables here; only the public API origin is emitted.
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const output = path.join(root, 'dist');
const value = (process.env.BACKEND_API_URL || '').trim();
if (!value) throw new Error('Set BACKEND_API_URL to the deployed backend HTTPS origin before building.');
const origin = new URL(value);
if (origin.protocol !== 'https:' || origin.username || origin.password ||
    origin.pathname !== '/' || origin.search || origin.hash) {
  throw new Error('BACKEND_API_URL must be an HTTPS origin, without credentials, path, query or fragment.');
}

fs.mkdirSync(output, { recursive: true });
const names = fs.readdirSync(root, { withFileTypes: true });
for (const entry of names) {
  if (entry.isFile() && /\.(html|css|js|svg|png|jpg|webp|ico)$/.test(entry.name)) {
    fs.copyFileSync(path.join(root, entry.name), path.join(output, entry.name));
  }
}
const assets = path.join(root, 'assets');
if (fs.existsSync(assets)) fs.cpSync(assets, path.join(output, 'assets'), { recursive: true });
const config = fs.readFileSync(path.join(root, 'config.js'), 'utf8');
const pattern = /\? "http:\/\/127\.0\.0\.1:8000" : ""/;
if (!pattern.test(config)) throw new Error('The API configuration format changed; update build.cjs.');
fs.writeFileSync(path.join(output, 'config.js'), config.replace(pattern, `? "http://127.0.0.1:8000" : ${JSON.stringify(origin.origin)}`));
console.log('Frontend built with configured backend origin.');
