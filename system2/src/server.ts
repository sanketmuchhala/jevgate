import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { fileURLToPath } from "node:url";
import { createOpenRouter } from "@openrouter/ai-sdk-provider";
import type { LanguageModel } from "ai";
import { review } from "./review.ts";
import { ReviewRequestSchema } from "./schema.ts";

const MAX_BODY_BYTES = 1_000_000;

function send(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw new Error("body too large");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

export function createHandler(opts: { model: LanguageModel; modelId: string; timeoutMs: number }) {
  return async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    if (req.method === "GET" && req.url === "/healthz") {
      return send(res, 200, { status: "ok", model: opts.modelId });
    }
    if (req.method !== "POST" || req.url !== "/v1/review") {
      return send(res, 404, { error: "not found" });
    }

    let body: unknown;
    try {
      body = JSON.parse(await readBody(req));
    } catch (err) {
      return send(res, 400, { error: `unreadable body: ${(err as Error).message}` });
    }
    const parsed = ReviewRequestSchema.safeParse(body);
    if (!parsed.success) {
      return send(res, 400, { error: "invalid request", issues: parsed.error.issues });
    }

    try {
      send(res, 200, await review(opts.model, parsed.data, { timeoutMs: opts.timeoutMs }));
    } catch (err) {
      // The Python router treats any non-200 as SYSTEM_TWO_UNAVAILABLE and routes to a human.
      send(res, 502, { error: (err as Error).message });
    }
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) {
    console.error("OPENROUTER_API_KEY is not set");
    process.exit(1);
  }
  const modelId = process.env.SYSTEM2_MODEL ?? "anthropic/claude-sonnet-5.5";
  const port = Number(process.env.SYSTEM2_PORT ?? 8787);
  const timeoutMs = Number(process.env.SYSTEM2_TIMEOUT_MS ?? 15_000);
  const model = createOpenRouter({ apiKey })(modelId);

  createServer(createHandler({ model, modelId, timeoutMs })).listen(port, () => {
    console.log(`system2 reviewer on :${port} using ${modelId}`);
  });
}
