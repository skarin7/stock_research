# HANDOFF — multi-agent stock-trading system

Branch: `claude/multi-agent-stock-research-mTCHX` · PR #3 (ready-for-review, do **not** merge to master yet).
Read `CLAUDE.md` (auto-loads) and the PR #3 description for full context.

## Where things stand

Re-platformed the 7-stage `main.py` pipeline onto a **LangGraph multi-agent system** (`run_agents.py`), wrapping the existing modules (not rewriting them). `main.py` still runs; research mode reproduces its report.

**All 7 roadmap iterations done:**
1. LangGraph scaffolding (state, contracts, `agent_node` guard, research+analyst nodes, Postgres/obs with fallbacks)
2. OpenRouter provider switch (`llm_router.py`, `LLM_PROVIDER=anthropic|openrouter`)
3. Bull/Bear debate subgraph (`agents/nodes/debate.py`, bounded by `MAX_DEBATE_ROUNDS`/`DEBATE_TOP_N`)
4. Risk + Portfolio gates + paper-mode fills (`risk.py`, `portfolio.py`, `trading.py`)
5. Groww broker + `interrupt()` human approval + Telegram/CLI resume (`broker/groww_trader.py`, `approval.py`)
6. Scheduled Monitoring (`monitoring.py`, `build_monitor_graph`, `--mode monitor`)
7. Memory + signal self-eval (`memory.py`, long-term store in `persistence/store.py`)

Plus: Terraform deploy (`deploy/deploy.sh` + `deploy/terraform/`, `import.sh` to adopt existing gcloud resources), CI (`.github/workflows/tests.yml`), opt-in metrics push (`PROMETHEUS_PUSHGATEWAY_URL`).

**Graph:** `research → analyst → [debate → risk → portfolio → trading] → finalize → memory → END`; trading chain gated OFF by default. Separate `START → monitor → END` for `--mode monitor`.

## Verify

```bash
python -m pytest tests/ -q          # 68 pass; tests mock config — no keys/DB needed
python run_agents.py --mode research --dry-run   # needs API keys + network
```

## Not done (deliberate human gates before live trading)

- Verify `growwapi.place_order` params/constants against the installed SDK (the only untested seam, gated off).
- Set `DATABASE_URL` (Neon) — required for cross-process approval resume.
- Validate cheaper OpenRouter models against the backtest before trusting them.
- Run paper mode before flipping `ENABLE_LIVE_TRADING`.

## Optional next steps

- Feed memory (`store.recent_calls` / `latest_signal_perf`) back into scoring weights (the self-eval loop is recorded but not yet consumed).
- Grafana Cloud dashboards: needs Grafana Alloy (remote_write) — Langfuse Cloud already covers per-run LLM cost/trace history.

## Deploy notes

- `deploy/deploy.sh` provisions compute + scheduler and injects creds; you create **Neon** + **Langfuse Cloud** and paste keys into `terraform.tfvars`.
- Already deployed via `setup_gcp.sh`? Run `deploy/terraform/import.sh` first (zero cost, no teardown) so Terraform adopts the existing job/repo/SAs/scheduler.

## Session note

PR #3 activity subscription does **not** carry across sessions — re-subscribe in the new session if you want CI/review autofixing.
