# CHARLIE frontend

Fresh V1 foundation: full-viewport environment, vector Charlie Core, and a
small live-runtime adapter. Normal mode connects to Charlie's `/ws` stream;
`?fixture=...` remains local-only demonstration mode.

```powershell
npm install
npm run dev
```

Validation commands:

```powershell
npm run typecheck
npm run lint
npm test
npm run build
git diff --check
```

Run the documented backend from the repository root when live events are
needed:

```powershell
..\.venv\Scripts\python.exe run.py
```
