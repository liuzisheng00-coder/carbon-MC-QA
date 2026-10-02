import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const sourceRoot = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.dirname(sourceRoot);

async function implementationFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map(async (entry) => {
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        return implementationFiles(entryPath);
      }
      if (
        entry.isFile() &&
        /\.(?:mjs|jsx)$/.test(entry.name) &&
        !/\.test\.(?:mjs|jsx)$/.test(entry.name)
      ) {
        return [entryPath];
      }
      return [];
    }),
  );
  return nested.flat();
}

test("Fragment variant is isolated from the legacy Web IFC renderer and geometry endpoint", async () => {
  const files = await implementationFiles(sourceRoot);
  const source = await Promise.all(files.map((file) => readFile(file, "utf8")));
  const implementation = source.join("\n");

  assert.ok(files.length > 0, "expected Fragment implementation source files");
  assert.doesNotMatch(
    implementation,
    /dm2c_frontend[\\/]src[\\/]web-ifc/,
    "the standalone viewer must not import the legacy Web IFC frontend",
  );
  assert.ok(
    !implementation.includes("/ifc-geometry"),
    "the standalone viewer must not call the backend progressive geometry endpoint",
  );
});

test("JSX entry imports the React runtime required by the configured Vite transform", async () => {
  const entry = await readFile(path.join(sourceRoot, "main.jsx"), "utf8");

  assert.match(
    entry,
    /import\s+React(?:\s*,\s*\{[^}]*\})?\s+from\s+["']react["']/,
    "the JSX entry must provide React when Vite uses the classic JSX transform",
  );
});

test("Vite enables the React JSX transform for every viewer component", async () => {
  const viteConfig = await readFile(path.join(frontendRoot, "vite.config.mjs"), "utf8");

  assert.match(viteConfig, /from\s+["']@vitejs\/plugin-react["']/);
  assert.match(viteConfig, /plugins\s*:\s*\[\s*react\(\)\s*\]/);
});
