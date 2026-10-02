import { IfcImporter } from "@thatopen/fragments";
import webIfcWasmUrl from "web-ifc/web-ifc.wasm?url";

function messageFrom(error) {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return String(error);
}

function wasmDirectory() {
  const wasmUrl = new URL(webIfcWasmUrl, globalThis.location.href);
  return new URL("./", wasmUrl).href;
}

globalThis.onmessage = async ({ data }) => {
  if (data?.type !== "convert" || !(data.ifcBytes instanceof ArrayBuffer)) {
    globalThis.postMessage({
      type: "error",
      message: "The fragment Worker received invalid IFC bytes",
    });
    return;
  }

  try {
    globalThis.postMessage({
      type: "progress",
      progress: 0,
      message: "Converting IFC",
    });

    const importer = new IfcImporter();
    importer.wasm.path = wasmDirectory();
    importer.wasm.absolute = true;
    const fragmentBytes = await importer.process({
      bytes: new Uint8Array(data.ifcBytes),
      raw: false,
    });
    const transferableBytes = fragmentBytes.buffer.slice(
      fragmentBytes.byteOffset,
      fragmentBytes.byteOffset + fragmentBytes.byteLength,
    );

    globalThis.postMessage(
      {
        type: "result",
        fragmentBytes: transferableBytes,
      },
      [transferableBytes],
    );
  } catch (error) {
    globalThis.postMessage({
      type: "error",
      message: messageFrom(error),
    });
  }
};
