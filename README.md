# LensPlan

[![CI](https://github.com/Akhilesh-Vangala/lensplan/actions/workflows/ci.yml/badge.svg)](https://github.com/Akhilesh-Vangala/lensplan/actions/workflows/ci.yml)

Production and distribution planner for a two-plant lens network (Fort Worth and Singapore).

The mixed-integer program minimizes production, setup, holding, freight, and stockout cost. A simulated-annealing search over setups, with each candidate costed as an LP, is included as a baseline. Extra line-hours are valued by re-solving the MIP, not by reading the LP dual.

## Stack

- Optimizer: Gurobi (when licensed) or HiGHS
- Backend: FastAPI, SQLAlchemy 2, PostgreSQL
- Frontend: React 18, TypeScript, Vite
- Ports: UI 3006, API 8006, Postgres 5435

## Model

Indices: line `l`, SKU `k`, plant `p`, DC `d`, period `t`.

- Variables: production `x`, binary setup `y`, shipments `s`, inventory `I`, unmet demand `u`
- Constraints: line hours, setup linking, minimum run length, at most four SKUs per line per period, plant flow, DC balance, shelf life

Unmet demand keeps the model feasible. `y` is a setup indicator, not a sequenced changeover.

## Default instance

10 SKUs, 2 plants, 4 lines, 3 DCs, 8 periods. 1,360 variables, 870 constraints.

| Method | Objective | Runtime |
|---|---|---|
| Gurobi MIP | $917,481 | 1.47 s |
| Annealing + LP | $923,447 | 3.7 s |
| Gap | 0.65% | |

HiGHS is the CI default. The UI time limit is 90s so a clone without Gurobi still returns a feasible plan.

On this instance the LP relaxation prices Singapore L1, period 3 at $39.95/hour. Re-solving the MIP with one extra hour realizes about half of that (`GET /runs/{id}/marginal-value`).

## Quick start

```bash
git clone https://github.com/Akhilesh-Vangala/lensplan.git
cd lensplan
cp .env.example .env
docker compose up --build
```

Open http://localhost:3006. API: http://localhost:8006. Postgres: localhost:5435.

Without Docker:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && python seed.py && uvicorn app.main:app --port 8006
```

Frontend: `cd frontend && npm install && npm run dev`

### Demo accounts

| Email | Password | Role |
|---|---|---|
| planner@lensplan.example.com | planner-password | planner |
| analyst@lensplan.example.com | analyst-password | analyst |
| admin@lensplan.example.com | admin-password | admin |

Default JWT secret is `change-me-in-production`. Local use only.

## Tests

```bash
cd backend
pytest -q
```

39 tests. Gurobi agreement tests skip when no license is present.

## License

MIT. See [LICENSE](LICENSE).
