import { copyFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const source = new URL("../node_modules/web-ifc/web-ifc.wasm", import.meta.url);
const target = new URL("../public/web-ifc/web-ifc.wasm", import.meta.url);

mkdirSync(dirname(fileURLToPath(target)), { recursive: true });
copyFileSync(fileURLToPath(source), fileURLToPath(target));
