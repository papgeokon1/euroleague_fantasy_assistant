# EuroLeague Fantasy Assistant

An AI assistant that plays the [EuroLeague Fantasy Challenge](https://www.euroleaguebasketball.net/en/euroleague/news/euroleague-fantasy-challenge-returns-for-2026-27/) as well as possible. It projects player PIR, optimizes squad, lineup, captain and transfers under the game's rules, and explains its picks.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the plan.

## Setup

Requires Python 3.12+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

## Usage

```powershell
elf --help
elf rules     # game rules the assistant assumes
```

## Development

```powershell
pytest              # tests
ruff check .        # lint
ruff format .       # format
```

## Layout

```
src/elfantasy/   package code (rules, data, models, optimizer, assistant)
tests/           pytest tests
data/raw/        downloaded source data (git-ignored)
data/processed/  cleaned tables (git-ignored)
models/          trained model files (git-ignored)
notebooks/       exploration only; reusable code moves into src/
docs/            roadmap and design notes
```
