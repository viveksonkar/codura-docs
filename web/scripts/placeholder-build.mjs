// Copies an app's public/ folder into dist/ until the real Vite build lands (M1-10, M2-04).
import { cpSync, mkdirSync, rmSync } from "node:fs";
import { resolve } from "node:path";

const appDir = process.cwd();
const dist = resolve(appDir, "dist");
rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });
cpSync(resolve(appDir, "public"), dist, { recursive: true });
console.log(`placeholder build: ${dist}`);
