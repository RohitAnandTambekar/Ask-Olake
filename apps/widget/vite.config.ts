import preact from "@preact/preset-vite";
import { defineConfig } from "vitest/config";

// Builds one self-contained file, apps/api/static/widget.js, that the API serves.
// CSS is inlined into the bundle (imported with ?inline) and injected into a shadow root.
export default defineConfig({
  plugins: [preact()],
  build: {
    outDir: "../api/static",
    emptyOutDir: false, // the API's index.html lives there too
    sourcemap: false,
    lib: {
      entry: "src/main.tsx",
      name: "AskOLake",
      formats: ["iife"],
      fileName: () => "widget.js",
    },
  },
  test: {
    environment: "jsdom",
  },
});
