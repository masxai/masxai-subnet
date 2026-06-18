# MASXAI Subnet — v1 (Lightweight)

A minimal, runnable Bittensor forecasting subnet for **testnet netuid 501**.

v1 forecasts **short-horizon TAO price direction** ("will TAO be higher in 60
minutes?") so the reward loop closes within the hour — no LLM, no human resolver,
no database. The full MASXAI geopolitical engine plugs in later as a second
forecast category (see `CLAUDE.md` → roadmap).

## Why price-direction for v1?

A forecasting subnet's reward signal must arrive fast enough to validate the
mechanics on testnet. Geopolitical forecasts resolve in weeks; you can't debug a
loop that pays out in 14 days. TAO price direction resolves in 60 minutes against
a free oracle, so every hour you see real Brier scores, EMA updates, and weight
changes. Prove the mechanics here, then swap in harder forecast categories.

## Architecture

```
validator                              miner
   │  issue: snapshot price, query ───────►  predict P(price higher)
   │  store in PENDING queue          ◄───────  return probability
   │
   │  (60 min later)
   │  resolve: fetch new price, score with Brier
   │  EMA into per-miner score
   │  set weights on chain
```

The **deferred-resolution queue** in `neurons/validator.py` is the core idea:
forecasts are issued one epoch and scored a later epoch when the horizon passes.

## Files

| File | Role |
|------|------|
| `masxai/protocol.py` | `ForecastSynapse` wire protocol |
| `masxai/constants.py` | All v1 constants (netuid 501, 60-min horizon, etc.) |
| `masxai/oracle.py` | Free CoinGecko price feed + outcome resolution (soft-fail) |
| `masxai/scoring.py` | Brier score → reward → EMA |
| `neurons/miner.py` | Zero-dependency baseline miner |
| `neurons/validator.py` | Validator with deferred-resolution queue |
| `tests/test_scoring.py` | Verify scoring math before testnet |

## Setup

This repo is meant to be a **fork of `opentensor/bittensor-subnet-template`** so
that `template.base.miner` / `template.base.validator` are importable. Copy the
`masxai/` package and `neurons/` files into your fork.

```bash
git clone https://github.com/opentensor/bittensor-subnet-template.git masxai-subnet
cd masxai-subnet
# drop in the masxai/ package and neurons/miner.py, neurons/validator.py
pip install -e .
pip install -r requirements.txt
```

## Test first

```bash
pytest tests/test_scoring.py -v
```

Expected: coin-flip reward = 0.75, confident-correct ≈ 1.0, confident-wrong ≈ 0.02.

## Watch the loop locally (no testnet)

Before registering on chain, you can watch the whole issue → resolve → weight
loop close in ~30 seconds against a simulated price feed and mock miners:

```bash
python scripts/mock_run.py
# options: --miners 12 --steps 18 --tick 1.5 --horizon 3 --drift 0.8
```

This runs the **real** validator logic (`resolve_due`, `issue_round`, Brier
scoring, EMA, state persistence) and only stubs the network primitives
(metagraph/wallet/dendrite) and the oracle (a random walk with upward drift, so
the 60-min horizon is compressed to seconds). Mock miners run distinct
strategies (bullish / bearish / baseline / flaky); you should see bullish miners
climb the EMA leaderboard and earn the most weight, bearish miners earn the
least — confirming the scoring rule rewards calibration.

## Register on testnet 501

```bash
btcli subnet register --netuid 501 --subtensor.network test \
  --wallet.name masxai-miner --wallet.hotkey default
btcli subnet register --netuid 501 --subtensor.network test \
  --wallet.name masxai-validator --wallet.hotkey default

# stake to the validator so it earns a permit to set weights
btcli stake add --netuid 501 --subtensor.network test \
  --wallet.name masxai-validator --wallet.hotkey default
```

## Run

```bash
# miner (axon on :8901)
python neurons/miner.py --netuid 501 --subtensor.network test \
  --wallet.name masxai-miner --wallet.hotkey default \
  --axon.port 8901 --logging.debug

# validator
python neurons/validator.py --netuid 501 --subtensor.network test \
  --wallet.name masxai-validator --wallet.hotkey default --logging.debug
```

## Verify it's working

```bash
btcli wallet overview --netuid 501 --subtensor.network test \
  --wallet.name masxai-validator
```

Within ~1–2 hours you should see the validator log resolved forecasts, scores
updating, and EMISSION/INCENTIVE columns becoming non-zero for the miner.

## Known v1 limitations (intentional)

- **Copy-trading is possible** — price-direction answers are trivially copyable.
  Run only operator-controlled miners in v1. **Add commit-reveal (v1.1) before
  opening registration to the public.**
- No reputation, no specialization, single validator, no Brier decomposition.
  All deferred — see roadmap in `CLAUDE.md`.

## Building a real miner

Replace `predict()` in `neurons/miner.py`. The contract: given the synapse,
return `P(price_at_resolve > reference_price)` in `[0.01, 0.99]`. A momentum
signal, an order-book model, an LLM, or the full MASXAI engine all slot in here
without touching the validator or protocol.
