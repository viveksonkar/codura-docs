/// <reference types="vitest" />
/**
 * Widget build (channels.md §3, M2-07): Vite library mode.
 *   default mode → dist/widget.js (IIFE, script-tag auto-mount) + dist/widget.esm.js
 *   --mode hosted → dist/hosted.js (Hosted Chat Page bootstrap)
 * public/ (hosted.html, demo.html) is copied into dist/. nginx serves dist/ at /widget/v1/.
 */
import { defineConfig } from "vite";

export default defineConfig(({ mode }) => {
  const hosted = mode === "hosted";
  return {
    esbuild: { jsx: "automatic", jsxImportSource: "preact", legalComments: "none" },
    define: { "process.env.NODE_ENV": JSON.stringify("production") },
    publicDir: hosted ? false : "public",
    build: {
      outDir: "dist",
      emptyOutDir: !hosted,
      target: "es2019",
      sourcemap: true,
      minify: "esbuild",
      lib: hosted
        ? { entry: "src/hosted/main.ts", name: "CoduraHosted", formats: ["iife"], fileName: () => "hosted.js" }
        : {
            entry: "src/main.ts",
            name: "CoduraChatWidget",
            formats: ["iife", "es"],
            fileName: (format) => (format === "es" ? "widget.esm.js" : "widget.js"),
          },
    },
    test: { environment: "happy-dom" },
  };
});
