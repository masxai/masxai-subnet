# CLAUDE.md - MASXAI Subnet MVP

## Goal

Build and maintain a Bittensor forecasting subnet for Bittensor ecosystem events.
Miners generate structured forecasts with Gemini. Validators store forecasts,
resolve objective outcomes after the forecast window, score miners, and update
weights.

## Current Implementation

This repo keeps a simple, runnable deferred-resolution loop:

1. Validator snapshots objective reference data.
2. Validator queries all serving miner axons with `ForecastSynapse`.
3. Miner returns a structured forecast.
4. Miner optionally posts the forecast summary to Discord.
5. Validator persists the forecast in `validator_state.json`.
6. After `resolve_at`, validator resolves objective ground truth and updates
   `self.scores`.

## Forecast Types

The protocol supports:

- `tao_price_movement`
- `subnet_token_price`
- `new_subnet_registration`
- `governance_outcome`
- `ecosystem_growth_metric`
- `significant_bittensor_event`

Only add an event type to `ENABLED_EVENT_TYPES` after adding an automatic,
objective resolver in `masxai/oracle.py`. Unsupported event types should remain
available in the protocol but should not be issued by default.

## Miner Rules

- Gemini is the MVP forecasting engine.
- Use `GEMINI_API_KEY` or `GOOGLE_API_KEY`.
- Default model is `gemini-2.5-flash`; operators can set `GEMINI_MODEL`.
- Missing Gemini keys must not crash the miner; return the neutral baseline.
- Discord publishing is optional through `DISCORD_WEBHOOK_URL`.
- The miner must return:
  - event type
  - boolean prediction
  - confidence score
  - forecast horizon
  - AI reasoning summary
  - timestamp

## Validator Rules

- Never score a forecast without objective ground truth.
- If an oracle fails, keep the forecast pending and retry next epoch.
- Persist pending forecasts and scores so restarts do not lose in-flight work.
- Keep scoring deterministic and local to validator code.
- Weights come from `self.scores` through the template validator machinery.

## Scoring

Use the MVP scoring formula:

```text
Final Score =
50% Accuracy +
20% Confidence Calibration +
20% Historical Consistency +
10% Timeliness
```

The Brier helpers in `masxai/scoring.py` are retained for compatibility tests and
probability-only miners.

## Before Changing Protocol

`masxai/protocol.py` is the wire contract. Any field change can break running
miners, so bump `SYNAPSE_VERSION`, update miner and validator together, and run
the scoring tests plus the mock loop.

## Useful Commands

```bash
source .venv/bin/activate
python -m pytest tests/test_scoring.py -v
python scripts/mock_run.py
```
