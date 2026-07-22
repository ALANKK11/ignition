# IGNITION — HANDOFF BRIEFING

**Read this before touching anything.** You are inheriting a working project mid-flight from another Claude instance. This document exists so you do not repeat mistakes that took many painful iterations to find. The user has no patience left for rediscovery — everything below was learned the hard way, usually by the user catching a bad output on his phone and naming the ticker.

---

## 1. WHO THE USER IS (the most important section)

He is a retail trader who trades **high-volatility, high-risk/high-reward small caps**. Not investing. Not swing trading blue chips. He hunts the names running 50–800% intraday, usually sub-$10, often sub-$1, frequently nano-cap.

**Tickers he named as GOOD examples of what he trades:** `CPHI`, `OMH`, `SLGB` — small caps that jumped hard and fast with multiple legs.

**Tickers he named as USELESS to him** (all of which the tool surfaced at some point, to his fury): `UTZ`, `DHR`, `AMG`, `CAVA`, `QQQ`, `UTX`. These are mega/mid-caps, index funds, or one-print gaps. If your output contains names like these at the top, **you have already failed.**

**Correction (2026-07-22, from him directly): those lists are day-dependent, not permanent.** He traded `UTX` on 7/21 — the same ticker rejected earlier as ghost liquidity — because *that day* it had big swings on real volume. His words: it's "not about being sub-dollar; it's just about where the stock is." Hot is a property of today's tape, not of the ticker. Two consequences: (a) never hard-code a name as good or bad — every gate must read the current session; (b) "is X hot right now, for a name I'm already holding" is a first-class question the product must answer even when X never earned a board slot. That is what PULSE exists for (failure log 19).

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
| `splits.py` | Split-aware share-volume adjustment + ADV (item 30). Authoritative Alpaca corporate-actions ex-dates merged with a clean-ratio heuristic. |
| `watch.py` | **The watchlist lane (item 31).** Zero-gate telemetry rows for every `watchlist.txt` name, every tick; honest reason lines for missing data; `write_watchlist()` backs the workflow_dispatch tickers input. |
| `flow.py` | Intraday state machine. Time-of-day-normalized `PACE`/`TP`; states: `IGNITING`, `NEW MONEY`, `RUNNING`, `CHURN`, `FADING`, `LEAVING`, `COOLING`, `QUIET`, `OPEN DRIVE`, `PM HOT`. Hysteresis on downgrades. |
| `flow_alpaca.py` | Full-market radar, `ext_sweep()` (hardened), `movers_from_radar()`, `path_stats()`/heat, `assemble_board()`, `fast_update()` (45s lane), promotions. |
| `journal.py` | SQLite: scans, picks, flow_events, evals, ignition_evals. Rank-IC and edge-vs-control grading. `JsonlJournal` sidecar for the live looper + `ingest_jsonl_events()`. |
| `hub.py` | Renders the entire static mobile page. No external assets, no CDN. Version-gates state files. |
| `report.py` | Rich terminal rendering + CSV/JSON/TradingView export. |
| `demo.py` | Deterministic synthetic market with planted archetypes. `--demo` works fully offline — use it for regression testing. |

### Workflows (`.github/workflows/`)

| File | Schedule (ET) | Purpose |
|---|---|---|
| `live.yml` | 6:55am and 12:55pm, Mon–Fri | Two long shifts covering 7am–6:52pm. Ticks the fast lane every ~45s, full discovery every ~160s, pushes the hub each tick. `workflow_dispatch` has a **tickers** input: it overwrites `watchlist.txt`, commits it, and starts a fresh shift (canceling any running one) — this is how he edits his list from the phone. |
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
- `latest_watch.json` — **MY NAMES** (item 31): one row per watchlist ticker, his order, zero gates, honest reason lines; refreshed by every lane including the 45s fast tick. The hub renders it as the page's TOP section and writes `docs/watch.json` (tickers only) for the tape page to merge.
- `latest_pulse.json` — **PULSE**: hot-or-not coverage of every symbol with meaningful tape this session (no price class, no ADV band; ~80B/row, capped 3000 rows), overlaid with heat/state/first-seen for tracked names. Written each full tick from radar rows already in hand — zero extra API calls. The hub copies it minified to `docs/pulse.json`; the page's ticker box answers lookups client-side (`pverdict()` in hub JS: heat-based when tracked, pace/|move|/range-derived when not, `FADING/LEAVING` states surface as MONEY LEAVING). Knobs under `pulse:` in config.

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

19. **The board answered "what's hot" but not "is MY name hot."** He holds names the engine didn't pick; when one wasn't a top-N winner he had no read at all — and the good/bad ticker lists in §1 turned out to be day-dependent anyway (UTX, 7/21). Fix: PULSE — see the state-model entry. The lookup box sits at the top of the page; unknown or dead names answer COLD honestly rather than silently.

20. **The commit lane has a hard latency floor — "last 10-30 seconds" is impossible through it.** Data → Actions tick (45-160s) → commit → Pages deploy (~30-90s) → phone refresh: minutes, always. His 2026-07-22 demand ("literally in last 10-30 seconds the volume or money is drying up") cannot be met by pushing harder. Fix: **TAPE** (`src/tape.py` → `docs/tape.html`, linked from the hub header) — the phone opens a websocket DIRECTLY to Alpaca's free IEX stream and computes rolling 10s/30s/60s/5m dollar-flow per watched name on-device, re-evaluated every second. Verdict ladder with hysteresis: WARMING (first 75s) → STEADY / SURGING (~3s after a burst) → THINNING (~20s after flow fades) → MONEY LEAVING (thinning + under 1-min vwap, ~22s) → DRAINED (~27s, no prints ≥25s or flow ≤15% of own 5-min pace). Sparse names read **THIN TAPE**, never a false DRAINED — the ghost-liquidity lesson at second scale. Watchlist ≤8 names (free stream caps subscriptions), chips persist in localStorage, suggestions pulled from pulse heat, beep/vibrate on state flips. **SECURITY INVARIANT: the repo is public. Alpaca keys are pasted into the page once and live in localStorage on his phone ONLY — never committed, never in docs/, never in state. Do not ever change this.** Alpaca free tier allows ONE concurrent stream connection: the Actions shift is REST-only so they don't collide, but a second phone/tab will 406 (the page explains it).

21. **Pulse froze between full ticks.** The 45s lane refreshed the board but pulse kept its 160s timestamp. `refresh_pulse_from_board()` now splices refreshed board rows into pulse and bumps its ts on every fast tick; full-market rows keep full-tick values (refreshing 11k names at 45s would be pure API waste). Hub meta-refresh tightened 300s → 150s.

22. **His actual spec, verbatim (2026-07-22): "I utilize big price swings of more than 10% in any direction preferably green."** Two structural consequences. (a) `mover_min_move` lowered 0.15 → 0.10 in BOTH defaults and yaml — the board's admission threshold now matches his stated edge exactly. (b) **HALT RADAR** (`src/intel.py`, hub section): an LULD pause (code LUDP) fires when a Tier-2 name moves ~10% in five minutes — the free nasdaqtrader RSS is therefore a market-wide detector for precisely his instrument, covering Nasdaq- AND NYSE/AMEX-listed names in one feed. Poll ≤1/min (Nasdaq's stated limit; enforced with a state-file guard). The parser was built against a LIVE pull of the real feed (2026-07-22 13:21 GMT) which contained an undocumented reason code ("D") and items back to 2019 — hence: tolerate unknown codes, filter to today for the day view, diff successive polls into a per-day log (`halts_day_<date>.json`), track per-symbol counts. 3+ LUDP halts up renders "×N EXHAUSTION?" — after three halts up, reversal odds on resume rise sharply. T1 = news pending; H10/H11 = regulatory, do not touch.

23. **EDGE verdict — why it runs / why it dies, on every board card.** Fuses four free layers: (a) **EDGAR dilution grade** via `data.sec.gov/submissions` per board name (CIK map auto-cached from `sec.gov/files/company_tickers.json`, UA header set, 0.15s spacing under the 10 req/s cap; 6h cache): FRESH PAPER (424B*/S-1 ≤7d — shares selling NOW) / S-1 PENDING / OPEN SHELF (S-3 family ≤540d — can sell into strength) / CLEAN. A runner on FRESH PAPER is graded SMASH RISK; the nightly scan multiplies scores by dil_mult_* for its top 25. (b) **HOD-extension fade table** from ~3k-event gapper stats: ext <10% → 93% historical fade, 10–25% → 74%, 25–50% → 57%, 50–100% → 31%, >100% → 11%. Single-vendor numbers — displayed as "hist", used as priors, never silent gates. (c) **SSR flag** (low ≤ −10% vs prev close, computed in radar) and **vs_vwap** carried from flow states into board rows. (d) **float rotation** via yfinance (24h cache; micro-cap floats are stale/wrong — order-of-magnitude only, never a gate). Verdicts: SMASH RISK / FADE RISK / WATCH / GO, with the verdict-changing fact leading the reason line ("shelf open · ext +189% → 11% fade hist · above vwap · rot 6.7x"). A +207% name on an open shelf correctly reads WATCH, not GO — that is the point of the layer.

24. **Intel is fail-open.** Every external call (halt feed, EDGAR, yfinance) is try-wrapped and cached; feed down ≠ shift down; demo mode skips the EDGAR pass entirely. `latest_intel.json` is STATE_V-gated like everything else.

25. **"It has to be 1ms latency" (2026-07-22) — the premise is wrong and saying so is the help.** 1ms means colocation in the exchange datacenter on a direct feed (six figures/yr). His signal is a 10–30s *measurement window*: per-print network latency of 1ms vs 150ms moves a 30s average by <1%. The latency that actually cost him was in MY software, and was ~1000x larger: a 1s eval tick, a 75s warmup, 3s hysteresis. Fixed: eval loop 1000ms → **250ms** (canvas repaint stays 1s so phones don't melt), warmup 75s → **40s**. Added a **real feed-lag meter** in the header — Alpaca's trade messages carry an RFC3339 exchange timestamp, so `Date.now() - Date.parse(t)` is true end-to-end lag; displayed as a median of the last 200 prints (phone-clock skew makes it approximate — say so, don't hide it). Future instances: if he asks for 1ms again, show him the measured number rather than arguing.

26. **NEXT-60s flow forecast — and its backtest is the honest part.** Per symbol: dollar-flow term structure (10s/30s/60s/300s), uptick-vs-downtick **dollar** imbalance (tick-rule classified), and print-size mix → BUILDING / STEADY / DRYING. Every call is auto-graded 60s later against realized 60s flow (symmetric ±10% band), sampled at most **1 per symbol per 20s** so overlapping near-duplicate calls can't inflate the sample. Scoreboard persists in localStorage and shows all-calls + directional hit rate vs the 50% coin flip.
   **Backtest result (40 seeds × 2400s each, shipped code run under node):** on persistent-regime tape, directional calls hit **78.8%** (n=2204). On memoryless-noise control they hit **19.4%** (n=505) — i.e. NOT a phantom edge, but an *inverted* one. Interpretation: the call is a **momentum bet**; it reads trending flow and backfires under mean-reversion, and no simulation can tell us which regime his actual names are in. Therefore the scoreboard is a first-class UI element and explicitly advises **"your tape is mean-reverting: FADE the call"** below 40% directional (n≥25), "calls are carrying" above 60%, "no edge, ignore it" between. **Do not remove the scoreboard to make the feature look better — the scoreboard IS the feature.**

27. **PROVENANCE INCIDENT + the fix he actually needed (2026-07-22, mid-session).** His complaint: the tape verdict "switches green to red, green to red — it goes from one extreme to another," and he asked for (a) an anomaly read on the last 10-15s of flow and (b) the session-level "overall the money is leaving" read. Two of my own bugs caused the flicker: hysteresis counted TICKS not seconds (so the 250ms speedup quartered every confirmation window), and the verdict compared 30s flow to a TRAILING 5-min baseline, which sinks along with a bleeding name and reads STEADY forever (empirically shown: 0.48 ratio after flow fell 93%).
   **Between my turns, `src/tape.py` in the working tree changed in ways this instance did not make**: the core had been swapped for a TREND/BURST engine (anchored-to-peak trend + robust 12s burst z-score) with the UI left unwired — calling deleted functions, would have crashed a browser. Origin unknown (plausibly a parallel session on a shared workspace; possibly tooling). Response taken, in order: (1) diffed the tree against the last zip actually shipped to him (`/mnt/user-data/outputs/*.zip` is the provenance anchor); (2) **security-audited every network sink and key touch** — clean: keys go to localStorage and the Alpaca auth frame only; (3) adopted the engine on merit, finished the UI wiring myself, and put it through the full harness, which caught **two real bugs in the adopted code**: burst baselines counted not-yet-existing history buckets as zero-flow (early-session z in the thousands), and one whale print permanently poisoned the peak anchor (fix: `peakCand()` = f60 minus the largest single print, shared by page and tests).
   **RULE FOR EVERY FUTURE INSTANCE: at session start, diff the working tree against the newest shipped zip in outputs. Any drift you didn't make gets a security audit before it ships — this page holds his API keys.**

28. **The tape's two reads, final semantics (all node-verified):** TREND = f60 vs session peak flow (whale-robust), sticky 3s-to-degrade / 10s-to-recover: FLOW ALIVE ≥55% of peak, COOLING ≥25%, MONEY LEAVING ≥10%, MONEY GONE <10%, DRIED UP at 25s of silence. Zero flicker across 25 noisy-healthy seeds; slow bleed called at COOLING@+3min, MONEY LEAVING@+6min. BURST = last 12s vs the name's own observed 12s buckets (median+MAD, phantom-empty buckets excluded, ≥2.5 min judged tape required), BUY/SELL by tick rule, rendered as an event with an age, alerts at z≥4, 0-0.25s detection latency in replay. **The NEXT-60s forecast + scoreboard were retired** — regime-dependent (item 26), contributed to the flicker complaint, and BURST answers the "something is happening NOW" need without pretending to know the future.

29. **The tape's engine chips lied after load — pullEngine (recreated 2026-07-22).** tape.html fetched `pulse.json` ONCE at page load; the engine chips and suggestions then showed that snapshot forever, with no indication the engine had moved on, stalled, or died. Fix: `pullEngine()` polls `pulse.json` + `watch.json` every 45s (the engine's own fast cadence) with EXPLICIT states in the header — "engine: 2m ago" (fresh), "engine STALE Xm — check Actions" (orange, >6m), "engine feed unreachable" (red, fetch failed). Never silent, never a stale read presented as live. *Note: this fix and item 30 originally shipped in `ignition_splits.zip`, which never got uploaded — both were recreated from spec by the next instance and pushed directly.*

30. **Split-aware ADV everywhere a volume baseline is computed (recreated 2026-07-22).** A reverse split rescales the share unit mid-window: after a 1:10, a raw 20-day mean mixes pre- and post-split share counts and overstates ADV ~10x, so pace/rvol read ~0.1x reality and the name goes structurally invisible — INLF, exactly the class he trades. Dollar volume is split-invariant but pace, rvol, and the radar all run on SHARES. Fix: `src/splits.py` — `adjusted_volumes()` converts every pre-split bar into today's share unit; `split_aware_adv()` is the drop-in mean. Two layers merged per name: authoritative ex-dates from Alpaca corporate actions (`AlpacaData.splits_range`, one call/day, fail-open) and a heuristic (close-over-close jump within 2% of a clean split ratio WITHOUT elevated volume — a real CPHI-class double prints on 10-50x tape, guard is rvol < 3x, so real moves are never eaten). Wired into `prepare()` (radar/flow baselines), `full_market_candidates()` (scan funnel), and `compute_base_metrics()` (scan metrics). Fixture-proven on INLF-shaped reverse, forward 4:1, real-double-kept, and the authoritative-date path (`tests/test_pivot.py`).

31. **THE PIVOT — watchlist-first (2026-07-22, his decision, his words: discovery is "bullshit — I'm still better… not something that finds the things, but where I already put the tickers in, and then it helps me do what I do").**
   **Cause:** discovery kept losing to his own judgment; what he actually wanted graded was the names he already chose.
   **Decision:** `watchlist.txt` is the spine. His names get first-class treatment in every lane, every tick, with ZERO admission gates — no ADV baseline, no mover threshold, no pulse dollar floor, no price band. Discovery stays (it caught LABT +208% legitimately) but is demoted below.
   **Mechanism:**
   - `src/watch.py` — the watch lane. `refresh()` runs on every full tick, every ext sweep, and the 45s fast lane: 2 API calls (snapshots + 1-min bars from 4am) for the whole list. `build_rows()` emits one row per ticker in HIS input order, always; missing data degrades to an honest `reason` string ("no IEX prints yet today", "thin IEX tape: $2.1k today", "no prior close on file"), never to a missing row. Prev close falls back to the snapshot's `prevDailyBar` so a fresh-split/new-listing name (no engine baseline) still gets day% — the INLF class can't be hidden because nothing gates it.
   - State: `latest_watch.json` (STATE_V-gated). Hub renders **MY NAMES** as the TOP section — one rich card per name: last, day%, heat meter, state, vwap side, off-high, swings/travel, $tape, x ADV, SSR, halt count + resume, dilution grade chip, EDGE verdict with reason line, PR headline if the board has one. Empty list renders a how-to card, never nothing.
   - Phone editing: `live.yml` `workflow_dispatch` gained a `tickers` input → `src.watch.write_watchlist()` overwrites `watchlist.txt`, commits, pushes, then the shift starts. Dispatching mid-shift is safe and useful: concurrency cancels the old shift and the new one reads the new list. A direct GitHub edit of watchlist.txt also reaches a RUNNING shift within a tick, because `_git_push_state` rebases against origin every push.
   - Intel: `refresh_intel(priority=watchlist)` — his names consume the EDGAR/float budget before board names. Pulse: watchlist rows are always present (zero gates) so the lookup box always answers. Scan: watchlist already bypassed `base_metrics_ok` and always enriched — unchanged.
   - TAPE: hub writes `docs/watch.json` (tickers only); `pullEngine()` merges it into the chip list each poll — capped at 8 (the stream limit), names removed on the phone stay removed (`tape_wx` in localStorage) until re-added, and the add-box still works with zero network.
   **Proven** (`tests/test_pivot.py`, `tests/test_tape.js`, full `--demo` regression): the $2k-tape/no-baseline/no-history card renders complete with the honest reason; dispatch input round-trips input→watchlist.txt→docs/watch.json; MY NAMES renders above the discovery board; mergeWatch order/cap/removed-set; page script parses; TREND/BURST core invariants (item 28) re-verified untouched; tape network sinks audited — still exactly three (pulse.json, watch.json, Alpaca wss), keys still localStorage-only.
   **Unproven:** the watch lane against live Alpaca snapshots/bars at real scale; the dispatch→commit→shift sequence on a real Actions runner; whether 2 extra calls per 45s tick matter to the rate budget (they shouldn't: ~25/tick full, budget 200/min).

32. **Editing from the page + the current-moment read (2026-07-22, same day as the pivot — his immediate feedback on it).** Three complaints, verbatim-adjacent: (a) "I don't wanna have to go to fucking repo actions and add the tickers… I wanna add it from my website"; (b) a name "moving sideways for the last two hours" was still showing on the hot screen — "what's the point?"; (c) he wants the read "in the current moment… relative to a base… overall mood, so it's not green and then red, green and then red."
   **(a) On-page editing.** The hub's MY NAMES section has an add box and per-card ×. Edits save to localStorage and render INSTANTLY from the pulse feed (client-side, zero auth). To move the engine's list, the page writes `watchlist.txt` through the GitHub Contents API using a **fine-grained PAT he pastes once** (repo-scoped, Contents read/write) — `api.github.com` supports CORS from browsers, so no relay server is needed and the $0 constraint holds. **SECURITY INVARIANT (extends item 20's): the token lives in localStorage ONLY, is sent ONLY to api.github.com, and never appears in the repo.** `tests/test_hub_js.js` audits the hub script's outbound hosts on every run. Sync states are explicit (syncing / updated ✓ / token rejected / retrying) — never silent. Sha conflicts with the shift's own commits retry once; the running shift picks the new file up within a tick because `_git_push_state` rebases every push. The Actions tickers box still works as a fallback.
   **(b) Stale names sink.** `path_stats` heat's dead-recency floor dropped 0.25 → 0.10 — a name with zero travel in 30 min now caps at heat 10 regardless of its morning run (old calibration in item 11 shifts accordingly: the died-at-11:30 runner reads ~10, not 26; UTZ still 0, live CPHI still ~99). The board's HOT NOW chip is additionally gated on ≥1.5% travel in the last 30 min — heavy tape on a stopped price is churn, not heat.
   **(c) NOW + MOOD.** `now_stats()` (flow_alpaca): whale-robust dollar flow of the last 15 minutes vs the session's own **peak** 15-minute window — anchored, never a trailing average (the item-27 lesson at minute scale: trailing baselines sink with a bleeding name and read healthy forever). Also travel/15m and `stalled_min` (minutes since a 5-min window traveled ≥1.5%). Cards show it as one sentence: "now: $412k/15m · 3.1% travel/15m · 64% of its peak 15m" or "sideways 1h50m — nothing happening now". **MOOD** is the sticky layer: candidates MONEY HERE (≥55% of peak) / COOLING (≥30%) / MONEY LEAVING (≥12%) / DEAD / STALLED / WARMING, and `sticky_mood()` requires 2 consecutive ticks to degrade, 3 to recover (state in `watch_mood_<date>.json`) — alternating borderline input provably cannot flip the label. This is TAPE's TREND design (item 28) applied to the minute-scale lane; prior art check: it's also how commercial scanners frame "happening now" (Trade-Ideas' 5-minute relative volume vs the stock's own typical 5 minutes) — ours anchors to session peak instead of a historical norm because his names often HAVE no meaningful history that day.
   **Freshness:** hub JS polls `watch.json` + `pulse.json` every 45s and re-renders MY NAMES in place — the phone shows each Pages deploy without a reload; the commit-lane floor (item 20) still applies, and second-scale remains TAPE's job.
   **Proven:** 43 fixture checks (`tests/test_pivot.py`: sideways-2h name reads r15<0.12 / stalled≥90m / heat≤12 while its mid-run self reads MONEY HERE; flicker sequence never flips; watch.json carries card-ready rows) + hub/tape node tests + full `--demo`. **Unproven:** the Contents-API sync from a real phone browser (CORS + fine-grained-PAT path is per GitHub docs but untested from his device), and mood band calibration against his real names — the bands are knob-less constants in `mood_candidate()` for now; if he says a label is wrong, tune there.

33. **The card speaks English + the LIVE STRIP (2026-07-22, from his INLF screenshot).** He sent a real MY NAMES card and said, in order: "what does this mean?", "it's not updating", and described the read he actually wants — below vwap, no volume in the last ~50 seconds compared to the last jump 10 minutes ago, "looks like it's on the downshift for the last thirty seconds", plus "I can try to predict the next thirty… press here to compute."
   **(a) Story line.** `_story()` (hub.py) writes the card's lead read as sentences a person would say: "+64% today, 27% below its high, lost the vwap. The money is walking — $8K/15m on the tape vs $57K at its peak (14%), while the chart still shows green — and it's still whipping (10 legs today)." Composed from fields already on the row; fixture-tested against his literal screenshot numbers. The fragment meta row stays, dimmed, below it.
   **(b) LIVE STRIP.** The "last 30 seconds" read cannot come through the commit lane (item 20). Fix: the hub page itself opens the Alpaca IEX stream **reusing the keys TAPE already saved** — localStorage is shared across pages on the same origin, so there is no new secret and nothing new leaves the phone (the sink audit in `tests/test_hub_js.js` now allows exactly two outbound hosts: api.github.com and stream.data.alpaca.markets, and asserts the hub never WRITES the key fields). Per card, every second: dollars in the last 30s vs the name's own best 30s burst of the last 10 minutes → FLOW / MID / DRY / NO PRINTS, with an explicit "DOWNSHIFT" tag when the last 30s is under half the prior 30s. States are sticky (4s to degrade, 8s to recover — `lvSticky`), node-tested for zero flicker on alternating borderline input and for his exact INLF shape (burst 8 min ago, trickle since → "drying up · DOWNSHIFT"). One-connection reality: if TAPE is open, the strip says "TAPE is using the stream" instead of silently failing; no keys → it says where to save them; market closed → says that.
   **(c) "Predict the next 30s" — deliberately NOT rebuilt.** Item 26/28 history: the NEXT-60s forecast was built, backtested (78.8% directional on trending tape, 19.4% — i.e. actively inverted — on mean-reverting tape), and retired because no one can tell which regime his name is in from inside the tool. He was told this plainly. If he insists, revive it ONLY with the scoreboard (the scoreboard IS the feature — item 26); do not ship a bare prediction button.
   **Unproven:** the live strip against the real stream from his phone (same caveat as TAPE item 20 originally had — the protocol is implemented to spec, first open is the test), and hub+TAPE stream contention behavior in practice.

34. **"Who cares what it's like today" — MY NAMES leads with the live per-second read, ranked (2026-07-22, his reaction to item 33's cards).** He trades every second, hundreds of times a day; a card headlining "+62% today" is showing him the one number he doesn't use. Fix: the card headline is now the LIVE verdict — **MONEY IN / EASING / DRAINING ↓ / STALLED** — painted every second from the on-device Alpaca stream (`liveHead`), with MOOD as the fallback until the first print lands. The day%, heat, and travel all dropped to a small footnote row. And MY NAMES **reorders every second** (`liveScore`: sticky-state first — i.e. relative to each name's own 30s burst — dollar flow breaks ties) so whichever name has money rushing in *right now* sits on top; that's his "relative to the others." Sticky states (item 33's `lvSticky`, 4s/8s) keep the ORDER from churning too — the DOM only reshuffles when the ranked sequence actually changes. `wc_<t>` / `lvh_<t>` / `lv_<t>` ids let the per-second painter fill both the server-rendered card and JS-rendered cards identically. **This makes explicit what was always true (item 20): the GitHub lane is context, the phone's direct stream is the only thing fast enough for how he trades — so the stream read is now the card, and the 45s engine data is the footnote.** Same security envelope as item 33 (keys reused from TAPE, localStorage-only, api.github.com + alpaca stream the only sinks; asserted in `tests/test_hub_js.js`). Unproven: same as 33 — the live paint + per-second reorder against the real stream on his phone; the fixtures cover the pure functions (`liveHead`/`liveScore`/ranking) and the card structure, not a live socket.

35. **One ⚙ keys panel + SIP-first feed auto-select (2026-07-22).** His asks: "fetch data the fastest way so we are most accurate" and "put any key I need there on the phone on the website itself once and forget" — he also suggested the page just use the repo secrets since they're already there.
   **Why the page can't read repo secrets — told to him, keep telling future users:** the repo is public; GitHub only decrypts secrets for Actions runners. Any mechanism that let the page read them would let every visitor read them. So the phone holds its own copy of the same values, entered once.
   **The panel:** hub's ⚙ keys button opens ONE card for everything — Alpaca key + secret, GitHub token, and the feed selector. Saves into the SAME localStorage slots TAPE uses (`tape_k`/`tape_s`), so entering keys on either page covers both; saved fields show "saved ✓ (paste to replace)" placeholders; the panel always reopens (the item-33 lockout class can't recur). The sink audit now asserts exactly two key writes (the panel inputs) and the same two outbound hosts.
   **The accuracy lever:** Alpaca's **SIP** feed is the full consolidated tape — every US exchange, every print — vs IEX's ~3% sample; same websocket protocol, just `/v2/sip` vs `/v2/iex`. Auto mode (default) probes SIP with his keys and on an entitlement rejection (code 402/409 or "insufficient subscription") caches `feed_sip=0` and reconnects to IEX — one reconnect ever; re-probed when keys change or the pref is cycled (auto/sip/iex button). Both hub strip and TAPE share the flag via localStorage and show the active feed explicitly ("live ● SIP full tape" / "IEX sample (~3%) — SIP needs a paid Alpaca plan"). If he upgrades Alpaca, the pages switch themselves. Note: SIP on Alpaca is a paid add-on — do NOT present it as free; the free-tier truth stays IEX, said on-page, honestly.
   **Latency truth (recorded so nobody re-litigates):** the stream is push — prints arrive the moment Alpaca relays them; the measured end-to-end lag is on TAPE's header (item 25). There is no faster free path than the direct websocket already in use; SIP changes COVERAGE (accuracy), not latency. The engine/commit lane stays what it is: context.
   **Unproven:** SIP entitlement behavior against his real account (probe → fallback is per Alpaca's documented error codes but untested live); the panel UX on his phone browser.

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
- Halt RSS parser proven against a LIVE pull of the real feed (not a synthetic fixture): field extraction, self-closing empties → None, undocumented code "D", day filtering, poll-diff dedupe, ×3 exhaustion counting. EDGAR grading windows, ext→fade math, and the full verdict truth table are fixture-proven; the hub renders all three verdict paths + halt radar + SSR end-to-end.
- Forecast backtested under node against 40 seeded replays of two tape regimes with a noise control, exactly as shipped; the negative control result drove the inversion advisory. Latency changes verified not to break the verdict ladder (full scenario suite re-run green).
- TAPE core (`tapeStats`/`tapeVerdict`) executed under node against simulated streams, timeline measured: tape stops → THINNING +20s, DRAINED +27s; burst → SURGING +3s; fade-under-vwap → MONEY LEAVING +22s; 1-print-per-50s name → THIN TAPE with zero false alarms; noisy-steady flow → zero verdict flicker over 300s; <75s of tape → WARMING. The 45s pulse splice (board overlay, ts bump, insert, radar-field preservation) is fixture-tested.
- PULSE: assembly fixture (his four 7/21 tickers + ghost-liquidity exclusion + board overlay + version gate), and the *shipped* verdict JS executed under node against 11 shapes — CPHI/OMH/UTX/SLGB land HOT/HOT/HOT/WARM, flat SBRA lands COLD, churn and deal-pin and FADING→MONEY LEAVING all correct.
- Split-aware ADV (item 30) and the whole watchlist-first lane (item 31): `tests/test_pivot.py` (29 checks) + `tests/test_tape.js` (page parse, mergeWatch, TREND/BURST core invariants) — run both after touching any of it.

### NEVER verified against live services

The build sandbox has no network access to Yahoo, Alpaca, Finnhub, or the newswires. Therefore these paths have only ever run against fixtures:

- Real Alpaca API responses (auth, rate limits, actual field shapes at scale).
- Real Finnhub responses.
- **The RSS feed URLs.** These are the newest and least certain component. Wires move their endpoints. The shift log prints per-feed HTTP status; dead feeds are skipped and named; the list is plain config (`news.feeds` in `config.yaml`) so a moved endpoint is a one-line edit.
- GitHub Actions runtime behavior: whether a 6-hour shift survives without being killed, whether `git pull --rebase` contention between the live shift and the evening job causes push failures under real load.
- The ignition receipts hit-rate. The grading machinery works; there is no live data in it yet.
- Whether his real tape trends or mean-reverts at the 60s horizon — the single open question that decides whether NEXT is worth reading straight, inverted, or not at all. His own scoreboard answers it within a session; do not pre-judge it.
- EDGAR endpoints from a live Actions runner (UA acceptance, rate behavior, submissions shape drift) — the parser matches the documented columnar shape but has not been exercised live from this environment. Same for yfinance float freshness on his actual names, and the halt feed's behavior during a fast multi-LUDP day (the live pull happened before the day's first LUDP).
- EDGE verdict calibration against his real fills — the fade table is a prior from someone else's dataset until his own journal grades it.
- TAPE against the real Alpaca websocket: the v2 stream protocol (connect/auth/subscribe handshake, `t` trade messages, 406 on second connection) is implemented to Alpaca's documented spec but has NEVER been exercised live from a browser — no network path to Alpaca from the build environment. First open during tape is the test. There is no REST fallback; if the stream auth fails the page says so and the hub board remains the read.
- PULSE against live radar output at full-market scale, and its commit-history cost: worst case ~230KB per full tick (~160s) while the market is busy — watch repo growth over the first weeks; the cap/floors under `pulse:` are the dial, and a periodic history squash is the escape hatch.
- The widened band ($0.10 floor, $200k dollar-ADV floor) against the real Alpaca master at full scale — the in-band candidate count will be far larger than under the old $1/$5M floors; the pre-rank cut to 400 is designed for it but has only run against the 600-symbol fake API.

**If the user reports something broken, first ask which of these unverified surfaces it touches.** Then get the Actions log — that is the only window into live behavior.

---

## 6. IMMEDIATE OPEN ITEMS

1. **First live shift is the real test.** Watch the Actions log for per-feed newswire status and Alpaca errors.
2. **Repo hygiene: DONE (2026-07-22).** The ~16 stale root duplicates from the drag-upload era are deleted; imports verified. `.gitignore` now covers `__pycache__`.
3. **Deployment friction: SOLVED (2026-07-22).** Sessions now have direct push access to the repo. Rules that come with it: never push to main while a live shift is mid-run without handling the contention (the shift commits `live-tick` to main every tick — merge, then cancel-and-redispatch `live shift` so it picks the code up), prefer deploying before 6:55am / after 7pm ET, and never commit demo-rendered `docs/` — the shift regenerates docs from real state within a tick.
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
