import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("full Fragment variant has an isolated port and retains the connected DM2C application", async () => {
  const pkg = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));
  const entry = await readFile(path.join(root, "src", "main.jsx"), "utf8");

  assert.match(pkg.scripts.dev, /--port 5173/);
  assert.doesNotMatch(pkg.scripts.dev, /--port 5175/);
  assert.match(entry, /DM2CApp\.connected\.jsx/);
});

test("full Fragment variant keeps the X-Ray toolbar below the floating header", async () => {
  const viewer = await readFile(
    path.join(root, "src", "FragmentModelViewer.jsx"),
    "utf8",
  );

  assert.match(viewer, /toolbar:\s*\{[\s\S]*top:\s*76/);
  assert.match(viewer, /inspector:\s*\{[\s\S]*right:\s*18,[\s\S]*bottom:\s*18/);
});

test("full variant connects only the Model workspace to the Fragment renderer", async () => {
  const source = await readFile(
    path.join(root, "src", "DM2CVisualInterface.jsx"),
    "utf8",
  );

  assert.match(
    source,
    /import FragmentModelViewer from "\.\/FragmentModelViewer\.jsx"/,
  );
  assert.match(
    source,
    /<FragmentModelViewer[\s\S]*onSelectResult=\{handleModelSelection\}[\s\S]*ifcFileUrl=\{ifcFileUrl\}/,
  );
  assert.doesNotMatch(source, /ThreeModelViewer|ifcGeometryUrl|ifc-geometry/);

  // Non-Model functionality remains owned by the copied connected interface:
  // upload/start-project, QA submission, the three account perspectives, and Graph.
  assert.match(source, /<UploadScreen[\s\S]*onStartProject=\{onStartProject\}/);
  assert.match(source, /function PerspectivePanel\(/);
  assert.match(source, /product:[\s\S]*material:[\s\S]*process:/);
  assert.match(source, /onSubmit=\{\(\) => onSend\(\)\}/);
  assert.match(source, /<WorkspaceModeSwitch/);
  assert.match(source, /<GraphAssociationPanel/);
  assert.match(source, /aria-label="DM2C grounded question answering"/);
});

test("full Fragment variant documents one-address normal use", async () => {
  const readme = await readFile(path.join(root, "README.md"), "utf8");

  assert.match(readme, /dm2c_frontend_fragments_full/);
  assert.match(readme, /127\.0\.0\.1:5173/);
  assert.match(readme, /upload.*create.*project/i);
  assert.doesNotMatch(readme, /Open 5175 as the normal/i);
  assert.doesNotMatch(readme, /127\.0\.0\.1:5175/);
  assert.match(readme, /do not.*--reload/i);
  assert.match(readme, /X-Ray/i);
  assert.match(readme, /same (address|application)/i);
});

test("full variant hydrates an existing query-string project while retaining upload creation", async () => {
  const app = await readFile(path.join(root, "DM2CApp.connected.jsx"), "utf8");
  const readme = await readFile(path.join(root, "README.md"), "utf8");

  assert.match(app, /getExistingProjectId\(typeof window === "undefined" \? "" : window\.location\.search\)/);
  assert.match(app, /initialExistingProjectId/);
  assert.match(app, /apiRequest\(`\/api\/projects\/\$\{initialExistingProjectId\}`\)/);
  assert.match(app, /setStarted\(true\)/);
  assert.match(app, /const startProject = async \(\) =>/);
  assert.match(app, /apiRequest\("\/api\/projects", \{ method: "POST", body: form \}\)/);
  assert.match(readme, /http:\/\/127\.0\.0\.1:5173\/\?projectId=/);
});
