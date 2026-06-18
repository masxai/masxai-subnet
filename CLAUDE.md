# CLAUDE.md — MASXAI Subnet **v1 (Lightweight)**
## Goal: a miner + validator that run smoothly on testnet netuid 501, today.

> This is the **v1** governance file. It deliberately scopes the subnet down to the
> smallest design that actually closes the reward loop on testnet. The full vision
> (35-doctrine geopolitical engine, commit-reveal, reputation, ensemble) lives in
> `CLAUDE_FULL.md` and is the v3 target. **Do not pull v3 machinery into v1.**

---

## 0. The one rule that shapes everything

A forecasting subnet only works on testnet if **the reward signal arrives fast**.
v1 forecasts **short-horizon TAO price direction**, which resolves in ~60 minutes
against a free public price oracle. No LLM, no human resolver, no Supabase.

The full MASXAI geopolitical engine is **not** the v1 miner. It plugs in later as a
second `ForecastCategory`. v1 ships with a zero-dependency baseline miner anyone can run.

---

## 1. What v1 is (and is not)

| | v1 (this doc) | v3 (CLAUDE_FULL.md) |
|---|---|---|
| Forecast target | TAO price direction, 60-min horizon | Geopolitics, macro, crypto, events |
| Miner | Zero-dependency baseline (no API keys) | Full MASXAI 7-stage pipeline |
| Resolution | Free price oracle (CoinGecko), automatic | LLM resolver + multi-turn web search |
| Reward | `1 − Brier`, EMA into scores | Brier decomposition + reputation + bonuses |
| Anti-copy | **Known limitation, documented** | Commit-reveal + similarity detection |
| Storage | In-memory + JSON state file | Supabase |
| Dependencies | `bittensor`, `httpx` | Gemini, GPT-4o, LlamaIndex, Supabase |

v1 is a **smoke test of the subnet mechanics**, not the product. Its job is to prove
the loop: validator issues → miner answers → validator resolves & scores → weights set.

---

## 2. The loop (memorize this)

```
Every epoch (~360 blocks / ~72 min on testnet), the validator:
  1. ISSUE   — build a price-direction question, snapshot current TAO price,
               query all miner axons, store each response in a PENDING queue.
  2. RESOLVE — for every pending forecast whose resolve_at time has passed,
               fetch the new price, compute outcome (up/down), score with Brier,
               EMA the score into self.scores[uid], move to RESOLVED.
  3. WEIGHT  — convert self.scores to normalized weights, set on chain.
```

The **deferred-resolution queue** is the heart of v1. Issuing and resolving are
decoupled in time — a forecast issued this epoch is scored a future epoch.

---

## 3. Build on the official template

v1 is built on `opentensor/bittensor-subnet-template` (SDK v10). You fork it and
drop in the v1 files. The template gives you `BaseMinerNeuron` / `BaseValidatorNeuron`
(wallet, metagraph sync, registration check, weight-setting plumbing) for free.

```
masxai-subnet/                         # your fork of the template
├── CLAUDE.md                          ← this file
├── masxai/
│   ├── __init__.py
│   ├── protocol.py                    ← ForecastSynapse (custom)
│   ├── constants.py                   ← v1 constants
│   ├── oracle.py                      ← free price feed + outcome resolution
│   └── scoring.py                     ← Brier + EMA reward
├── neurons/
│   ├── miner.py                       ← v1 baseline miner
│   └── validator.py                   ← v1 validator with deferred-resolution queue
└── (template files: base/, utils/, etc. — leave as-is)
```

---

## 4. v1 constants (do not expand in v1)

```python
NETUID = 501
NETWORK = "test"
SUBTENSOR_ENDPOINT = "wss://test.finney.opentensor.ai:443"

FORECAST_ASSET = "bittensor"          # CoinGecko id for TAO
FORECAST_HORIZON_SECONDS = 3600       # 60-minute resolution horizon
QUERY_TIMEOUT = 12                    # seconds for dendrite query
EMA_ALPHA = 0.1                       # score smoothing; higher = faster adaptation
MIN_RESOLVED_BEFORE_WEIGHTS = 1       # set weights as soon as anything resolves
NEUTRAL_PROB = 0.5                    # baseline / fallback probability
PROB_CLAMP_LO, PROB_CLAMP_HI = 0.01, 0.99
STATE_FILE = "validator_state.json"   # pending + resolved + scores persistence
```

---

## 5. Non-negotiables for v1 code

1. **Baseline miner runs with zero API keys.** If the reference miner needs a secret to start, it's wrong.
2. **Probabilities are always clamped to [0.01, 0.99].** Never 0 or 1 (Brier punishes certainty correctly, but clamp guards against degenerate inputs).
3. **The validator never crashes on a single failure.** Oracle down? Skip resolution this epoch, retry next. Miner times out? Score it neutral/penalize, continue.
4. **Pending forecasts persist to disk.** A validator restart must not lose in-flight forecasts (`STATE_FILE`).
5. **Resolution is automatic and objective.** No LLM in the v1 resolve path.
6. **Async everywhere** for I/O (dendrite, oracle httpx).
7. **One forecast category in v1** (`tao_price`). The enum has the others reserved, but v1 only issues `tao_price`.

---

## 6. Known v1 limitations (document, don't fix yet)

- **Copy-trading is possible.** Price-direction is trivially copyable; a miner could echo another's answer. v1 runs with operator-controlled miners only. **First v1.1 task: commit-reveal before opening to external miners.**
- **No reputation / specialization.** Flat EMA scoring only.
- **Single validator.** No resolution quorum.
- **No Brier decomposition.** Raw `1 − Brier` per forecast. Decomposition is a v2 task.

These are acceptable for a controlled testnet smoke test. They are **blockers for mainnet** and for opening miner registration to the public.

---

## 7. v1 → product path (the only roadmap that matters now)

```
v1.0  ← THIS: price-direction loop closes on 501, weights move, EMA works
v1.1  Commit-reveal anti-copy  → safe to open miner registration on testnet
v1.2  Add `crypto_markets` category (BTC/ETH direction) — same oracle pattern
v2.0  Brier decomposition (reliability − resolution + uncertainty) + reputation
v2.1  Plug MASXAI geopolitical engine in as a category with LLM-resolved questions
v3.0  Full CLAUDE_FULL.md: doctrines, multi-validator quorum, ensemble, Supabase
```

Do not start v1.1 until v1.0 has run unattended on 501 for at least 24h with weights
visibly updating in `btcli wallet overview --netuid 501 --subtensor.network test`.

---

## 8. Run commands (testnet 501)

```bash
# Register miner + validator on 501
btcli subnet register --netuid 501 --subtensor.network test \
  --wallet.name masxai-miner --wallet.hotkey default
btcli subnet register --netuid 501 --subtensor.network test \
  --wallet.name masxai-validator --wallet.hotkey default

# Stake to validator so it earns a validator permit (needed to set weights)
btcli stake add --netuid 501 --subtensor.network test \
  --wallet.name masxai-validator --wallet.hotkey default

# Run miner (opens an axon on :8901)
python neurons/miner.py --netuid 501 --subtensor.network test \
  --wallet.name masxai-miner --wallet.hotkey default \
  --axon.port 8901 --logging.debug

# Run validator
python neurons/validator.py --netuid 501 --subtensor.network test \
  --wallet.name masxai-validator --wallet.hotkey default --logging.debug

# Watch weights / emission move
btcli wallet overview --netuid 501 --subtensor.network test \
  --wallet.name masxai-validator
```

---

## 9. When Claude Code touches this repo

- v1 only. If a request implies v3 machinery (doctrines, commit-reveal, Supabase, LLM resolver), **stop and confirm** it's intended for v1 — almost always it isn't.
- Keep the baseline miner dependency-free.
- Any change to `protocol.py` (the Synapse) breaks registered miners — version it and confirm first.
- After any change to `scoring.py` or `oracle.py`, run the unit tests before suggesting a testnet run.

*v1.0 — lightweight, runnable, scoped for netuid 501 testnet.*
