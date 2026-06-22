# MasXAI Subnet MVP

MasXAI is a Bittensor forecasting subnet for Bittensor ecosystem events. Miners
run Gemini-backed forecasting agents, validators resolve objective ground truth,
score forecasts, and set miner weights from forecast quality.

The current implementation keeps the proven deferred-resolution loop from the
lightweight v1 code, then adds the MVP forecast schema, Gemini miner path, and
Discord publishing.

## MVP Forecasts

Supported event taxonomy:

- `tao_price_movement`
- `subnet_token_price`
- `new_subnet_registration`
- `governance_outcome`
- `ecosystem_growth_metric`
- `significant_bittensor_event`

The validator only issues event types listed in `masxai/constants.py` as
`ENABLED_EVENT_TYPES`. By default this is `tao_price_movement`, because it has
automatic objective resolution through the price oracle. Add more event types to
that list only after adding an objective resolver in `masxai/oracle.py`.

## Forecast Schema

Miner responses follow the MVP schema:

```json
{
  "forecast_id": "uuid",
  "event_type": "tao_price_movement",
  "prediction": true,
  "confidence": 0.92,
  "forecast_window": "1h",
  "reasoning": "network activity and price momentum remain positive",
  "timestamp": "ISO8601"
}
```

## Miner Workflow

1. Receive a validator forecasting task.
2. Build a Gemini prompt from the task and validator-supplied context.
3. Generate a structured forecast.
4. Return the forecast to the validator.
5. Publish a summary to Discord when `DISCORD_WEBHOOK_URL` is configured.

Gemini configuration lives in a local `.env` file:

```bash
cp .env.example .env
```

Then edit `.env`:

```env
GEMINI_API_KEY=your-gemini-key
GEMINI_ENABLED=true
GEMINI_MODEL=gemini-2.5-pro
GEMINI_TIMEOUT=8
DISCORD_WEBHOOK_URL=your-discord-webhook
MASXAI_FALLBACK_TAO_PRICE_USD=
MASXAI_FORECAST_INTERVAL_SECONDS=300
```

`.env` is ignored by git. The miner loads it automatically.

`MASXAI_FALLBACK_TAO_PRICE_USD` is optional. Leave it empty for objective
oracle-based scoring. For testnet/dev only, set it to a TAO/USD value if your
machine cannot reach CoinGecko, Binance, or Kraken and the validator logs
`oracle unavailable; skipping issue this epoch`.

`MASXAI_FORECAST_INTERVAL_SECONDS` controls how often the validator asks miners
for a new forecast. `300` means one forecast round every 5 minutes.

Without a Gemini key, the miner returns a neutral baseline forecast so local
testing still works.

If the miner logs `ConnectTimeout` for Gemini, the server cannot reach
`generativelanguage.googleapis.com` quickly enough. You can raise
`GEMINI_TIMEOUT` up to about `15`, or set `GEMINI_ENABLED=false` to run explicit
baseline mode until outbound connectivity is fixed.

## Validator Workflow

1. Snapshot objective reference data.
2. Query miner axons with a `ForecastSynapse`.
3. Store miner forecasts in the pending queue.
4. Wait for `resolve_at`.
5. Fetch actual outcome from the oracle.
6. Score each forecast.
7. EMA the score into `self.scores` so the template weight machinery can submit
   weights on chain.

Pending forecasts and scores are persisted to `validator_state.json`.

## Scoring

Structured forecasts use the MVP weighted score:

```text
Final Score =
50% Accuracy +
20% Confidence Calibration +
20% Historical Consistency +
10% Timeliness
```

Legacy Brier helpers remain in `masxai/scoring.py` for probability-only tests and
older local mocks.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -r requirements.txt
cp .env.example .env
```

Run tests:

```bash
python -m pytest tests/test_scoring.py -v
```

Run the local loop without chain access:

```bash
python scripts/mock_run.py
```

## Testnet 501

```bash
btcli subnet register --netuid 501 --subtensor.network test \
  --wallet.name masxai-miner --wallet.hotkey default
btcli subnet register --netuid 501 --subtensor.network test \
  --wallet.name masxai-validator --wallet.hotkey default

btcli stake add --netuid 501 --subtensor.network test \
  --wallet.name masxai-validator --wallet.hotkey default

python neurons/miner.py --netuid 501 --subtensor.network test \
  --wallet.name masxai-miner --wallet.hotkey default \
  --axon.port 8901 --logging.debug

python neurons/validator.py --netuid 501 --subtensor.network test \
  --wallet.name masxai-validator --wallet.hotkey default --logging.debug
```

## MVP Economics

The product target is to burn 95% of miner emissions and distribute 5% according
to validator weights. This repo currently computes and submits weights; emission
burn mechanics must be enforced in subnet economics/runtime configuration, not
inside miner forecast code.
