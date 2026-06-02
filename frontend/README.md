# ActionsPlane UI

React + Vite + TanStack Query dashboard for the ActionsPlane control plane.

```sh
npm install
npm run dev      # http://localhost:5173, proxies /api -> FastAPI on :8000
npm run build    # type-check + production build to dist/
```

Views: repository list, cross-repo run grid (status filter), run detail with jobs and
per-workflow metrics. Live updates arrive over Server-Sent Events (`/api/v1/events/stream`)
and invalidate the React Query caches for sub-second refresh.
