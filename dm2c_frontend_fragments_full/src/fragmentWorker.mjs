import { IfcImporter } from "@thatopen/fragments";
import webIfcWasmUrl from "web-ifc/web-ifc.wasm?url";

function errorMessage(error) {
  return error instanceof Error && error.message ? error.message : String(error);
}

function wasmDirectory() {
  const wasmUrl = new URL(webIfcWasmUrl, globalThis.location.href);
  return new URL("./", wasmUrl).href;
}

globalThis.onmessage = async ({ data }) => {
  if (data?.type !== "convert" || !(data.ifcBytes instanceof ArrayBuffer)) {
    globalThis.postMessage({
      type: "error",
      error: "The fragment Worker received invalid IFC bytes",
    });
    return;
  }

  try {
    globalThis.postMessage({ type: "progress", message: "Converting IFC" });
    const importer = new IfcImporter();
    importer.wasm.path = wasmDirectory();
    importer.wasm.absolute = true;
    const converted = await importer.process({
      bytes: new Uint8Array(data.ifcBytes),
      raw: false,
    });
    const fragmentBytes = converted.buffer.slice(
      converted.byteOffset,
      converted.byteOffset + converted.byteLength,
    );
    globalThis.postMessage(
      { type: "converted", fragmentBytes },
      [fragmentBytes],
    );
  } catch (error) {
    globalThis.postMessage({ type: "error", error: errorMessage(error) });
  }
};
