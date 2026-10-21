# CLAUDE.md

AI assistant for the EuroLeague Fantasy Challenge. The plan is in docs/ROADMAP.md.

## Commands
- Install: `pip install -e ".[dev]"` (inside `.venv`)
- Test: `pytest`
- Lint and format: `ruff check .` and `ruff format .`
- CLI: `elf --help`

## Conventions
- Package code lives in `src/elfantasy/`. Notebooks are for exploration only.
- Game rules live only in `src/elfantasy/rules.py`. Never hard-code a rule anywhere else.
- No lookahead leakage: features for round N use only data available before round N's deadline.
- A model or strategy change has to beat the current baseline in the backtest before it ships.
- The LLM explains and reads news. The projection model and the optimizer make the picks.
- `data/` and `models/` are git-ignored and must be reproducible from code.
