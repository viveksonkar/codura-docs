// NFR-08: fail the build (and CI) if gzipped widget.js exceeds the budget.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gzipSync } from "node:zlib";

const BUDGET_KB = 40;
const file = resolve(process.cwd(), "dist/widget.js");
const gz = gzipSync(readFileSync(file), { level: 9 }).length / 1024;
const line = `widget.js gzipped: ${gz.toFixed(1)} KB (budget ${BUDGET_KB} KB)`;
if (gz > BUDGET_KB) {
  console.error(`✗ ${line}`);
  process.exit(1);
}
console.log(`✓ ${line}`);
