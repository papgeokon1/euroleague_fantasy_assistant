# Frontend

React + Vite + TypeScript app for the EuroLeague Fantasy assistant. It talks to the
FastAPI backend in `src/elfantasy/api/`. Every number comes from the API: projections,
the optimizer, turn advice and news.

Pages: **My team** (squad, projections, lineup advice), **Optimize** (the optimizer,
run as a background job), **Compare** (up to 6 players side by side), **Chat** (the
assistant, with its tool calls streamed as it works).

## Run it

Use the built app (one server):

```
cd frontend && npm install && npm run build
elf serve            # http://localhost:8000
```

During development, with hot reload:

```
elf serve --reload   # API on :8000
cd frontend && npm run dev   # app on http://localhost:5173, proxies /api to :8000
```

Checks: `npm run lint` (oxlint) and `npm run build` (type-check + bundle).
