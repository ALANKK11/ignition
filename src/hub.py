"""
IGNITION HUB — renders the phone dashboard as one static HTML file.

Design constraints: must be readable half-asleep on a phone, zero external
assets (works from any static host, loads instantly, no CDN, no tracking),
auto-refreshes itself, and looks like an instrument, not a website.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os

from .util import NY, fmt_big

TAG_HEX = {"bold magenta": "#e879f9", "magenta": "#e879f9", "cyan": "#22d3ee",
           "bold yellow": "#facc15", "yellow": "#fde047", "bold red": "#f87171",
           "red": "#f87171", "green": "#4ade80", "bold green": "#4ade80"}
STATE_HEX = {"IGNITING": "#f87171", "NEW MONEY": "#e879f9", "RUNNING": "#facc15",
             "CHURN": "#22d3ee", "FADING": "#fb923c", "LEAVING": "#ef4444",
             "COOLING": "#9ca3af", "QUIET": "#4b5563", "OPEN DRIVE": "#fde047",
             "PM HOT": "#4ade80", "PRE-OPEN": "#4b5563",
             "PM IGNITION": "#ff5a1f", "AH IGNITION": "#ff5a1f"}
INTERESTING = ["LEAVING", "FADING", "IGNITING", "NEW MONEY", "CHURN",
               "OPEN DRIVE", "PM HOT", "RUNNING"]

CSS = """
:root{--bg:#0b0d10;--card:#14181d;--edge:#242a31;--tx:#e7e9ec;--dim:#8b939c;
--mut:#5b636c;--hot:#ff5a1f;--ok:#4ade80;--bad:#f87171}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:15px/1.45 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,sans-serif;padding:14px 12px 60px;max-width:640px;margin:0 auto;
-webkit-font-smoothing:antialiased}
h1{font-size:20px;letter-spacing:.06em}h1 s{color:var(--hot);text-decoration:none}
.sub{color:var(--dim);font-size:12px;margin-top:2px}
h2{font-size:11px;letter-spacing:.14em;color:var(--dim);margin:22px 2px 8px;
text-transform:uppercase}
.card{background:var(--card);border:1px solid var(--edge);border-radius:12px;
padding:10px 12px;margin-bottom:8px}
.row{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.tk{font-weight:700;font-size:16px}
.px{color:var(--dim);font-size:13px}
.up{color:var(--ok)}.dn{color:var(--bad)}
.chip{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.04em;
padding:2px 7px;border-radius:999px;border:1px solid}
.note{color:var(--dim);font-size:12.5px;margin-top:4px}
.bar{height:5px;border-radius:3px;background:#20262d;margin-top:7px;overflow:hidden}
.bar i{display:block;height:100%;background:linear-gradient(90deg,#7a3010,var(--hot))}
.score{font-weight:800;font-size:15px}
.meta{display:flex;gap:14px;color:var(--dim);font-size:12px;margin-top:5px;flex-wrap:wrap}
.meta b{color:var(--tx);font-weight:600}
.rot{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 2px}
.ev{font-size:12.5px;color:var(--dim);padding:3px 2px;border-bottom:1px solid var(--edge)}
.ev:last-child{border:none}
.ev b{color:var(--tx)}
.quiet{color:var(--mut);font-size:12px;margin-top:6px}
.audit{display:flex;gap:10px;overflow-x:auto;padding-bottom:4px}
.audit .card{min-width:118px;flex:0 0 auto;text-align:center}
.big{font-size:19px;font-weight:800}
.foot{color:var(--mut);font-size:11px;margin-top:26px;line-height:1.6}
.stale{background:#3a1d12;border:1px solid #7a3010;color:#ffb08c;border-radius:10px;
padding:8px 12px;font-size:12.5px;margin-bottom:10px}
"""

JS = """
const el=document.getElementById('ago');
if(el){const t=Date.parse(el.dataset.ts);const f=()=>{const m=Math.max(0,
Math.round((Date.now()-t)/60000));el.textContent=m<1?'just now':m+' min ago';
if(m>=45)document.getElementById('stale')?.removeAttribute('hidden')};
f();setInterval(f,20000)}
"""


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _pct(x, dec=1):
    if x is None:
        return '<span class="px">--</span>'
    cls = "up" if x > 0 else ("dn" if x < 0 else "px")
    return f'<span class="{cls}">{x * 100:+.{dec}f}%</span>'


def _chip(text, hexc):
    return (f'<span class="chip" style="color:{hexc};border-color:{hexc}55;'
            f'background:{hexc}14">{html.escape(str(text))}</span>')


def _transitions_only(events):
    if not events:
        return ""
    lines = "".join(
        f'<div class="ev">{e["ts"][11:16]} &nbsp;<b>{html.escape(e["ticker"])}</b> '
        f'{html.escape(e["prev_state"] or "—")} → '
        f'<b style="color:{STATE_HEX.get(e["state"], "#e7e9ec")}">{html.escape(e["state"])}</b>'
        f'{" &nbsp;·&nbsp; " + html.escape(e["note"]) if e["note"] else ""}</div>'
        for e in events[-12:][::-1])
    return f'<h2>Transitions today</h2><div class="card">{lines}</div>'


def _scan_section(scan, confluence_only=False):
    if not scan:
        return '<h2>Scan</h2><div class="card note">No scan yet — first scheduled run will fill this in.</div>'
    ext_l = scan.get("ext_label", "AH")
    head = (f'<h2>{"Morning confirm" if scan["mode"] == "premarket" else "Tonight&rsquo;s scan"}'
            f' · {scan["trade_date"]} targets {scan["target_date"]}</h2>')
    rows_ = scan["rows"]
    if confluence_only:
        rows_ = [r for r in rows_ if r["score"] >= 40][:6]
        if not rows_:
            return ""
        head = (f'<h2>Tonight&rsquo;s setups · real confluence only · '
                f'targets {scan["target_date"]}</h2>')
    cards = []
    for r in rows_:
        tags = " ".join(_chip(t, TAG_HEX.get(c, "#9ca3af")) for t, c in r["tags"])
        drv = " · ".join(r["drivers"])
        cards.append(f'''<div class="card">
<div class="row"><span class="px">#{r["rank"]}</span><span class="tk">{html.escape(r["ticker"])}</span>
<span class="score" style="color:{'#f87171' if r["score"] >= 55 else ('#facc15' if r["score"] >= 42 else '#e7e9ec')}">{r["score"]:.1f}</span>
<span class="px">{r["close"]:.2f}</span>{_pct(r["day_pct"])}<span class="px">{ext_l}</span>{_pct(r["ext_pct"])}
<span class="px">{('%.1fx' % r["rvol"]) if r["rvol"] else ''}</span></div>
<div class="bar"><i style="width:{min(r["score"], 100):.0f}%"></i></div>
<div class="row" style="margin-top:7px">{tags}</div>
{f'<div class="note">{html.escape(drv)}</div>' if drv else ''}</div>''')
    return head + "".join(cards)


def _flow_section(flow, events):
    if not flow:
        return ""
    rows = flow["rows"]
    hot = [r for r in rows if r["state"] in INTERESTING]
    hot.sort(key=lambda r: INTERESTING.index(r["state"]))
    quiet = len(rows) - len(hot)
    ts = dt.datetime.fromisoformat(flow["ts"])
    head = f'<h2>Flow · as of {ts.strftime("%H:%M")} ET</h2>'
    rot = ""
    if flow.get("rot_in") or flow.get("rot_out"):
        chips = [_chip(f"IN {r['ticker']} {r['surge']:.1f}x its norm", "#e879f9")
                 for r in flow.get("rot_in", [])]
        chips += [_chip(f"OUT {r['ticker']} {r['frac']:.0%} of peak", "#ef4444")
                  for r in flow.get("rot_out", [])]
        rot = f'<div class="rot">{"".join(chips)}</div>'
    cards = []
    for r in hot:
        hexc = STATE_HEX.get(r["state"], "#9ca3af")
        tp = f'{r["tp"]:.1f}x now' if r["tp"] is not None else ""
        pace = f'pace {r["pace"]:.1f}x' if r["pace"] is not None else ""
        vw = f'vwap {r["vs_vwap"] * 100:+.1f}%' if r["vs_vwap"] is not None else ""
        cards.append(f'''<div class="card" style="border-left:3px solid {hexc}">
<div class="row"><span class="tk">{html.escape(r["ticker"])}</span>
{_chip(r["state"], hexc)}<span class="px">{r["last"]:.2f}</span>{_pct(r["day_pct"])}</div>
<div class="meta"><b>{tp}</b><span>{pace}</span><span>{vw}</span>
<span>${fmt_big(r["dollar_w"])} /15m</span></div>
{f'<div class="note">{html.escape(r["note"])}</div>' if r["note"] else ''}</div>''')
    evs = ""
    if events:
        lines = "".join(
            f'<div class="ev">{e["ts"][11:16]} &nbsp;<b>{html.escape(e["ticker"])}</b> '
            f'{html.escape(e["prev_state"] or "—")} → '
            f'<b style="color:{STATE_HEX.get(e["state"], "#e7e9ec")}">{html.escape(e["state"])}</b>'
            f'{" &nbsp;·&nbsp; " + html.escape(e["note"]) if e["note"] else ""}</div>'
            for e in events[-14:][::-1])
        evs = f'<h2>Transitions today</h2><div class="card">{lines}</div>'
    q = f'<div class="quiet">+ {quiet} names quiet</div>' if quiet else ""
    return head + rot + "".join(cards) + q + evs


SESSION_LABEL = {"pre": "Pre-market board", "rth": "Live board",
                 "post": "After-hours board"}


def _heat_meter(heat):
    if heat is None:
        return ""
    n = max(0, min(5, int(round(heat / 20))))
    col = "#f87171" if heat >= 70 else ("#facc15" if heat >= 40 else "#5b636c")
    return (f'<span style="color:{col};letter-spacing:1px;font-size:13px">'
            f'{"▰" * n}{"▱" * (5 - n)}</span>'
            f'<span class="px" style="margin-left:4px">{heat:.0f}</span>')


def _board_section(bd, closed_now, stale_day=None):
    if not bd or not bd.get("rows"):
        return ""
    ts = bd["ts"][11:16]
    label = SESSION_LABEL.get(bd.get("session"), "Board")
    if closed_now and bd.get("session") == "rth":
        label = "Today&rsquo;s board (closed)"
    if stale_day:
        label = f"Last board · {stale_day} {SESSION_LABEL.get(bd.get('session'), '')}"
    cards = []
    for r in bd["rows"]:
        up = r["move"] > 0
        chips = ""
        if r.get("new"):
            chips += _chip("NEW", "#ff5a1f")
        if r.get("hot"):
            chips += _chip("HOT NOW", "#facc15")
        if r.get("pin"):
            chips += _chip("PIN · DEAD", "#e879f9")
        if r.get("catalyst"):
            chips += _chip(f'PR {r.get("pr_ts") or ""}', "#ff5a1f")
        if r.get("state"):
            chips += _chip(r["state"], STATE_HEX.get(r["state"], "#9ca3af"))
        meta = [f'${fmt_big(r["dollars"])}']
        if r.get("vs_adv"):
            meta.append(f'{r["vs_adv"]:.1f}x ADV')
        if r.get("off_hi") is not None:
            meta.append(f'{r["off_hi"] * 100:+.0f}% off high')
        if r.get("swings"):
            meta.append(f'{r["swings"]} swings')
        if r.get("path") is not None:
            meta.append(f'{r["path"] * 100:.0f}% traveled')
        if r.get("tp"):
            meta.append(f'{r["tp"]:.1f}x tape now')
        meta.append(f'since {r["first_seen"]}')
        cards.append(f"""<div class="card" style="border-left:3px solid {'#4ade80' if up else '#f87171'}">
<div class="row"><span class="tk" style="font-size:18px">{html.escape(r["ticker"])}</span>
<span class="score" style="font-size:19px;color:{'#4ade80' if up else '#f87171'}">{r["move"] * 100:+.0f}%</span>
{_heat_meter(r.get("heat"))}
<span class="px">{r["last"]:.2f}</span>{chips}</div>
<div class="meta">{"".join(f"<span>{m}</span>" for m in meta)}</div>
{f'<div class="note" style="margin-top:5px;color:#c9ced4">📰 {html.escape(r["headline"])}'
 + ("".join(" " + _chip(fl, "#f87171" if fl.startswith("⚠") else "#8b939c")
            for fl in (r.get("flags") or [])[:3])) + "</div>"
 if r.get("headline") else ""}</div>""")
    return (f'<h2 style="color:#ff5a1f;font-size:13px">{label} · {ts} ET</h2>'
            '<div class="note" style="margin:-4px 2px 8px">every US listing, ETFs out, '
            'volume-verified, ranked by the HEAT METER: intraday travel a human could '
            'actually trade (one-print gaps score ~0), swing count, and whether '
            'it&rsquo;s moving RIGHT NOW</div>'
            + "".join(cards))


def _ext_section(ext):
    if not ext or not ext.get("rows"):
        return ""
    pre = ext.get("session") == "pre"
    ts = ext["ts"][11:16]
    cards = []
    for r in ext["rows"]:
        va = f'{r["vs_adv"]:.1f}x ADV' if r.get("vs_adv") else ""
        cards.append(f"""<div class="card" style="border-left:3px solid #ff5a1f">
<div class="row"><span class="tk">{html.escape(r["ticker"])}</span>
<span class="score" style="color:{'#4ade80' if r["gap"] > 0 else '#f87171'}">{r["gap"] * 100:+.0f}%</span>
<span class="px">{r["last"]:.3f}</span>
<span class="px">${fmt_big(r["dollars"])} ext</span><span class="px">{va}</span>
{_chip("NEW", "#ff5a1f") if r.get("new") else ""}</div></div>""")
    title = "Pre-market ignitions" if pre else "After-hours ignitions"
    return (f'<h2 style="color:#ff5a1f">{title} · full market · {ts} ET</h2>'
            '<div class="note" style="margin:-4px 2px 8px">every US listing, '
            'price floor $0.10 — gaps confirmed by real extended-hours dollar '
            'volume, auto-injected into the next scan</div>' + "".join(cards))


def _movers_section(mv):
    if not mv or not mv.get("rows"):
        return ""
    ts = mv["ts"][11:16]
    cards = []
    for r in mv["rows"]:
        cards.append(f"""<div class="card" style="border-left:3px solid {'#4ade80' if r['day_pct'] > 0 else '#f87171'}">
<div class="row"><span class="tk">{html.escape(r["ticker"])}</span>
<span class="score" style="font-size:17px;color:{'#4ade80' if r['day_pct'] > 0 else '#f87171'}">{r["day_pct"] * 100:+.0f}%</span>
<span class="px">{r["last"]:.2f}</span><span class="px">{r["pace"]:.1f}x vol</span>
<span class="px">${fmt_big(r["dollar_day"])} iex</span>
{_chip("WATCHING", "#e879f9") if r.get("promoted") else ""}</div></div>""")
    return (f'<h2 style="color:#4ade80">Today&rsquo;s tape · real movers · {ts} ET</h2>'
            '<div class="note" style="margin:-4px 2px 8px">every US listing, ETFs '
            'excluded — only names that actually moved ≥15% on real, elevated '
            'volume, biggest move first</div>' + "".join(cards))


def _audit_section(hist):
    if hist is None or len(hist) == 0:
        return ""
    cards = []
    for _, r in list(hist.iterrows())[:8]:
        ic = r.get("ic")
        edge = r.get("edge_rvol")
        cards.append(f'''<div class="card"><div class="px">{r["trade_date"]}</div>
<div class="big" style="color:{'#4ade80' if (ic or 0) > 0 else '#f87171'}">
{f"{ic:+.2f}" if ic == ic and ic is not None else "…"}</div>
<div class="px">IC · edge {f"{edge:.1f}x" if edge and edge == edge else "--"}</div></div>''')
    return ('<h2>Self-audit · rank IC per scan</h2>'
            f'<div class="audit">{"".join(cards)}</div>')


def _ign_precision_line(jr):
    try:
        h_, n = jr.ignition_stats()
    except Exception:
        return ""
    if not n:
        return ""
    col = "#4ade80" if h_ / n >= 0.5 else "#f87171"
    return (f'<div class="note" style="margin-top:6px">ignition receipts, 30d: '
            f'<b style="color:{col}">{h_}/{n} hit</b> — an ignition "hits" if it '
            f'then trades ≥2x its ADV or ≥$1M in the regular session</div>')


def build(cfg: dict, out_dir: str, demo: bool = False) -> str:
    from .journal import Journal
    sdir = os.path.join(cfg["_paths"]["data"], "state")
    scan = _load(os.path.join(sdir, "latest_scan.json"))
    flow = _load(os.path.join(sdir, "latest_flow.json"))
    STATE_V = 4
    bd = _load(os.path.join(sdir, "latest_board.json"))
    if bd and bd.get("v") != STATE_V:
        bd = None
    mv = _load(os.path.join(sdir, "latest_movers.json"))
    if mv and mv.get("v") != STATE_V:
        mv = None                        # written by old code — never render
    ext = _load(os.path.join(sdir, "latest_ext.json"))
    if ext and ext.get("v") != STATE_V:
        ext = None
    if ext:
        newest = max(dt.datetime.now(NY).date().isoformat(),
                     (flow or {}).get("ts", "")[:10])
        if ext.get("ts", "")[:10] < newest:
            ext = None                   # an old sweep is history, not news
    if mv:
        newest = max(dt.datetime.now(NY).date().isoformat(),
                     (flow or {}).get("ts", "")[:10])
        if mv.get("ts", "")[:10] < newest:
            mv = None
    bd_stale = None
    if bd and bd.get("ts", "")[:10] < dt.datetime.now(NY).date().isoformat():
        bd_stale = bd["ts"][:10]      # overnight: show it, but say when it's from
    jr = Journal(cfg["_paths"]["journal"])
    hist = None
    events = []
    try:
        hist = jr.history(demo, limit=8)
    except Exception:
        pass
    if flow:
        day = flow["ts"][:10]
        try:
            import json as _json
            p = os.path.join(sdir, f"flow_events_{day}.jsonl")
            if os.path.exists(p):
                with open(p) as fh:
                    events = [_json.loads(x) for x in fh if x.strip()]
            else:
                events = jr.events_for_day(demo, day)
        except Exception:
            events = []
    now = dt.datetime.now(NY)
    ts_iso = (flow or scan or {}).get("ts", now.isoformat())
    closed = ""
    if flow:
        wd = now.weekday() < 5
        rth = wd and (dt.time(9, 30) <= now.time() < dt.time(16, 0))
        if not rth:
            closed = ('<div class="stale" style="background:#14181d;border-color:'
                      '#242a31;color:#8b939c">market closed — flow and radar below '
                      f'are the session&rsquo;s last readings ({flow["ts"][11:16]} ET)</div>')
    is_closed = bool(closed)
    if bd:
        body = (_board_section(bd, is_closed, bd_stale) + closed
                + _transitions_only(events)
                + _scan_section(scan, confluence_only=True)
                + _audit_section(hist) + _ign_precision_line(jr))
    else:
        body = (_ext_section(ext) + _movers_section(mv) + closed
                + _flow_section(flow, events)
                + _scan_section(scan, confluence_only=True)
                + _audit_section(hist) + _ign_precision_line(jr))
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta http-equiv="refresh" content="300">
<meta name="theme-color" content="#0b0d10">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon.svg"><link rel="apple-touch-icon" href="icon.svg">
<title>IGNITION</title><style>{CSS}</style></head><body>
<h1><s>IGNITION</s> HUB{' · DEMO' if demo else ''}</h1>
<div class="sub">updated <span id="ago" data-ts="{ts_iso}">…</span> ·
auto-refreshes · board every 20m 7am–8pm · scan 9:15pm + 7:45am ET
{f" · <b style='color:#4ade80'>{html.escape(flow['provider'])}</b>" if flow else ""}</div>
<div id="stale" class="stale" hidden>This page hasn&rsquo;t updated in a while —
market closed, or check the Actions tab of your repo.</div>
{body}
<div class="foot">IGNITION ranks expected <b>activity</b>, not direction — volume and
range cut both ways. Not investment advice. Flow snapshots on the hub refresh every
~20 min; run <code>python ignition.py flow</code> locally for the 75-second live board.</div>
<script>{JS}</script></body></html>"""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(doc)
    with open(os.path.join(out_dir, "manifest.webmanifest"), "w") as f:
        json.dump({"name": "IGNITION", "short_name": "IGNITION",
                   "start_url": "./", "display": "standalone",
                   "background_color": "#0b0d10", "theme_color": "#0b0d10",
                   "icons": [{"src": "icon.svg", "sizes": "any",
                              "type": "image/svg+xml"}]}, f)
    with open(os.path.join(out_dir, "icon.svg"), "w") as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                '<rect width="100" height="100" rx="22" fill="#0b0d10"/>'
                '<path d="M50 14c4 14-8 20-8 32a8 8 0 0016 0c0-6-3-9-3-14 '
                '10 6 17 16 17 27a22 22 0 11-44 0c0-19 18-27 22-45z" fill="#ff5a1f"/></svg>')
    open(os.path.join(out_dir, ".nojekyll"), "w").close()
    return os.path.join(out_dir, "index.html")
