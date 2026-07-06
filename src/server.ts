#!/usr/bin/env node
// server.ts - HTTP transport. 3가지를 서빙한다:
//   POST /mcp            MCP Streamable HTTP (type:http 커넥터용)
//   GET  /               라이브 대시보드(standalone 플래그 주입 -> 브라우저에서 fetch REST 사용)
//   POST /api/tool/:name 도구 REST (MCP 와 동일한 buildTools 핸들러 공유)
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import cors from "cors";
import express from "express";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import fs from "node:fs/promises";
import { registerAll, buildTools } from "./mcp-tools.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 3002);

const server = new McpServer({ name: "config-monitor", version: "0.1.0" });
registerAll(server, __dirname);

// REST 용 도구 맵 (MCP 와 동일 핸들러)
const toolMap = Object.fromEntries(buildTools(__dirname).map((d) => [d.name, d.run]));

const app = express();
app.use(cors());
app.use(express.json({ limit: "20mb" }));

// 라이브 대시보드: 빌드된 단일파일에 standalone 플래그를 주입해 서빙.
app.get("/", async (_req, res) => {
  try {
    let html = await fs.readFile(join(__dirname, "dist", "dashboard.html"), "utf-8");
    html = html.replace("<head>", '<head><script>window.__CONFIG_MONITOR_HTTP__=true;</script>');
    res.type("html").send(html);
  } catch {
    res.status(500).send("dist/dashboard.html 없음 - 먼저 'npm run build' 를 실행하세요.");
  }
});

app.get("/health", (_req, res) => {
  res.json({ status: "ok", server: "config-monitor", version: "0.1.0" });
});

// 도구 REST: 브라우저(standalone) 대시보드가 호출. MCP 와 동일 핸들러.
app.post("/api/tool/:name", async (req, res) => {
  const fn = toolMap[req.params.name];
  if (!fn) return res.status(404).json({ error: `unknown tool: ${req.params.name}` });
  try {
    const r = await fn(req.body || {});
    res.json({ text: r.content?.[0]?.text ?? "" });
  } catch (e) {
    res.status(500).json({ error: String(e) });
  }
});

app.post("/mcp", async (req, res) => {
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined,
    enableJsonResponse: true,
  });
  res.on("close", () => transport.close());
  await server.connect(transport);
  await transport.handleRequest(req, res, req.body);
});

app.listen(PORT, () => {
  console.error(`[config-monitor] HTTP at http://localhost:${PORT}/ (dashboard) · /mcp · /api/tool/:name`);
});
