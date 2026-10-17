// Photon bridge (Photon Beta REST API, https://api.photon.codes). Run: node --env-file=../.env bridge.mjs
//   POST /send {"to": "+1...", "body": "..."}   (Authorization: Bearer PHOTON_BRIDGE_SECRET) -> sends a text
//   inbound texts (polled via listMessages) are forwarded to the Python app: POST $SENTINEL_URL/photon/inbound
//   {"from","text"} signed with X-Photon-Signature = hex HMAC-SHA256(PHOTON_WEBHOOK_SECRET, raw body).
// A shared line can't start a conversation: the person must text the line first. /send replies into that
// conversation using the space/sender ids of their latest inbound text.
import http from "node:http";
import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";

const {
  PHOTON_PROJECT_ID, PHOTON_API_KEY, PHOTON_PROJECT_SECRET, PHOTON_WEBHOOK_SECRET = "",
  PHOTON_BRIDGE_SECRET = "", PHOTON_BRIDGE_PORT = "3001", PHOTON_POLL_MS = "2000",
  PHOTON_API_URL = "https://api.photon.codes", SENTINEL_URL = "http://127.0.0.1:8080",
} = process.env;
const API_KEY = (PHOTON_API_KEY || PHOTON_PROJECT_SECRET || "").trim();
const PROJECT = (PHOTON_PROJECT_ID || "").trim();

if (!PROJECT || !API_KEY) {
  console.error("Set PHOTON_PROJECT_ID and PHOTON_API_KEY (or PHOTON_PROJECT_SECRET) in agent/.env.");
  process.exit(1);
}

async function api(method, path, { query, body, headers = {} } = {}) {
  const url = new URL(`${PHOTON_API_URL}/v1/projects/${PROJECT}${path}`);
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined) url.searchParams.set(k, v);
  const r = await fetch(url, {
    method,
    headers: { Authorization: `Bearer ${API_KEY}`, "content-type": "application/json", ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status} ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : {};
}

const digits = (s) => String(s ?? "").replace(/\D/g, "");
const contexts = new Map();                   // phone digits -> {spaceId, agentId}
const seen = new Set();                       // inbound message ids already handled

function remember(m) {
  const handle = digits(m.sentBy?.handle);
  if (!handle || !m.space?.id || !m.receivedBy?.id) return;
  contexts.set(handle, { spaceId: m.space.id, agentId: m.receivedBy?.id });
}

async function listReceived(createdSince) {
  const items = [];
  let pageToken;
  do {
    const page = await api("GET", "/messages", {
      query: { direction: "received", platform: "imessage", order: "asc", pageSize: "100", createdSince, pageToken },
    });
    items.push(...(page.items ?? []));
    pageToken = page.nextPageToken;
  } while (pageToken);
  return items;
}

async function send(to, body) {
  let ctx = contexts.get(digits(to));
  if (!ctx) {                                  // e.g. after a restart: rebuild from history
    for (const m of await listReceived()) remember(m);
    ctx = contexts.get(digits(to));
  }
  if (!ctx) throw new Error(`no conversation with ${to} yet: they must text the shared line first`);
  await api("POST", "/messages", {
    headers: { "idempotency-key": randomUUID() },
    body: {
      spaceId: ctx.spaceId, senderId: ctx.agentId,   // the line's own user id; parentMessageId (a threaded reply) gets 409 SPACE_NOT_SENDABLE
      content: { type: "text", format: "plain", text: body },
    },
  });
}

function authorized(req) {
  const got = Buffer.from(req.headers.authorization ?? "");
  const want = Buffer.from(`Bearer ${PHOTON_BRIDGE_SECRET}`);
  return PHOTON_BRIDGE_SECRET !== "" && got.length === want.length && timingSafeEqual(got, want);
}

async function forward(from, text) {
  const body = JSON.stringify({ from, text });
  const sig = createHmac("sha256", PHOTON_WEBHOOK_SECRET).update(body).digest("hex");
  const r = await fetch(`${SENTINEL_URL}/photon/inbound`, {
    method: "POST", body, headers: { "content-type": "application/json", "X-Photon-Signature": sig },
  });
  if (!r.ok) console.error("sentinel rejected inbound:", r.status);
}

http.createServer(async (req, res) => {
  if (req.method !== "POST" || req.url !== "/send") { res.writeHead(404).end(); return; }
  if (!authorized(req)) { res.writeHead(401).end(); return; }
  let raw = "";
  for await (const chunk of req) raw += chunk;
  try {
    const { to, body } = JSON.parse(raw);
    await send(to, body);
    res.writeHead(200, { "content-type": "application/json" }).end('{"ok":true,"status":"sent"}');
  } catch (e) {
    console.error("send failed:", e.message);
    res.writeHead(502, { "content-type": "application/json" }).end(JSON.stringify({ ok: false, status: String(e.message) }));
  }
}).listen(Number(PHOTON_BRIDGE_PORT), "127.0.0.1", () => console.log(`bridge listening on :${PHOTON_BRIDGE_PORT}`));

// Photon reachable? Fail loudly at startup instead of on the first text.
const platform = await api("GET", "/platforms/imessage");
console.log(`photon beta connected (iMessage mode: ${platform.mode})`);

// History only primes reply contexts; only texts that arrive from now on are forwarded.
for (const m of await listReceived()) { seen.add(m.id); remember(m); }
let since = new Date().toISOString();

for (;;) {
  await new Promise((r) => setTimeout(r, Number(PHOTON_POLL_MS)));
  try {
    for (const m of await listReceived(since)) {
      if (seen.has(m.id)) continue;
      seen.add(m.id);
      remember(m);
      const c = m.content;
      if (c?.type !== "text") continue;
      console.log(`inbound from ${m.sentBy?.handle}: ${c.text}`);
      await forward(m.sentBy?.handle, c.text);
      if (m.createdAt > since) since = m.createdAt;
    }
  } catch (e) {
    console.error("poll failed:", e.message);
  }
}
