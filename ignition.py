#!/usr/bin/env python3
"""IGNITION — pre-session volume & volatility scanner.

    python ignition.py scan               # evening scout (run ~6-9pm ET)
    python ignition.py scan --premarket   # morning confirm (run ~7-9am ET)
    python ignition.py scan --demo        # offline synthetic market
    python ignition.py eval               # grade past scans vs what happened
    python ignition.py history            # rolling IC / edge across scans
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console

from pathlib import Path
from src import flow as flow_mod
from src import report
from src.config import load_config, read_ticker_file
from src.journal import Journal
from src.report import (export_csv, export_json, export_tradingview,
                        render_eval, render_history, render_scan)
from src.scoring import capacity_mult, composite_score, make_tags, top_drivers
from src.signals import (afterhours_component, base_components, base_metrics_ok,
                         catalyst_component, compute_base_metrics,
                         options_heat_component, short_fuel_component)
from src.universe import build_universe
from src.util import NY, next_trading_day, ny_now, ny_today

console = Console()


def get_provider(demo: bool, include_next: bool = False):
    if demo:
        from src.demo import DemoProvider
        return DemoProvider(include_next=include_next)
    from src.providers import LiveProvider
    return LiveProvider()


# ---------------------------------------------------------------------------


def enrich_one(provider, ticker, metrics, th, mode):
    """Second-pass signals for one ticker. Exception-safe."""
    comp, extra = {}, {}
    try:
        intra = provider.get_intraday_1m(ticker)
        comp["afterhours"], ah_info = afterhours_component(intra, metrics, th, mode)
        extra.update(ah_info)
    except Exception:
        comp["afterhours"] = None
    try:
        ed = provider.get_next_earnings(ticker)
        comp["catalyst"], cat_info = catalyst_component(ed, metrics["last_bar_date"], mode)
        extra.update(cat_info)
    except Exception:
        comp["catalyst"] = None
    try:
        ss = provider.get_short_stats(ticker)
        comp["short_fuel"], s_info = short_fuel_component(ss, metrics, th)
        extra.update(s_info)
    except Exception:
        comp["short_fuel"] = None
    try:
        iv = provider.get_atm_iv(ticker, metrics["close"])
        comp["options_heat"], o_info = options_heat_component(iv, metrics)
        extra.update(o_info)
    except Exception:
        comp["options_heat"] = None
    if provider.name == "live":
        time.sleep(0.05)
    return ticker, comp, extra



def _journal(cfg, no_journal: bool):
    if no_journal:
        return None
    if os.environ.get("IGNITION_JSONL"):
        from src.journal import JsonlJournal
        return JsonlJournal(cfg["_paths"]["journal"], _state_dir(cfg))
    return Journal(cfg["_paths"]["journal"])


def _state_dir(cfg) -> str:
    d = os.path.join(cfg["_paths"]["data"], "state")
    os.makedirs(d, exist_ok=True)
    return d


def _dump_scan_state(cfg, meta, show, mode):
    ext_key = "pm_ret" if mode == "premarket" else "ah_ret"
    rows = []
    for i, r in enumerate(show, 1):
        m, e = r["metrics"], r["extra"]
        rows.append({
            "rank": i, "ticker": r["ticker"], "score": round(r["score"], 1),
            "close": round(m["close"], 2), "day_pct": round(m["ret1"], 4),
            "ext_pct": (round(e.get(ext_key, e.get("ah_ret")), 4)
                        if e.get(ext_key, e.get("ah_ret")) is not None else None),
            "rvol": round(m["rvol"], 2), "dollar_adv": m["dollar_adv"],
            "tags": [[t, c] for t, c in r["tags"]], "drivers": r["drivers"],
        })
    payload = {"ts": meta["generated_at"], "trade_date": str(meta["trade_date"]),
               "target_date": str(meta["target_date"]), "mode": mode,
               "provider": meta["provider"], "universe": meta["universe_size"],
               "scored": meta["scored"], "ext_label": "PM" if mode == "premarket" else "AH",
               "rows": rows}
    with open(os.path.join(_state_dir(cfg), "latest_scan.json"), "w") as f:
        json.dump(payload, f)


def _dump_flow_state(cfg, now, rows, ri, ro, provider, window):
    payload = {"ts": now.isoformat(timespec="seconds"), "provider": provider,
               "window_min": window,
               "rows": [{k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in r.items()
                         if k in ("ticker", "last", "day_pct", "vs_vwap", "pace",
                                  "tp", "dollar_w", "dshare", "surge", "state",
                                  "note", "peak_tp")} for r in rows],
               "rot_in": [{"ticker": r["ticker"], "surge": round(r["surge"], 2)}
                          for r in ri],
               "rot_out": [{"ticker": r["ticker"],
                            "frac": round(r["tp"] / max(r["peak_tp"], .1), 3)}
                           for r in ro]}
    with open(os.path.join(_state_dir(cfg), "latest_flow.json"), "w") as f:
        json.dump(payload, f)


def _attach_finnhub(provider, console, demo):
    if demo:
        return
    from src.providers_finnhub import earnings_map, key
    k = key()
    if not k:
        return
    m = earnings_map(k, ny_today(), 6)
    if m is not None:
        provider.earnings_map = m
        console.print(f"[dim]finnhub earnings calendar: {len(m)} symbols in window[/dim]")
    else:
        console.print("[dim]finnhub unavailable — using yahoo per-ticker earnings[/dim]")


def cmd_scan(args):
    cfg = load_config(args.config)
    th = cfg["thresholds"]
    weights = dict(cfg["weights"])
    provider = get_provider(args.demo)
    _attach_finnhub(provider, console, args.demo)

    with console.status("[bold]building candidate universe…"):
        universe, watchlist = build_universe(cfg, provider)
    if not args.demo and cfg["universe"].get("full_market"):
        try:
            from src.flow_alpaca import full_market_candidates
            from src.providers_alpaca import AlpacaData, creds as _ac
            _c = _ac()
            if _c:
                fm = full_market_candidates(
                    AlpacaData(*_c), cfg["universe"],
                    lambda m: console.print(f"[dim]{m}[/dim]"),
                    cap=int(cfg["universe"].get("max_universe", 400)))
                if fm:
                    universe = list(dict.fromkeys(list(watchlist) + fm))
        except Exception as e:
            console.print(f"[yellow]full-market sourcing failed ({e}) — "
                          f"falling back to seed universe[/yellow]")
    if not args.demo:
        from src.flow_alpaca import load_ignitions
        ign = load_ignitions(_state_dir(cfg), ny_today())
        if ign:
            universe = list(dict.fromkeys(ign + universe))
            watchlist = set(watchlist) | set(ign)   # bypass filters, always enrich
            console.print(f"[dim]ext-hours ignitions injected into universe: "
                          f"{', '.join(ign[:12])}{' …' if len(ign) > 12 else ''}[/dim]")
    if not universe:
        console.print("[bold red]No candidates — check universe_seed.txt / network.")
        sys.exit(1)
    console.print(f"[dim]universe candidates:[/dim] [bold]{len(universe)}[/bold]")

    with console.status(f"[bold]downloading daily history for {len(universe)} names…"):
        hist = provider.get_history(universe, days=cfg["scan"]["history_days"])
    if not hist:
        console.print("[bold red]No market data returned. If you're offline, try --demo.")
        sys.exit(1)
    console.print(f"[dim]history loaded:[/dim] [bold]{len(hist)}[/bold] tickers")

    # ---- pass 1: base metrics + filters + base score ----------------------
    ucfg = cfg["universe"]
    candidates = {}
    for t, df in hist.items():
        m = compute_base_metrics(df)
        if m is None:
            continue
        if not base_metrics_ok(m, min_price=ucfg["min_price"],
                               min_dollar_volume=ucfg["min_dollar_volume"],
                               is_watchlist=(t in watchlist),
                               max_price=cfg["universe"].get("max_price"),
                               max_dollar_adv=cfg["universe"].get("max_dollar_adv")):
            continue
        comps = base_components(m, th)
        score, contrib = composite_score(comps, weights)
        score *= capacity_mult(m.get("dollar_adv"),
                               full_below=cfg["scan"]["capacity_full_below"],
                               ceiling=float(ucfg.get("max_dollar_adv") or 3e8),
                               floor=cfg["scan"]["capacity_floor"])
        candidates[t] = {"ticker": t, "metrics": m, "components": comps,
                         "score": score, "contrib": contrib, "extra": {}}
    if not candidates:
        console.print("[bold red]Nothing passed the liquidity filters.")
        sys.exit(1)

    last_dates = [c["metrics"]["last_bar_date"] for c in candidates.values()]
    trade_date = max(set(last_dates), key=last_dates.count)
    if args.demo:
        mode = "premarket" if args.premarket else "evening"
    else:
        mode = "premarket" if (args.premarket or trade_date < ny_today()) else "evening"
    target_date = trade_date if mode == "premarket" and trade_date >= ny_today() \
        else next_trading_day(trade_date)

    ranked = sorted(candidates.values(), key=lambda r: r["score"], reverse=True)

    # ---- pass 2: enrichment on the funnel top ----------------------------
    n_enrich = 0
    if not args.fast:
        enrich_n = args.enrich or cfg["scan"]["enrich_top"]
        targets = [r["ticker"] for r in ranked[:enrich_n]]
        for w in watchlist:            # your own list always gets the full pass
            if w in candidates and w not in targets:
                targets.append(w)
        with console.status(f"[bold]enrichment pass on {len(targets)} names "
                            "(after-hours tape · earnings · shorts · options)…"):
            workers = 1 if args.demo else cfg["scan"]["workers"]
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(enrich_one, provider, t, candidates[t]["metrics"],
                                  th, mode) for t in targets]
                for fut in as_completed(futs):
                    try:
                        t, comp, extra = fut.result()
                    except Exception:
                        continue
                    candidates[t]["components"].update(comp)
                    candidates[t]["extra"].update(extra)
                    n_enrich += 1
        for t in targets:
            r = candidates[t]
            r["score"], r["contrib"] = composite_score(r["components"], weights)
            r["score"] *= capacity_mult(r["metrics"].get("dollar_adv"),
                                        full_below=cfg["scan"]["capacity_full_below"],
                                        ceiling=float(ucfg.get("max_dollar_adv") or 3e8),
                                        floor=cfg["scan"]["capacity_floor"])

    from src.signals import is_deal_pin
    for r in candidates.values():
        if is_deal_pin(r["metrics"]):
            r["metrics"]["_pin"] = True
            r["score"] *= 0.30      # correct data, dead setup — bury it
    if not args.demo and cfg["scan"].get("dilution_check"):
        # a forecast pick with an offering printing is a trap, not a setup
        try:
            from src import intel as _intel
            _dc, _sd = {}, cfg["_paths"]["state"]
            _now = ny_now()
            _mult = {"FRESH PAPER": cfg["scan"]["dil_mult_fresh"],
                     "S-1 PENDING": cfg["scan"]["dil_mult_s1"],
                     "OPEN SHELF": cfg["scan"]["dil_mult_shelf"]}
            for r in sorted(candidates.values(), key=lambda x: x["score"],
                            reverse=True)[:25]:
                d = _intel.dilution(r["ticker"], _sd, _now, _dc)
                m = _mult.get(d["grade"])
                if m:
                    r["score"] *= m
                    r["metrics"]["_dil"] = f'{d["grade"]}: {d["why"]}'
        except Exception as e:
            console.print(f"[yellow]dilution check skipped: {e}[/yellow]")
    ranked = sorted(candidates.values(), key=lambda r: r["score"], reverse=True)
    for r in ranked:
        r["drivers"] = top_drivers(r["contrib"])
        r["tags"] = make_tags(r["components"], r["metrics"], r["extra"], mode)

    show = ranked[: args.top or cfg["scan"]["show_top"]]
    meta = {
        "trade_date": trade_date, "target_date": target_date, "mode": mode,
        "universe_size": len(universe), "scored": len(candidates),
        "enriched": n_enrich, "provider": provider.name,
        "generated_at": ny_now().isoformat(timespec="seconds"),
    }
    render_scan(console, show, meta)
    _dump_scan_state(cfg, meta, show, mode)

    # ---- journal: picks + random control sample --------------------------
    if not args.no_journal:
        jr = Journal(cfg["_paths"]["journal"])
        n_pick = 60 if len(ranked) >= 120 else max(10, len(ranked) // 3)
        pick_rows = [{
            "ticker": r["ticker"], "rank": i + 1, "score": r["score"],
            "close": r["metrics"]["close"], "adv20": r["metrics"]["adv20"],
            "atr20_pct": r["metrics"]["atr20_pct"],
            "components": {k: v for k, v in r["components"].items() if v is not None},
        } for i, r in enumerate(ranked[:n_pick])]
        pool = ranked[n_pick:]
        rng = random.Random(str(trade_date))
        ctrl = rng.sample(pool, min(cfg["scan"]["control_sample"], len(pool))) if pool else []
        ctrl_rows = [{"ticker": r["ticker"], "rank": None, "score": r["score"],
                      "close": r["metrics"]["close"], "adv20": r["metrics"]["adv20"],
                      "atr20_pct": r["metrics"]["atr20_pct"], "components": {}}
                     for r in ctrl]
        jr.record_scan(trade_date, mode, args.demo, len(universe), pick_rows, ctrl_rows)
        console.print(f"[dim]journaled {len(pick_rows)} picks + {len(ctrl_rows)} controls "
                      f"→ run [bold]eval[/bold] after the next close to grade this scan.[/dim]")

    # ---- exports ---------------------------------------------------------
    if args.csv:
        export_csv(args.csv, show)
        console.print(f"[dim]csv → {args.csv}[/dim]")
    if args.json:
        export_json(args.json, show, meta)
        console.print(f"[dim]json → {args.json}[/dim]")
    if args.tv:
        export_tradingview(args.tv, show)
        console.print(f"[dim]tradingview watchlist → {args.tv}[/dim]")


def _grade_ignitions(jr, provider, console):
    """Receipts for the ext sweeps: did each flagged ignition actually trade
    like one in its regular session? (Yahoo consolidated volume = the truth.)
    A name with no bar that day — halted, delisted, ghost — counts as a miss:
    we flagged something that wasn't tradeable."""
    pend = jr.pending_ignitions(False, ny_today().isoformat())
    if not pend:
        return
    hist = provider.get_history(sorted({t for _, t, _ in pend}), days=60)
    hits = 0
    for day, tk, state in pend:
        rvol = dollar = None
        hit = 0
        df = hist.get(tk)
        try:
            if df is not None:
                d = dt.date.fromisoformat(day)
                pos = [i for i, x in enumerate(df.index.date) if x == d]
                if pos:
                    i = pos[0]
                    base = float(df["Volume"].iloc[max(0, i - 20):i].mean())
                    v = float(df["Volume"].iloc[i])
                    rvol = v / base if base else None
                    dollar = v * float(df["Close"].iloc[i])
                    hit = int((rvol or 0) >= 2.0 or (dollar or 0) >= 1e6)
        except Exception:
            pass
        hits += hit
        jr.record_ignition_eval(day, tk, state, hit, rvol, dollar)
    h, n = jr.ignition_stats()
    console.print(f"[bold]ignition receipts:[/bold] {hits}/{len(pend)} graded hits "
                  f"today · 30-day precision {h}/{n}"
                  f" ({h / n:.0%})" if n else "")


def cmd_eval(args):
    cfg = load_config(args.config)
    jr = Journal(cfg["_paths"]["journal"])
    pending = jr.pending_scans(demo=args.demo)
    provider = get_provider(args.demo, include_next=True)
    if not args.demo:
        try:
            from src.journal import ingest_jsonl_events
            n = ingest_jsonl_events(jr, _state_dir(cfg))
            if n:
                console.print(f"[dim]ingested {n} live flow events[/dim]")
            _grade_ignitions(jr, provider, console)
        except Exception as e:
            console.print(f"[dim]ignition grading skipped: {e}[/dim]")
    if not pending:
        console.print("[dim]No un-graded scans. Run a scan first.[/dim]")
        return
    graded = 0
    for scan in pending:
        picks = jr.scan_picks(scan["id"])
        tickers = picks["ticker"].tolist()
        with console.status(f"[bold]grading scan of {scan['trade_date']} "
                            f"({len(tickers)} names)…"):
            hist = provider.get_history(tickers, days=60)
            grade = jr.evaluate_scan(scan["id"], scan["trade_date"], hist)
        if grade is None:
            console.print(f"[dim]scan {scan['trade_date']}: next session not complete "
                          "yet (or no data) — skipping.[/dim]")
            continue
        render_eval(console, scan, grade)
        graded += 1
    if not graded:
        console.print("[yellow]Nothing gradeable yet — come back after the next close.")


def cmd_history(args):
    cfg = load_config(args.config)
    jr = Journal(cfg["_paths"]["journal"])
    hist = jr.history(demo=args.demo)
    if hist.empty:
        console.print("[dim]Journal is empty.[/dim]")
        return
    render_history(console, hist)



def cmd_flow(args):
    cfg = load_config(args.config)
    fcfg = dict(cfg["flow"])
    if args.window:
        fcfg["window_min"] = args.window
    console = Console()
    jr = _journal(cfg, args.no_journal)
    store = flow_mod.new_state_store()

    if args.demo:
        from src.demo import FlowDemo
        fd = FlowDemo()
        med = flow_mod.default_curve()
        marks = [15, 35, 60, 90, 115, 135, 160, 185, 215, 260, 330][: args.ticks or 11]
        base = dt.datetime.combine(fd.day, dt.time(9, 30), tzinfo=NY)
        for k, m in enumerate(marks):
            now = base + dt.timedelta(minutes=m)
            rows, events = flow_mod.snapshot(
                fd.bars_until(now), fd.adv, fd.prev_close, {}, med, now, fcfg, store)
            ri, ro = flow_mod.rotation_lists(rows)
            report.render_flow(console, now, rows, events, ri, ro, "demo",
                               fcfg["window_min"],
                               clear=console.is_terminal and k > 0)
            _dump_flow_state(cfg, now, rows, ri, ro, "demo", fcfg["window_min"])
            if k == len(marks) - 1:
                demo_board = {"v": 4, "ts": now.isoformat(timespec="seconds"),
                    "session": "rth", "rows": [
                    {"ticker": "PYRO", "move": 4.13, "last": 1.94, "dollars": 6.1e6,
                     "vs_adv": 9.4, "off_hi": -0.03, "state": "IGNITING", "tp": 8.2,
                     "hot": True, "first_seen": "07:22", "new": False,
                     "heat": 97.0, "swings": 9, "path": 3.4, "catalyst": True,
                     "pr_ts": "07:19",
                     "headline": "PYRO Therapeutics Announces Strategic AI "
                                 "Partnership and $14M Purchase Order",
                     "flags": ["AI", "PARTNERSHIP", "PURCHASE ORDER"]},
                    {"ticker": "KNDL", "move": 1.46, "last": 3.61, "dollars": 4.1e6,
                     "vs_adv": 11.2, "off_hi": -0.01, "state": "RUNNING", "tp": 4.4,
                     "hot": True, "first_seen": "09:58", "new": True,
                     "heat": 84.0, "swings": 5, "path": 2.1},
                    {"ticker": "DRIP", "move": -0.34, "last": 2.05, "dollars": 2.7e6,
                     "vs_adv": 4.8, "off_hi": -0.02, "state": "LEAVING", "tp": 1.1,
                     "hot": False, "first_seen": "09:44", "new": False,
                     "heat": 31.0, "swings": 3, "path": 0.9},
                    {"ticker": "IGNA", "move": 0.12, "last": 8.7, "dollars": 2.6e6,
                     "vs_adv": 3.9, "off_hi": -0.016, "state": "FADING", "tp": 0.7,
                     "hot": False, "first_seen": "06:55", "new": False,
                     "heat": 12.0, "swings": 1, "path": 0.4}]}
                with open(os.path.join(_state_dir(cfg), "latest_board.json"), "w") as f:
                    json.dump(demo_board, f)
                demo_ext = {"v": 3, "ts": now.isoformat(timespec="seconds"), "session": "pre",
                            "rows": [{"ticker": "PYRO", "last": 1.94, "gap": 4.13,
                                      "dollars": 6.1e5, "vs_adv": 9.4, "new": True},
                                     {"ticker": "EMBR", "last": 0.62, "gap": 2.58,
                                      "dollars": 2.4e5, "vs_adv": 5.1, "new": True},
                                     {"ticker": "CNDR", "last": 12.40, "gap": -0.34,
                                      "dollars": 1.9e6, "vs_adv": 2.2, "new": False}]}
                with open(os.path.join(_state_dir(cfg), "latest_ext.json"), "w") as f:
                    json.dump(demo_ext, f)
            for ev in events:
                if jr:
                    jr.record_flow_event(True, ev)
            time.sleep(args.interval if args.interval is not None else
                       (0.8 if console.is_terminal else 0))
        return

    from src.providers_alpaca import creds as alpaca_creds
    ac = alpaca_creds()
    if ac:
        from src import flow_alpaca
        from src.providers_alpaca import AlpacaData
        ap = AlpacaData(*ac)
        sdir = _state_dir(cfg)
        watch = read_ticker_file(Path(cfg["_paths"]["root"]) / cfg["universe"]["watchlist_file"])
        seed = list(dict.fromkeys(
            (jr.latest_picks(False, 30) if jr else []) + watch))
        base = flow_alpaca.prepare(ap, sdir, seed, fcfg,
                                   lambda m: console.print(f"[dim]{m}[/dim]"))
        if base is None:
            console.print("[red]alpaca baselines failed — falling back to "
                          "yahoo flow this run[/red]")
        else:
            today = ny_today()
            store_path = os.path.join(sdir, f"flow_store_{today.isoformat()}.json")
            try:
                with open(store_path) as f:
                    store.update(json.load(f))
            except Exception:
                pass
            console.print(f"[dim]alpaca radar: {len(base['adv'])} symbols · "
                          f"engine seed {len(seed)} names · ctrl-c to stop[/dim]")
            tick = 0
            try:
                while True:
                    now = dt.datetime.now(NY)
                    radar_rows, movers, fresh, watchset, bars = flow_alpaca.fetch_tick(
                        ap, base, seed, now, sdir, fcfg)
                    rows, events = flow_mod.snapshot(
                        bars, base["adv"], base["prev_close"], base["curves"],
                        base["med"], now, fcfg, store)
                    ri, ro = flow_mod.rotation_lists(rows)
                    report.render_flow(console, now, rows, events, ri, ro,
                                       "alpaca-iex", fcfg["window_min"],
                                       clear=console.is_terminal and tick > 0)
                    if fresh:
                        console.print("[bold magenta]radar promoted:[/bold magenta] "
                                      + "  ".join(fresh))
                    for ev in events:
                        if jr:
                            jr.record_flow_event(False, ev)
                    _dump_flow_state(cfg, now, rows, ri, ro, "alpaca-iex",
                                     fcfg["window_min"])
                    flow_alpaca.dump_radar_state(sdir, now, radar_rows)
                    flow_alpaca.dump_movers_state(sdir, now, movers)
                    flow_alpaca.attach_heat(movers, bars, now)
                    _board = flow_alpaca.assemble_board(
                        sdir, now, "rth", movers,
                        {r["ticker"]: r for r in rows})
                    flow_alpaca.dump_pulse_state(sdir, now, radar_rows,
                                                 _board, cfg.get("pulse"))
                    try:
                        from src import intel
                        intel.refresh_intel(sdir, now, _board,
                                            cfg.get("intel"),
                                            log=lambda m: console.print(
                                                f"[dim]intel: {m}[/dim]"))
                    except Exception as e:
                        console.print(f"[yellow]intel skipped: {e}[/yellow]")
                    with open(store_path, "w") as f:
                        json.dump(store, f)
                    tick += 1
                    if args.ticks and tick >= args.ticks:
                        return
                    time.sleep(args.interval if args.interval is not None
                               else fcfg["refresh_sec"])
            except KeyboardInterrupt:
                console.print("\n[dim]flow monitor stopped.[/dim]")
            return

    provider = get_provider(False)
    watch = read_ticker_file(Path(cfg["_paths"]["root"]) / cfg["universe"]["watchlist_file"])
    monitor = list(dict.fromkeys(
        (jr.latest_picks(False, 30) if jr else [])
        + provider.screen("most_actives", 25)
        + provider.screen("day_gainers", 15)
        + watch))[: fcfg["monitor_cap"]]
    if len(monitor) < 5:
        console.print("[red]not enough names to monitor — run a scan first "
                      "or add tickers to watchlist.txt[/red]")
        sys.exit(1)
    today = ny_today()
    base_path = os.path.join(_state_dir(cfg), f"flow_base_{today.isoformat()}.json")
    base = None
    try:
        with open(base_path) as f:
            base = json.load(f)
    except Exception:
        pass
    if base:
        adv = base["adv"]
        prev_close = base["prev_close"]
        curves = {t: __import__("numpy").array(c) for t, c in base["curves"].items()}
        med = __import__("numpy").array(base["med"])
    else:
        with console.status("loading ADV + intraday volume-curve baselines…"):
            daily = provider.get_history(monitor, days=50)
            adv, prev_close = {}, {}
            for t, df in daily.items():
                hist = df[df.index.date < today] if df.index.date[-1] == today else df
                if len(hist) < 21:
                    continue
                adv[t] = float(hist["Volume"].iloc[-20:].mean())
                prev_close[t] = float(hist["Close"].iloc[-1])
            curves, med = flow_mod.build_curves(provider.get_intraday_recent(monitor))
        with open(base_path, "w") as f:
            json.dump({"adv": adv, "prev_close": prev_close,
                       "curves": {t: list(map(float, c)) for t, c in curves.items()},
                       "med": list(map(float, med))}, f)
    console.print(f"[dim]monitoring {len(adv)} names · per-name curves for "
                  f"{len(curves)} · refresh {fcfg['refresh_sec']}s · ctrl-c to stop[/dim]")
    store_path = os.path.join(_state_dir(cfg), f"flow_store_{today.isoformat()}.json")
    try:
        with open(store_path) as f:
            store.update(json.load(f))
    except Exception:
        pass
    tick = 0
    try:
        while True:
            now = dt.datetime.now(NY)
            bars = provider.get_intraday_batch(list(adv))
            rows, events = flow_mod.snapshot(bars, adv, prev_close, curves,
                                             med, now, fcfg, store)
            if not rows:
                console.print("[yellow]no intraday bars yet — is the market "
                              "open? retrying…[/yellow]")
            else:
                ri, ro = flow_mod.rotation_lists(rows)
                report.render_flow(console, now, rows, events, ri, ro,
                                   provider.name, fcfg["window_min"],
                                   clear=console.is_terminal and tick > 0)
                for ev in events:
                    if jr:
                        jr.record_flow_event(False, ev)
                _dump_flow_state(cfg, now, rows, ri, ro, provider.name,
                                 fcfg["window_min"])
                with open(store_path, "w") as f:
                    json.dump(store, f)
            tick += 1
            if args.ticks and tick >= args.ticks:
                return
            time.sleep(args.interval if args.interval is not None
                       else fcfg["refresh_sec"])
    except KeyboardInterrupt:
        console.print("\n[dim]flow monitor stopped.[/dim]")



def cmd_hub(args):
    cfg = load_config(args.config)
    from src import hub
    from src.config import PROJECT_ROOT
    out = args.out or os.path.join(PROJECT_ROOT, "docs")
    path = hub.build(cfg, out, demo=args.demo)
    Console().print(f"[bold]hub → {path}[/bold]")



def cmd_ext(args):
    cfg = load_config(args.config)
    fcfg = dict(cfg["flow"])
    console = Console()
    from src.providers_alpaca import creds as alpaca_creds
    ac = alpaca_creds()
    if not ac:
        console.print("[dim]ext sweep needs Alpaca keys — skipping (yahoo has "
                      "no full-market extended-hours view).[/dim]")
        return
    from src import flow_alpaca
    from src.providers_alpaca import AlpacaData
    ap = AlpacaData(*ac)
    sdir = _state_dir(cfg)
    jr = _journal(cfg, args.no_journal)
    watch = read_ticker_file(Path(cfg["_paths"]["root"]) / cfg["universe"]["watchlist_file"])
    seed = list(dict.fromkeys((jr.latest_picks(False, 30) if jr else []) + watch))
    base = flow_alpaca.prepare(ap, sdir, seed, fcfg,
                               lambda m: console.print(f"[dim]{m}[/dim]"))
    if base is None:
        console.print("[red]alpaca baselines failed — no ext sweep this run[/red]")
        return
    now = dt.datetime.now(NY)
    session = args.session or ("pre" if now.hour < 12 else "post")
    rows = flow_alpaca.ext_sweep(ap, base, now, sdir, fcfg, session,
                                 lambda m: console.print(f"[dim]{m}[/dim]"))
    fresh = flow_alpaca.record_ignitions(rows, sdir, now, session, jr, demo=False)
    flow_alpaca.assemble_board(sdir, now, session, rows)
    label = "PRE-MARKET" if session == "pre" else "AFTER-HOURS"
    if not rows:
        console.print(f"[dim]{label}: nothing gapping ≥"
                      f"{fcfg['ext_gap_min']:.0%} on real tape yet.[/dim]")
        return
    console.print(f"[bold red]{label} IGNITIONS[/bold red]  "
                  f"[dim]{now.strftime('%H:%M ET')}[/dim]")
    for r in rows:
        star = " [bold magenta]NEW[/bold magenta]" if r["ticker"] in fresh else ""
        va = f" · {r['vs_adv']:.1f}x ADV" if r.get("vs_adv") else ""
        console.print(f"  [bold]{r['ticker']:<6}[/bold] {r['last']:>8.3f}  "
                      f"{'[bold green]' if r['gap'] > 0 else '[bold red]'}"
                      f"{r['gap']:+.0%}[/]  ${r['dollars'] / 1e6:.2f}M ext{va}{star}")



def live_mode(now) -> str:
    if now.weekday() >= 5:
        return "off"
    hm = (now.hour, now.minute)
    if (7, 0) <= hm < (9, 30):
        return "pre"
    if (9, 32) <= hm and now.hour < 16:
        return "open"
    if (16, 10) <= hm and now.hour < 20:
        return "post"
    return "off"


def _git_push_state(console):
    import subprocess
    for c in ("git add -A docs data/state",
              "git commit -m live-tick -q",
              "git pull --rebase --autostash -q",
              "git push -q"):
        r = subprocess.run(c, shell=True, capture_output=True, text=True)
        if "commit" in c and r.returncode != 0:
            return                       # nothing changed this tick
    console.print("[dim]pushed[/dim]")


def cmd_live(args):
    """One long-running shift: tick every ~3 minutes across pre-market,
    regular hours, and after-hours, pushing the hub each tick. This is how
    a CPHI ignition reaches the phone in minutes instead of twenty."""
    os.environ["IGNITION_JSONL"] = "1"
    cfg = load_config(args.config)
    now = ny_now()
    until = args.until_et or ("12:52" if now.hour < 12 or
                              (now.hour == 12 and now.minute < 30) else "18:52")
    hh, mm = map(int, until.split(":"))
    end = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    fcfg = dict(cfg["flow"])
    ncfg = dict(cfg.get("news") or {})
    fast_sec = float(fcfg.get("fast_sec", 45))
    full_sec = args.interval or float(fcfg.get("full_sec", 160))
    console.print(f"[bold]LIVE[/bold] shift until {until} ET · full tick {full_sec:.0f}s "
                  f"· catalyst/fast lane {fast_sec:.0f}s · "
                  f"push={bool(os.environ.get('IGNITION_GIT_PUSH'))}")
    from src import flow_alpaca, newswire
    from src.providers_alpaca import AlpacaData, creds as _acreds
    seen: set = set()
    next_full = 0.0
    while ny_now() < end:
        t0 = time.time()
        now = ny_now()
        mode = live_mode(now)
        try:
            if mode == "off":
                console.print("[dim]off-hours — idling[/dim]")
            elif t0 >= next_full:
                # ---- full discovery tick -------------------------------
                if mode in ("pre", "post"):
                    cmd_ext(argparse.Namespace(session=mode, no_journal=False,
                                               config=args.config))
                else:
                    cmd_flow(argparse.Namespace(demo=False, ticks=1, interval=0,
                                                window=None, no_journal=False,
                                                config=args.config))
                next_full = time.time() + full_sec
                cmd_hub(argparse.Namespace(demo=False, out=None,
                                           config=args.config))
                if os.environ.get("IGNITION_GIT_PUSH"):
                    _git_push_state(console)
            else:
                # ---- 45s lane: press wires + hot-set refresh -----------
                changed = False
                ac = _acreds()
                base = None
                if ac:
                    today = ny_today().isoformat()
                    bp = os.path.join(_state_dir(cfg), f"alpaca_base_{today}.json")
                    try:
                        with open(bp) as fh:
                            base = json.load(fh)
                    except Exception:
                        base = None
                events = []
                if ncfg.get("enabled") and base:
                    from src.universe import ETF_EXCLUDE
                    events = newswire.poll(
                        ncfg.get("feeds") or [], seen,
                        valid=set(base["adv"]), exclude=ETF_EXCLUDE,
                        min_score=int(ncfg.get("min_score", 2)),
                        log=lambda m: console.print(f"[dim]{m}[/dim]"))
                    jrl = _journal(cfg, False)
                    for e in events:
                        console.print(f"[bold red]📰 CATALYST[/bold red] "
                                      f"[bold]{e['symbol']}[/bold] "
                                      f"({e['ts'].strftime('%H:%M')}) {e['headline'][:90]}")
                        if jrl:
                            jrl.record_flow_event(False, {
                                "ts": now, "ticker": e["symbol"], "prev": None,
                                "state": "CATALYST", "price": 0.0, "tp": None,
                                "note": e["headline"][:120]})
                if ac and base:
                    ap = AlpacaData(*ac)
                    base["med"] = base.get("med") or []
                    changed = flow_alpaca.fast_update(
                        ap, base, _state_dir(cfg), now, fcfg, events)
                if changed or events:
                    cmd_hub(argparse.Namespace(demo=False, out=None,
                                               config=args.config))
                    if os.environ.get("IGNITION_GIT_PUSH"):
                        _git_push_state(console)
        except SystemExit:
            pass
        except Exception as e:
            console.print(f"[yellow]tick error (continuing): {e}[/yellow]")
        time.sleep(max(3.0, fast_sec - (time.time() - t0)))


def cmd_paths(args):
    cfg = load_config(args.config)
    for k, v in cfg["_paths"].items():
        console.print(f"[bold]{k:8}[/bold] {v}")


def main():
    p = argparse.ArgumentParser(prog="ignition", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None, help="path to config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("scan", help="rank tomorrow's likely movers")
    ps.add_argument("--demo", action="store_true", help="offline synthetic market")
    ps.add_argument("--premarket", action="store_true",
                    help="morning mode: use pre-market tape instead of after-hours")
    ps.add_argument("--fast", action="store_true", help="skip the enrichment pass")
    ps.add_argument("--top", type=int, default=None, help="rows to display")
    ps.add_argument("--enrich", type=int, default=None, help="names to enrich")
    ps.add_argument("--no-journal", action="store_true")
    ps.add_argument("--csv", default=None)
    ps.add_argument("--json", default=None)
    ps.add_argument("--tv", default=None, help="write TradingView watchlist file")
    ps.set_defaults(func=cmd_scan)

    pe = sub.add_parser("eval", help="grade past scans against realized activity")
    pe.add_argument("--demo", action="store_true")
    pe.set_defaults(func=cmd_eval)

    ph = sub.add_parser("history", help="rolling IC / edge across all scans")
    ph.add_argument("--demo", action="store_true")
    ph.set_defaults(func=cmd_history)

    pf = sub.add_parser("flow", help="live intraday money-rotation monitor")
    pf.add_argument("--demo", action="store_true")
    pf.add_argument("--ticks", type=int, default=None, help="stop after N refreshes")
    pf.add_argument("--interval", type=float, default=None, help="seconds between refreshes")
    pf.add_argument("--window", type=int, default=None, help="trailing window minutes")
    pf.add_argument("--no-journal", action="store_true")
    pf.add_argument("--config", default=None)
    pf.set_defaults(func=cmd_flow)

    pb = sub.add_parser("hub", help="render the static phone dashboard (docs/)")
    pb.add_argument("--demo", action="store_true")
    pb.add_argument("--out", default=None)
    pb.add_argument("--config", default=None)
    pb.set_defaults(func=cmd_hub)

    px = sub.add_parser("ext", help="full-market pre-market / after-hours ignition sweep")
    px.add_argument("--session", choices=["pre", "post"], default=None)
    px.add_argument("--no-journal", action="store_true")
    px.add_argument("--config", default=None)
    px.set_defaults(func=cmd_ext)

    pl = sub.add_parser("live", help="long-running shift: tick pre/rth/post every ~3min")
    pl.add_argument("--until-et", default=None, help="HH:MM ET (default: auto shift end)")
    pl.add_argument("--interval", type=float, default=170)
    pl.add_argument("--config", default=None)
    pl.set_defaults(func=cmd_live)

    pp = sub.add_parser("paths", help="show config / journal / cache locations")
    pp.set_defaults(func=cmd_paths)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
