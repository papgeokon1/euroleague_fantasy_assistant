# CLAUDE.md

AI assistant for the EuroLeague Fantasy Challenge. The plan is in docs/ROADMAP.md.

## Commands
- Install: `pip install -e ".[dev,api]"` (inside `.venv`)
- Test: `pytest`
- Lint and format: `ruff check .` and `ruff format .`
- CLI: `elf --help`
- Web app: `elf serve` (API, plus the built frontend at http://localhost:8000).
  Frontend in `frontend/`: `npm run build`, `npm run dev` (hot reload, proxies /api),
  `npm run lint`.

## Conventions
- Package code lives in `src/elfantasy/`. Notebooks are for exploration only.
- The API (`src/elfantasy/api/`) is a thin layer over the core: put logic in the core, not in
  routes. Requests open the database briefly; never hold a connection across requests.
- Game rules live only in `src/elfantasy/rules.py`. Never hard-code a rule anywhere else.
- No lookahead leakage: features for round N use only data available before round N's deadline.
- A model or strategy change has to beat the current baseline in the backtest before it ships.
- The LLM explains and reads news. The projection model and the optimizer make the picks.
- `data/` and `models/` are git-ignored and must be reproducible from code.
