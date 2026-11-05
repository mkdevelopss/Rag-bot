# Using Inkwell as the AI backend for your apps

Inkwell runs as one service. Each app (or each tenant of an app) gets its own
**namespace**: its own documents, vectors, conversations and assistant persona.
Apps talk to it server-to-server with a **service API key**.

```
MarketBid backend ─┐                       ┌─ namespace "marketbid"
                   ├─ /api/v1 (bearer key) ─┤
DistributionOS ────┘                       └─ namespaces "distos-<company_id>" (one per distributor)
        ▲
        └── Inkwell calls back your "app tools" for live data (order status, stock, repair status)
```

> Never put a service key in a browser, the Android APK, or any frontend code.
> The browser talks to *your* backend; your backend talks to Inkwell.

## 1. Create a key per app

```bash
cd backend
python -m app.cli generate-service-key --name marketbid --namespaces marketbid
python -m app.cli generate-service-key --name distos --namespaces "distos-*"
```

Each command prints the raw key (store it in that app's secrets, e.g. Vercel
environment variables) and a `name:hash:glob` line. Add the lines to
`SERVICE_API_KEYS` in `backend/.env`, comma-separated, and restart.
The `distos-*` glob lets one key serve every distributor tenant while keeping
each tenant's data separate.

## 2. Feed it knowledge

Upload files:

```bash
curl -X POST https://rag.example.com/api/v1/namespaces/marketbid/documents \
  -H "Authorization: Bearer $INKWELL_KEY" \
  -F "files=@how-bidding-works.pdf"
```

Or sync records from your database (FAQs, policies, product descriptions).
Sending the same `external_id` again **replaces** the old version, so you can
call this from a "save" hook:

```ts
// TypeScript (Next.js route, server action, or Node backend)
await fetch(`${process.env.INKWELL_URL}/api/v1/namespaces/marketbid/documents/text`, {
  method: "PUT",
  headers: {
    Authorization: `Bearer ${process.env.INKWELL_KEY}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    external_id: `faq-${faq.id}`,
    title: faq.question,
    text: `# ${faq.question}\n\n${faq.answer}`,
  }),
});
```

Delete with `DELETE /api/v1/namespaces/{ns}/documents/by-external-id/{external_id}`.
Indexing runs in the background; poll `GET .../documents/{document_id}` for `status: "ready"`.

## 3. Chat from your backend

```ts
const response = await fetch(`${process.env.INKWELL_URL}/api/v1/namespaces/marketbid/chat`, {
  method: "POST",
  headers: { Authorization: `Bearer ${process.env.INKWELL_KEY}`, "Content-Type": "application/json" },
  body: JSON.stringify({
    message,
    conversation_id,            // omit on the first message; reuse the one returned
    user_ref: String(user.id),  // each end user only sees their own conversations
    context: { user_id: user.id, role: user.role },  // verified by YOUR backend
  }),
});
const { answer, sources, conversation_id: id, steps } = await response.json();
```

Python:

```python
import httpx

reply = httpx.post(
    f"{INKWELL_URL}/api/v1/namespaces/distos-{company_id}/chat",
    headers={"Authorization": f"Bearer {INKWELL_KEY}"},
    json={"message": text, "user_ref": str(user.id),
          "context": {"user_id": user.id, "role": user.role, "company_id": company_id}},
    timeout=90,
).json()
```

For a typing effect, send `"stream": true` and forward the Server-Sent Events
(`meta`, `status`, `sources`, `delta`, `done`, `error`) to the browser.
`POST .../search` returns raw ranked passages if you only need retrieval
(e.g. "related help articles").

## 4. Give the agent live data (app tools)

Copy `backend/agent_config.example.json` to `backend/agent_config.json` and edit it.
Per namespace you set the assistant's name, extra instructions and tools. Keys
can be exact (`marketbid`) or globs (`distos-*`).

When the model calls a tool, Inkwell sends:

```http
POST https://api.marketbid.example/internal/ai-tools/repair-request-status
X-Tool-Secret: <value of MARKETBID_TOOL_SECRET>
Content-Type: application/json

{"arguments": {"request_id": "RR-1042"},
 "context": {"user_id": 9, "role": "customer"},
 "namespace": "marketbid"}
```

Your endpoint must:

1. Reject requests without the right `X-Tool-Secret` (compare in constant time).
2. **Authorize with `context`, never with `arguments`.** `context` comes from your
   own backend in step 3; `arguments` were written by the model, and a user can
   talk the model into asking for someone else's order. Return only data that
   `context.user_id` is allowed to see.
3. Return short JSON or text (it's truncated at 6,000 characters).

```ts
// Next.js: app/api/internal/ai-tools/repair-request-status/route.ts
import { timingSafeEqual } from "node:crypto";

export async function POST(request: Request) {
  const secret = Buffer.from(request.headers.get("x-tool-secret") ?? "");
  const expected = Buffer.from(process.env.MARKETBID_TOOL_SECRET!);
  if (secret.length !== expected.length || !timingSafeEqual(secret, expected)) {
    return new Response("forbidden", { status: 403 });
  }
  const { arguments: args, context } = await request.json();
  const repair = await db.repairRequest.findFirst({
    where: { id: args.request_id, customerId: context.user_id },  // ownership check
    select: { status: true, bids: { select: { shopName: true, pricePkr: true } } },
  });
  return Response.json(repair ?? { error: "No repair request with that id for this user." });
}
```

Set the same secret in Inkwell's environment (`MARKETBID_TOOL_SECRET=...`) and restart.

## Ideas that fit your projects

| App | Knowledge to sync | Useful tools |
|---|---|---|
| MarketBid | How bidding works, shop subscription plans, buy & sell rules, safety tips | repair request status, shop bids, device passport lookup |
| DistributionOS | Price lists, schemes/discounts, return policy, SOPs per role | stock level, order status, retailer outstanding balance, today's route |

## Hosting notes

- The embedding model needs ~1 GB RAM. Free tiers with 512 MB (e.g. Render free)
  will run out of memory; Hugging Face Spaces (Docker) or a small VPS works.
- Run one backend process (see README limitations), behind HTTPS.
- Back up the data volume/folder: SQLite, Chroma and uploads together.
