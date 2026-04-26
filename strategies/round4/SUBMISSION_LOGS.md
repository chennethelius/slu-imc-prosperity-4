# Getting a real round-4 submission log for calibration

The backtester is currently calibrated against round-3 day-3 only
(`486991/`, 12.21% error). Both kept round-4 strategies — `softer_vfruit.py`
and `spot_regime.py` — are tuned against synthetic QP=1.0 fill behavior,
which may diverge from live IMC sandbox match logic.

## Steps

1. **Submit both strategies via the IMC Prosperity portal**
   - Push `strategies/round4/softer_vfruit.py` as one submission.
   - Push `strategies/round4/spot_regime.py` as a second submission.
   - Each submission generates a numeric ID (e.g. `486991`).

2. **Download the submission package after sandbox finishes**
   - From the portal, grab the `.json`, `.log`, and `.py` for each.
   - The `.json` contains `profit` (real PnL), `activitiesLog`, and `tradeHistory`.

3. **Drop into a numbered folder at the repo root**
   ```
   <id>/
     <id>.json
     <id>.log
     <id>.py
   ```
   Match the existing `486991/` format exactly. Do this for both submissions.

4. **Re-calibrate the backtester**
   ```
   python scripts/calibrate_backtester.py
   ```
   Auto-picks the most recently modified folder. Sweeps QP ∈ {0.0, 0.25,
   0.5, 0.75, 1.0} and reports the best match. If the error drifts past
   ~5%, it means our fill model is wrong for round-4 specifically.

5. **Re-run both backtests at the calibrated QP**
   ```
   cd backtester && cargo run --release -- \
     --trader ../strategies/round4/softer_vfruit.py \
     --dataset round4 --queue-penetration <calibrated> --products full
   cd backtester && cargo run --release -- \
     --trader ../strategies/round4/spot_regime.py \
     --dataset round4 --queue-penetration <calibrated> --products full
   ```
   The Pareto frontier may shift — if `spot_regime` beats `softer_vfruit`
   on mean+min under the new calibration, promote it as the primary.

## What we'll learn

- **Whether v02-style aggressive divergence over-trades real fills.** Our
  $244k D1 number assumes our ask gets full competitor flow. Live fills
  may be much lower.
- **Whether spot_regime's de-leverage pays in real money or just in the
  backtester's optimistic fill model.** The $65k D3 vs v02's $54k might
  collapse to <$5k difference live.
- **True QP for round-4 voucher books.** Round-3 was QP=1.0, but round-4
  voucher chains have wider spreads and competitor flow patterns may
  differ substantially.
