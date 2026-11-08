// Bundle the editor into one same-origin script for the dashboard, plus the licenses of
// every package that ends up in it. Run with `npm run build` from this folder.

import { build } from "esbuild";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const assets = join(here, "..", "src", "psydcar", "dashboard_assets");

const result = await build({
  entryPoints: [join(here, "src", "index.js")],
  outfile: join(assets, "editor.js"),
  bundle: true,
  minify: true,
  format: "iife",
  target: ["es2020"],
  legalComments: "none",
  banner: { js: "/* Psydcar editor: CodeMirror 6 and Lezer, MIT licensed. See editor-licenses.txt. */" },
  metafile: true,
});

const packages = new Map();
for (const input of Object.keys(result.metafile.inputs)) {
  const match = input.match(/node_modules\/((?:@[^/]+\/)?[^/]+)\//);
  if (!match || packages.has(match[1])) continue;
  const root = join(here, "node_modules", match[1]);
  const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
  const licenseFile = readdirSync(root).find((name) => /^licen[cs]e/i.test(name));
  packages.set(match[1], {
    version: manifest.version,
    license: manifest.license,
    text: licenseFile ? readFileSync(join(root, licenseFile), "utf8").trim() : `${manifest.license} license`,
  });
}

const sections = [...packages].sort(([a], [b]) => a.localeCompare(b)).map(([name, info]) =>
  `${name} ${info.version} (${info.license})\n\n${info.text}`);
writeFileSync(join(assets, "editor-licenses.txt"),
  `Third-party code bundled in editor.js\n\n${"=".repeat(60)}\n\n${sections.join(`\n\n${"=".repeat(60)}\n\n`)}\n`);

const size = result.metafile.outputs[relative(process.cwd(), join(assets, "editor.js"))]?.bytes;
console.log(`Bundled ${packages.size} packages into editor.js${size ? ` (${(size / 1024).toFixed(0)} KiB)` : ""}.`);
