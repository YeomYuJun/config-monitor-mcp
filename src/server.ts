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
import { registerAll, buildTools, langScript } from "./mcp-tools.ts";

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
    // standalone 플래그 + (env 지정 시) 시작 언어. 언어는 MCP 위젯 경로와 같은 주입을 쓴다.
    html = html.replace("<head>", '<head><script>window.__CONFIG_MONITOR_HTTP__=true;</script>' + langScript());
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

// 루프백 전용 바인딩: /api/tool/:name 은 인증 없이 편집 도구를 그대로 노출하고 cors() 도 전 오리진
// 허용이다. 이 서버는 로컬 설정 파일을 읽고 쓰는 용도라 원격 사용 자체가 대상이 아니므로,
// 0.0.0.0(기본값)으로 열어 같은 네트워크의 다른 기기에 노출될 이유가 없다.
app.listen(PORT, "127.0.0.1", () => {
  console.error(`[config-monitor] HTTP at http://127.0.0.1:${PORT}/ (dashboard) · /mcp · /api/tool/:name`);
});
