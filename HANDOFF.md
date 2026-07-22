# IGNITION — HANDOFF BRIEFING

**Read this before touching anything.** You are inheriting a working project mid-flight from another Claude instance. This document exists so you do not repeat mistakes that took many painful iterations to find. The user has no patience left for rediscovery — everything below was learned the hard way, usually by the user catching a bad output on his phone and naming the ticker.

---

## 1. WHO THE USER IS (the most important section)

He is a retail trader who trades **high-volatility, high-risk/high-reward small caps**. Not investing. Not swing trading blue chips. He hunts the names running 50–800% intraday, usually sub-$10, often sub-$1, frequently nano-cap.

**Tickers he named as GOOD examples of what he trades:** `CPHI`, `OMH`, `SLGB` — small caps that jumped hard and fast with multiple legs.

**Tickers he named as USELESS to him** (all of which the tool surfaced at some point, to his fury): `UTZ`, `DHR`, `AMG`, `CAVA`, `QQQ`, `UTX`. These are mega/mid-caps, index funds, or one-print gaps. If your output contains names like these at the top, **you have already failed.**

What he actually needs, in his own words, distilled:

- **Where the money is going**, and — equally — **when the money leaves.** He described the exact pain: a stock pumps into the open, the volume quietly exits, the chart still looks green, and retail keeps buying the top. He wants that moment flagged.
- **Where the money rotates to next**, while everyone is still staring at the old leader.
- **Earlier.** Constantly. "Get on the hype train earlier." Every architecture decision should be evaluated against latency-to-discovery.
- **Tradable moves, not big moves.** This distinction took the longest to learn — see §4, item 10. A stock that gaps 88% in one minute and then flatlines is worthless to him. A stock that travels 300% across nine legs with pullbacks is his entire business.
- **Direction-agnostic.** Big down moves are as tradable as big up moves. Do not build long-only logic.
- **On his phone.** He will not open a terminal. He will not run commands. He wakes up, taps a bookmark, and expects to be ready.
- **Free.** He explicitly capped spend at ~$0.10/scan and said plainly he'd rather pay nothing. Everything runs on free tiers. Do not propose paid data as a first resort — he will read it as laziness. If free genuinely cannot do something, say so directly and prove it.

### How to work with him

- **Never ask him to verify or test.** He said this explicitly and repeatedly. You build it, you test it yourself with fixtures, and you report what passed and what is still unproven. He will not run a demo for you.
- **Be short.** He has told the previous instance multiple times that responses are too long. Lead with the result. No preamble, no throat-clearing, no re-explaining what he already knows.
- **No excuses, no apology spirals.** He said: "I don't give a fuck about that excuse... All I care about is the results." When something is broken, diagnose the root cause and fix it. Do not defend prior work.
- **He curses heavily and gets angry.** It is not personal, and it is usually a signal that a real defect exists. Every single time he blew up, he was right. Treat his anger as a high-quality bug report.
- **He names tickers when output is wrong.** This is the best debugging channel in the project. When he says "look at AMG," go look at AMG, find the root cause, and fix that class of bug — not just that ticker.
- **Verify claims against reality when you can.** Web search is legitimate and valuable here. When the tool surfaced UTZ/DHR/AMG, searching the actual news proved the data layer was correct and the *ranking logic* was wrong — which redirected the entire fix. Do this before assuming a data bug.

---

## 2. WHAT THIS IS

**IGNITION** — a self-running scanner that finds tradable volatility in US equities and renders it to a phone-friendly static web page. It runs itself on GitHub's infrastructure at zero cost. The user's only interaction is opening a bookmark.

- **Repo:** `github.com/ALANKK11/ignition` (public)
- **Hub URL:** `alankk11.github.io/ignition` (GitHub Pages, serving `/docs`)
- **Compute:** GitHub Actions (free tier, public repo = unlimited minutes)
- **Cost:** $0.00. No paid data. No card on file.

### Repo secrets (already configured by the user)

| Secret | Purpose |
|---|---|
| `ALPACA_KEY_ID` | Alpaca market data (free paper account, IEX feed) |
| `ALPACA_SECRET_KEY` | " |
| `FINNHUB_KEY` | Earnings calendar (free tier) |

Required repo settings, already done: **Settings → Pages** → branch `main`, folder `/docs`. **Settings → Actions → General → Workflow permissions** → *Read and write*.

---

## 3. ARCHITECTURE

### Entry point: `ignition.py` (CLI)

| Command | What it does |
|---|---|
| `scan` | Nightly forecast: ranks the universe for *tomorrow's* likely activity from daily bars. `--premarket` re-ranks on pre-market tape. |
| `flow` | One or many regular-hours readings: full-market radar + per-name state machine. |
| `ext` | Full-market pre-market / after-hours ignition sweep (`--session pre|post`). |
| `live` | **The main event.** A long-running shift that ticks continuously, routing to `ext`/`flow` by clock, polling newswires, and pushing the hub. |
| `hub` | Renders `docs/index.html` from current state files. |
| `eval` | Grades past scans and past ignition calls against realized data. |
| `history` | Rolling IC / edge across scans. |
| `paths` | Prints config/journal/state locations. |

### Modules (`src/`)

| File | Responsibility |
|---|---|
| `config.py` | `DEFAULTS` dict + YAML deep-merge from `config.yaml`; path resolution (`IGNITION_HOME` env). |
| `util.py` | NY timezone helpers, trading-day math, manual Spearman (no scipy), formatters. |
| `providers.py` | `Provider` interface + `LiveProvider` (yfinance): daily bars, intraday, earnings, short stats, ATM IV, screeners. Fully exception-safe — any failure degrades one signal, never crashes. |
| `providers_alpaca.py` | `AlpacaData`: REST client for Alpaca Market Data v2 (IEX feed). Assets master, chunked snapshots (400/call), paginated multi-symbol bars, corporate actions. |
| `providers_finnhub.py` | Earnings calendar in one call, BMO/AMC aware. Returns `None` on failure → Yahoo fallback. |
| `newswire.py` | **Catalyst lane.** Polls free RSS from GlobeNewswire/PRNewswire/Accesswire, extracts tickers from headlines via exchange-prefix regex, validates against the live symbol master, scores catalysts, dedupes. |
| `signals.py` | Yang-Zhang volatility, ATR, Bollinger squeeze, NR7, RVOL, closing strength, breakout proximity; `base_components()` maps everything to fixed [0,1] scales; `is_deal_pin()`. |
| `scoring.py` | Weighted composite (available components only), driver attribution, archetype tags. |
| `universe.py` | Funnel construction + `ETF_EXCLUDE` (74 index/levered/commodity vehicles, hard-excluded everywhere). |
| `flow.py` | Intraday state machine. Time-of-day-normalized `PACE`/`TP`; states: `IGNITING`, `NEW MONEY`, `RUNNING`, `CHURN`, `FADING`, `LEAVING`, `COOLING`, `QUIET`, `OPEN DRIVE`, `PM HOT`. Hysteresis on downgrades. |
| `flow_alpaca.py` | Full-market radar, `ext_sweep()` (hardened), `movers_from_radar()`, `path_stats()`/heat, `assemble_board()`, `fast_update()` (45s lane), promotions. |
| `journal.py` | SQLite: scans, picks, flow_events, evals, ignition_evals. Rank-IC and edge-vs-control grading. `JsonlJournal` sidecar for the live looper + `ingest_jsonl_events()`. |
| `hub.py` | Renders the entire static mobile page. No external assets, no CDN. Version-gates state files. |
| `report.py` | Rich terminal rendering + CSV/JSON/TradingView export. |
| `demo.py` | Deterministic synthetic market with planted archetypes. `--demo` works fully offline — use it for regression testing. |

### Workflows (`.github/workflows/`)

| File | Schedule (ET) | Purpose |
|---|---|---|
| `live.yml` | 6:55am and 12:55pm, Mon–Fri | Two long shifts covering 7am–6:52pm. Ticks the fast lane every ~45s, full discovery every ~160s, pushes the hub each tick. |
| `evening.yml` | 9:15pm Mon–Fri | Grades yesterday, runs tonight's scan, renders hub. |
| `premarket.yml` | 7:45am Mon–Fri | Pre-market re-rank scan. |

**Cron is UTC.** The current expressions assume US daylight time. After the November clock change everything fires an hour early ET unless the cron lines are shifted.

### State model

Everything the hub renders comes from JSON state files in `$IGNITION_HOME/state/` (in CI: `data/state/`, committed back to the repo by the bot):

- `latest_board.json` — **the board** (what the user sees first)
- `latest_scan.json`, `latest_flow.json`, `latest_radar.json`, `latest_movers.json`, `latest_ext.json`
- `alpaca_base_<date>.json` — daily baselines cache (ADV, prev closes, volume curves)
- `flow_store_<date>.json` — state machine memory (peak participation, VWAP extension)
- `board_seen_<date>.json` — **first-seen timestamps** (powers "since 7:41a" and the NEW badge)
- `ignitions_<date>.json`, `radar_promoted_<date>.json`
- `flow_events_<date>.jsonl` — live looper's append-only event log, ingested into SQLite by `eval`

**`STATE_V` (currently 4) is stamped into board/ext/movers state.** The hub refuses to render state written by an older code version. This exists because stale state once haunted the page for days — see §4, item 7.

---

## 4. THE FAILURE LOG — read this, it is the real value of this document

Every item is a real defect the user caught in production. Do not regress any of them.

1. **Yahoo-only + 20-minute cron was too slow and blind pre-market.** Fixed by adding Alpaca and then by replacing cron polling with long-running shifts.

2. **AMG — the closing-auction contamination.** Alpaca's `16:00` minute bar contains the closing auction, which is *regular-session* volume. Every liquid name therefore passed the "$X of after-hours tape" floor using auction volume, and a single 1-share odd lot then set the "gap." **Fix:** after-hours window starts at **16:01**, and gaps are computed from the session's dollar-weighted **VWAP**, never a single `latestTrade` print.

3. **UTX — ghost liquidity.** A name with almost no real tape where a few hundred shares both set the reference price and cleared the dollar floor. **Fix:** breadth requirements — minimum distinct traded minutes, minimum shares, minimum dollars — all **doubled** for names whose own ADV is tiny in dollar terms.

4. **DHR — mechanical gap.** A mega-cap showing a -18% "gap" on 0.08% of its normal volume: corporate-action arithmetic, not selling. **Fix:** extended-hours shares must clear a minimum fraction of the name's own ADV.

5. **Splits.** A 1:10 reverse split reads as +900%. **Fix:** exclude names with a split effective today via Alpaca's corporate-actions endpoint; if that endpoint fails, exclude any gap landing within 2% of a clean split ratio.

6. **QQQ appeared in the scan.** An index ETF can never be "the name the whole market is watching tomorrow." **Fix:** `ETF_EXCLUDE` (74 symbols) applied to scan universe, radar universe, movers, and promotions; purged from the seed file.

7. **Stale state haunted the hub.** The page re-rendered old garbage after new code deployed, because state files had no version. **Fix:** `STATE_V` stamps; the hub drops any state written by a different version.

8. **The radar ranked by volume anomaly, so dead-but-weird-volume names sat on top.** Volume anomaly is how the engine decides what to *watch*; it is not what to *show*. **Fix:** the displayed list became a movers board filtered on `|move| ≥ 15%` with real dollar volume and elevated participation.

9. **Too many sections.** The hub had six. The user needs one. **Fix:** THE BOARD is the page; everything else is a small strip below it.

10. **UTZ — the most important lesson in the project.** The tool ranked UTZ #1 at +88.7% with 31x volume. Web search confirmed the data was **exactly correct**: Intersnack agreed to acquire Utz at $14.25/share cash, a 91% premium. But the pick was worthless — a cash buyout gaps once and then pins to the deal price, trading a 2% band forever. The user's complaint: "in one minute the price went 40 times... for the rest of the day it's just stale... no one could have called that."
   **Two fixes came out of this:**
   - `is_deal_pin()` — a move ≥20% whose intraday range is ≤25% of the move is arbitrage, not volatility. Scan score ×0.30; board shows a `PIN · DEAD` chip.
   - **The ranking metric changed entirely** (item 11).

11. **Ranking by size of move was wrong; ranking is now by tradability.** `path_stats()` computes: total intraday travel (Σ|1-min returns| with a sub-spread noise floor), **minus the single largest print** — which mathematically zeroes one-print gaps — plus a zigzag **swing count** at ≥7% legs, plus travel in the last 30 minutes for recency. These compress into **heat (0–100)**, which is the board's sort order.
   Calibration verified on four shapes: UTZ replica → **heat 0.0**; nine-leg CPHI replica (345% travel, still moving) → **99.9, 8 swings**; runner that died at 11:30 → 26; DHR-style mega-cap chop → 2.3.

12. **Latency.** GitHub's cron floor is ~20 minutes, which the user rejected. **Fix:** `live` command — long-running shifts, full discovery every ~160s, fast lane every ~45s.

13. **The hub went silently empty.** When board state was missing, the page
    rendered only the nightly forecast strip, so the user saw two mega-caps
    and reasonably concluded the whole tool was broken. **Fix:** a loud
    `NO BOARD DATA` diagnostic naming the likely cause and pointing at the
    Actions tab, plus the forecast strip is now explicitly headed "forecast
    ... not today's movers" so the two can never be confused again.

14. **The scan universe excluded his entire trading class.** See open item 5.

15. **No news awareness.** The user pointed out these moves are known before the tape confirms. **Fix:** `newswire.py` — the press release *is* t=0 of the move. Free RSS, polled every ~45s, tickers extracted and validated, catalysts scored, name pushed onto the board with the headline attached and a heat floor so it surfaces before volume confirms.

16. **config.yaml silently negated the universe-band fix (2026-07-22, caught same night).** The band was fixed in `DEFAULTS` (config.py) but `config.yaml` still carried `min_price: 1.0` / `min_dollar_volume: 5000000` — and **yaml deep-merges OVER defaults, so yaml always wins.** The 1:47am scan therefore ran the new full-market engine inside a $1–$50 / $5M–$300M band: liquid-mid-caps-only by construction. Top of the forecast: SBRA, AS, KLAR, CELH, ALLY, RRC — the exact class from items UTZ/DHR/AMG, seventh occurrence. **Rule: any change to `DEFAULTS` must be mirrored in `config.yaml` in the same commit.**

17. **Even inside the correct band, liquid names out-rank small ones** — rvol/squeeze/vol_trend are cleaner on liquid tape. Fix: `capacity_mult()` in scoring.py, applied to scan score at both passes (ignition.py). Full weight ≤$5M dollar ADV, log-taper to ×0.35 at the $300M ceiling: SBRA-class pays ×0.63, ALLY-class ×0.41, CPHI-class untouched. Knobs: `scan.capacity_full_below`, `scan.capacity_floor`. Intraday board is unaffected (heat already handles this).

18. **The hub's failure states were themselves failures.** Seven duplicate ungraded audit cards rendered as red "…" walls (same-date scans now dedupe, ungraded shows dim "grades after close", all-pending collapses to one quiet line), and a missing board at 2am fired the NO-BOARD alarm implying breakage (banner is now clock-aware: off-hours/weekend says "market closed — nothing is broken", shift-hours absence still alarms loudly).

### Concurrency note

The live looper writes flow events to a **JSONL sidecar**, never to SQLite, so the parallel `evening`/`premarket` jobs can never binary-conflict with the database in git. `eval` ingests the sidecars (renaming them `.done`) before grading.

---

## 5. WHAT IS PROVEN vs WHAT IS NOT

**Be precise about this with the user. He has zero tolerance for claims presented as tested when they aren't.**

### Verified with offline fixtures (all passing)

- All extended-hours guards: auction contamination, stale quotes, ghost liquidity, mechanical gaps, splits (both the corporate-actions path and the ratio-heuristic fallback).
- `is_deal_pin()` on the **real** July 21 2026 numbers: UTZ pinned; DHR (-11%, 5.1% range) and AMG (-7%, 4.7% range) correctly kept; a genuine +80% runner with 64% range kept.
- Heat/tradability calibration on four synthetic shapes (see §4.11).
- Newswire: CPHI-style PR extracted and scored, OMH-style offering kept and flagged `⚠OFFERING` (dumps are tradable too), ETF/unknown/no-ticker items dropped, dedupe holds across polls.
- Fast lane end-to-end: a PR for an unwatched name lands on the board within one 45s cycle at the right price/heat with the headline, while existing rows keep their first-seen memory.
- JSONL journal → ingest → ignition-grading pipeline.
- Full-market radar and promotion persistence against a 600-symbol fake Alpaca API.
- Board assembly: first-seen survives session changes, `NEW` fires exactly once per name per day, states join onto rows, heat sort correct.
- Complete `--demo` regression (scan → flow → eval → hub) after every change.
- Capacity tilt calibrated on the exact 2026-07-22 offender classes (SBRA/CELH/ALLY $ADVs) and confirmed not to reorder planted demo archetypes (IGNA #1, DMPD #2 hold).
- Audit strip fixed against the literal 7-row all-ungraded state that rendered on 2026-07-22; banner tested at 1:50am, 11am, Saturday, and Friday-evening clocks.

### NEVER verified against live services

The build sandbox has no network access to Yahoo, Alpaca, Finnhub, or the newswires. Therefore these paths have only ever run against fixtures:

- Real Alpaca API responses (auth, rate limits, actual field shapes at scale).
- Real Finnhub responses.
- **The RSS feed URLs.** These are the newest and least certain component. Wires move their endpoints. The shift log prints per-feed HTTP status; dead feeds are skipped and named; the list is plain config (`news.feeds` in `config.yaml`) so a moved endpoint is a one-line edit.
- GitHub Actions runtime behavior: whether a 6-hour shift survives without being killed, whether `git pull --rebase` contention between the live shift and the evening job causes push failures under real load.
- The ignition receipts hit-rate. The grading machinery works; there is no live data in it yet.
- The widened band ($0.10 floor, $200k dollar-ADV floor) against the real Alpaca master at full scale — the in-band candidate count will be far larger than under the old $1/$5M floors; the pre-rank cut to 400 is designed for it but has only run against the 600-symbol fake API.

**If the user reports something broken, first ask which of these unverified surfaces it touches.** Then get the Actions log — that is the only window into live behavior.

---

## 6. IMMEDIATE OPEN ITEMS

1. **First live shift is the real test.** Watch the Actions log for per-feed newswire status and Alpaca errors.
2. **Repo hygiene:** the repo root has ~16 duplicate files (`config.py`, `flow.py`, `hub.py`, etc.) from a bad drag-and-drop upload. They are harmless — Python imports from `src/`, Actions reads `.github/workflows/` — but they should be deleted. The user found manual deletion infuriating; do not make him do it file by file.
3. **Deployment friction is the user's biggest ongoing annoyance.** He has re-uploaded the whole folder many times by hand. There is **no GitHub connector in the Anthropic directory** (checked). The only path for an assistant to push directly is a fine-grained PAT (repo-scoped, Contents + Workflows read/write) pasted into chat — offered, not yet done. If he offers a token, use it, push a clean tree (which also fixes item 2 in one commit), and tell him to revoke it after.
4. **DST:** shift cron lines +1 hour in November.
5. **Universe band (fixed 2026-07-22, item 14 in the failure log):** the scan
   ran with `min_price 1.0` / `min_dollar_volume $5M` and no ceiling, which
   structurally excluded every CPHI-class name and admitted DHR/AMG. Band is
   now `$0.10–$50` price and `$200k–$300M` dollar ADV, and candidates come
   from `full_market_candidates()` over the entire Alpaca symbol master
   instead of a hand-written seed list.
6. **Receipts tuning:** once `ignition_evals` has live data, the 30-day precision line on the hub becomes the tuning signal for thresholds in `config.yaml` (`mover_min_move`, `ext_gap_min`, `news.min_score`).

---

## 7. THE MENTAL MODEL TO KEEP

The project converged on one idea after many wrong turns:

> **Rank what a human holding a phone could actually have traded — and get to it at the moment its cause exists, not when its effect is obvious.**

Everything follows from that. Size of move is not the goal. Volume anomaly is not the goal. A correct number attached to an untradable event is still a failure. The press release timestamp is the earliest honest point of entry, and the tape's *travel* — legs, pullbacks, reclaims, still moving now — is what separates his trade from a headline.

One boundary worth keeping honest with him: nothing polling public data beats a colocated algorithm to the first print, and claiming otherwise would be a lie he'd catch. What this system can legitimately do is sit on the catalyst at t≈0 and cover every US listing down to $0.10 continuously — which is the edge the paid scanners are reselling anyway.
