import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

function assetFileNames(assetInfo) {
  const names = [...(assetInfo.names ?? []), assetInfo.name ?? ""];
  return names.some((name) => name.endsWith("web-ifc.wasm"))
    ? "assets/web-ifc.wasm"
    : "assets/[name]-[hash][extname]";
}

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: { assetFileNames },
    },
  },
  worker: {
    rollupOptions: {
      output: { assetFileNames },
    },
  },
});
