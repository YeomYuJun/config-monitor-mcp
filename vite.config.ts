import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";
import { fileURLToPath } from "node:url";

// src/dashboard.html + src/mcp-app.ts -> src/dist/dashboard.html (단일 파일 번들)
// root=src 로 두어 출력 엔트리명이 dashboard.html 로 나오게 하고,
// input 은 config 위치 기준 절대경로로 잡아 CWD 의존 없이 항상 해석되게 한다.
export default defineConfig({
  root: "src",
  plugins: [viteSingleFile()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL("src/dashboard.html", import.meta.url)),
    },
  },
});
