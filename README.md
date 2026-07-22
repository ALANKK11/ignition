# IGNITION

**Find tomorrow's tape, tonight.** A pre-session scanner that ranks the entire liquid US equity universe by the probability of *abnormal volume and range* in the next session — long before it's on anyone's feed.

```
  ___ ____ _   _ ___ _____ ___ ___  _   _
 |_ _/ ___| \ | |_ _|_   _|_ _/ _ \| \ | |
  | | |  _|  \| || |  | |  | | | | |  \| |
  | | |_| | |\  || |  | |  | | |_| | |\  |
 |___\____|_| \_|___| |_| |___\___/|_| \_|
```

## Philosophy: read the tape, not the headlines

Anyone can read news. By the time a headline exists, the move it describes has usually started. What is actually *forecastable* about tomorrow is not direction — it's **activity**, and activity leaves fingerprints the day before:

1. **Volume is autocorrelated.** Abnormal volume today is the single best predictor of abnormal volume tomorrow. This is one of the oldest stylized facts in market microstructure.
2. **Volatility clusters.** Big ranges follow big ranges (ARCH effects). A name whose 5-day ATR just detached from its 20-day ATR is in a different regime.
3. **Compression precedes expansion.** A Bollinger-width percentile pinned at its lows, or an NR7 day (narrowest range in seven sessions), is a spring being loaded — especially sitting on a scheduled catalyst.
4. **After-hours is tomorrow's open, forming in real time.** An extended-hours move confirmed by real extended-hours volume is the market repricing *right now* while most participants are at dinner.
5. **Scheduled catalysts are free information.** Earnings tonight or tomorrow morning is a guaranteed volume event. You don't need sentiment analysis for a calendar.
6. **Crowded shorts are dry fuel.** High short-%-of-float plus days-to-cover doesn't ignite anything by itself — but when the tape sparks, it turns a move into a violent one. The signal is momentum-gated for exactly that reason.

IGNITION measures all of this and blends it into one number. News never enters the score. If the news matters, it's already in the volume, the range, and the after-hours prints — measured, not narrated.

## What a scan does

**Pass 1 — the funnel.** Builds a candidate universe from a static liquid seed list (~200 names), your personal `watchlist.txt`, and Yahoo's live screeners (most actives, gainers, losers, small-cap gainers, most shorted). Downloads ~6 months of daily bars for everything in one batched call, applies liquidity filters, and computes the cheap signals: RVOL, volume z-score, ATR expansion, Bollinger squeeze + NR7, closing-strength pin, 20-day breakout/breakdown pressure.

**Pass 2 — enrichment.** The top ~60 by base score (plus your entire watchlist, always) get the expensive per-name work: minute-bar extended-hours tape, next earnings date, short interest stats, and nearest-expiry ATM implied vol compared against Yang-Zhang realized vol — when options are pricing an event the stock hasn't shown yet, that ratio screams.

**Score.** Each component maps onto a fixed, interpretable 0–1 scale (not a cross-sectional z-score), then blends by configurable weights. Fixed scales mean **a 60 tonight means the same thing as a 60 last month**, which is what makes the journal meaningful. Components that couldn't be measured are excluded from both numerator and denominator — a name is never punished for missing data. Empirically: pure noise sits in the teens, a single strong archetype lands in the 30s, and 55+ is rare multi-signal confluence — those are the ones.

Every row shows **WHY**: archetype tags (`EARNINGS Jul 22`, `AH +5.2%`, `COILED`, `NR7`, `SQUEEZE 28%SI`, `IGNITED 6.5x`, `20D HIGH`, `IV 110%`) plus the top weighted drivers. No black box.

## The hub (phone mode — the way this is meant to be lived with)

You don't have to run anything. Push this folder to a GitHub repo, flip two
settings, and GitHub's own schedulers run the evening scan, the premarket
confirm, and a flow reading every 20 minutes through the session — each run
re-renders a static, dark, mobile-first dashboard served free at
`https://<you>.github.io/<repo>/`. Add it to your phone's home screen and it
behaves like an app: wake up, tap, see last night's ranked board, the morning
confirm, the live fade/rotation states, and the self-audit ICs. Total cost:
$0.00 — free Actions compute, free Pages hosting, free Yahoo data. Exact
5-minute setup: **`deploy/SETUP.md`**. (`python ignition.py hub` renders the
same page locally into `docs/` from your latest state.)

The honest tradeoff: hub flow updates every ~20 minutes (GitHub's scheduler
floor, plus occasional queue delays), which is built for glancing, not
execution. When you're actively in a trade, run `python ignition.py flow`
locally for the 75-second live board.

## The ritual (terminal mode)

```bash
pip install -r requirements.txt

python ignition.py scan                 # evening scout — run ~6:30–9:00pm ET,
                                        # after AH tape and earnings develop
python ignition.py scan --premarket     # morning confirm — run ~7:00–9:15am ET,
                                        # re-ranks on pre-market tape instead
python ignition.py flow                 # during the session: live money-rotation
                                        # board — where flow is, entering, LEAVING
python ignition.py eval                 # after any later close: grade past scans
python ignition.py history              # rolling IC / edge across all scans
```

Evening scan builds the watch plan; the pre-market run catches overnight surprises the evening couldn't see and confirms (or kills) the evening's candidates. Useful flags: `--fast` (skip enrichment; scores not comparable to full scans), `--top 40`, `--enrich 100`, `--csv out.csv`, `--json out.json`, `--tv watch.txt` (comma list that imports straight into a TradingView watchlist), `--demo` (fully offline synthetic market for kicking the tires).

## FLOW — the intraday rotation board

The scan gets you positioned the night before. `flow` answers the two questions that matter once the bell rings: **where is the money right now, and where is it going next** — specifically the pattern where the morning's leaders are still green on every screen while the participation that made them move has already left.

Raw intraday volume comparisons lie, because volume is U-shaped: 5M shares by 10:00 and 5M by 14:00 are different animals. So every reading is normalized by each name's *own* typical cumulative-volume curve for that exact minute (built from its last ~5 sessions of minute bars). Two numbers result: **PACE** (on track for X× a normal day) and **TP**, the trailing-15-minute participation vs what normally trades in that exact 15-minute slot. TP is the tell — it collapses when the money leaves, long before the chart looks broken.

On top of that, a per-name state machine with memory (peak participation, opening pace, maximum VWAP extension):

| state | meaning |
|---|---|
| `FADING` | **the retail trap.** Peak participation was real (≥2×), trailing pace has collapsed to <35% of peak, but price still sits near HOD and the day is still green. Tape cooled; chart hasn't. The crowd walking in is buying from the money walking out. |
| `LEAVING` | confirmed exit: pace off peak *and* VWAP lost after having been extended above it, price rolling. |
| `NEW MONEY` | was **not** an opening leader, now ≥2× participation and rising, taking flow share — where the invisible hand went. |
| `IGNITING` | ≥2.8× trailing pace, accelerating vs the prior window, price actually moving. Fresh aggression in progress. |
| `CHURN` | heavy tape, zero progress, pinned at highs — absorption fight. Effort without result. |
| `RUNNING` / `COOLING` / `QUIET` | sustained · rolling off · nothing. |

Downgrade states are sticky (hysteresis), so a name doesn't flap between LEAVING and RUNNING on boundary noise. The **ROTATION** panel ranks flow against each name's *normal* share of tape — so a small cap running 4× its usual flow outranks a megacap drifting at 1× — with the OUT column showing exactly how much of peak participation remains (watching a leader tick 45% → 25% → 9% of peak while its chart holds +11% is the whole point). Every state transition is timestamped, printed, and journaled to `flow_events` in SQLite, so the tape reads become auditable history, not vibes.

Run it with `python ignition.py flow` (defaults: your last scan's top picks + live most-actives/gainers + your watchlist, refreshed every 75s). Useful flags: `--window 10`, `--interval 45`, `--ticks N`, `--demo` (a scripted session, continuous with the scan demo: last night's #1 pick pumps and fades, the rotation target ignites at lunch).

## The self-audit (the part that keeps it honest)

Every scan journals its ranked picks **plus a random control sample from the same filtered universe** into SQLite (`~/.ignition/journal.db`). `eval` then pulls the next session's actual bars and grades the scan on two numbers:

- **Rank IC** — Spearman correlation between the score and realized next-day activity (blend of realized-RVOL rank and realized-range rank). Positive and persistent → the ranking carries information.
- **Edge vs control** — mean realized RVOL and range of the top-10 picks divided by the random controls. **Edge 1.0x means the scanner is decorative.** In the planted demo it grades ~2.9x volume edge with IC ≈ +0.47; on live data expect something far more modest — volume/vol persistence is a real but not enormous effect. The point is you'll *know*, from your own journal, instead of trusting vibes.

`history` shows the rolling mean IC and the fraction of positive-IC days across every scan you've ever run. If the edge decays, you'll see it — and you can retune.

## Tuning

Everything lives in `config.yaml`: component weights (relative multipliers — crank what you trust, zero what you don't), liquidity floors (`min_price`, `min_dollar_volume` — lower them if you hunt small caps), screener list, funnel sizes, and signal thresholds (e.g. what |AH move| earns a full score). `watchlist.txt` names bypass all filters and always get full enrichment.

## Data sources, and APIs worth paying for

Out of the box everything runs on Yahoo via `yfinance` — zero keys, zero cost. Its honest ceiling: 1-minute bars lag roughly 1–2 minutes, extended-hours volume is thin for illiquid names, and screeners are ~15-minute delayed. Fine for a 15-minute rotation window; not a scalping feed.

If you want to hand the machine a key, in order of value per dollar: **Polygon.io** (Stocks Starter ~$29/mo: unlimited REST calls, 15-min-delayed full-market snapshots, all history; Advanced ~$199/mo: real-time — the single-call full-market snapshot means FLOW can watch *every* US listing instead of a 60-name monitor set, which is the true "invisible hand" detector). **Alpaca** (free with a brokerage account: real-time IEX feed via WebSocket — genuinely free real-time, ~3% of tape but directionally honest). **Finnhub** (free tier for earnings calendars, paid for real-time). **IBKR** (if you already have the account, the TWS API gives real-time everything, shorts availability included, but the integration burden is the highest). The provider layer is a small interface built for exactly this swap — Polygon support is the next stage of this build.

## Data + extending

Free data source is Yahoo via `yfinance`: batched daily bars, minute pre/post bars, earnings calendar, short stats, option chains, live screeners. Every call is defensive — a flaky ticker or changed endpoint degrades that one signal to "not measured" instead of crashing the scan. The entire data layer is one small interface (`src/providers.py`: six methods). If you have a Polygon, IBKR, or Alpaca subscription, implement those six methods against it and everything else — signals, scoring, report, journal — works unchanged, with better AH volume and real-time screeners.

## Honest limitations

Yahoo's extended-hours volume is thin and sometimes missing for illiquid names (the AH component's volume-confidence weighting exists precisely for this). Screener data is ~15-minute delayed. Earnings BMO/AMC timing isn't always distinguishable, so "earnings today or tomorrow" is treated as one bucket. An evening scan can't see catalysts that drop overnight — that's what `--premarket` is for. Halts, offerings, and gap-only sessions can make a "hit" untradeable in practice. And to say it plainly: **this ranks expected *activity*, not direction, and it is not investment advice** — a top rank means expect volume and range, which cuts both ways. The journal exists so the tool has to prove itself to you.

## Layout

```
ignition.py            CLI (scan / flow / hub / eval / history / paths)
.github/workflows/     the autopilot: evening + premarket + flow schedules
deploy/SETUP.md        5-minute phone-hub setup, $0
src/hub.py             static mobile dashboard renderer
config.yaml            weights, universe, thresholds
universe_seed.txt      ~200-name liquid + high-beta seed list (edit freely)
watchlist.txt          your names — always enriched, never filtered
src/providers.py       data layer (live yfinance + swappable interface)
src/signals.py         signal math (Yang-Zhang vol, ATR, squeeze, AH, ...)
src/scoring.py         composite blend, driver attribution, archetype tags
src/universe.py        funnel construction
src/journal.py         SQLite journal, IC + edge grading
src/report.py          rich terminal report + CSV/JSON/TradingView export
src/demo.py            deterministic synthetic market with planted archetypes
```
