import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

function assetFileNames(assetInfo) {
  const names = [
    ...(assetInfo.names ?? []),
    assetInfo.name ?? "",
  ];
  if (names.some((name) => name.endsWith("web-ifc.wasm"))) {
    return "assets/web-ifc.wasm";
  }
  return "assets/[name]-[hash][extname]";
}

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        assetFileNames,
      },
    },
  },
  worker: {
    rollupOptions: {
      output: {
        assetFileNames,
      },
    },
  },
});
