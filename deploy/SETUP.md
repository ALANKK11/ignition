# IGNITION on autopilot — phone hub in ~5 minutes, $0

The result: scans and flow snapshots run themselves in GitHub's cloud on a
schedule, and your phone gets one always-fresh page at
`https://<your-username>.github.io/<repo-name>/`. Nothing runs on your machine.

## One-time setup

1. Create a GitHub account if you don't have one. Create a **new repository**
   (e.g. `ignition`). **Public** repo = unlimited free Actions minutes and free
   Pages hosting. (Private repo = 2,000 free minutes/month, which this fits in
   at ~950/month — but GitHub Pages on private repos needs a paid plan, so for
   $0 hosting with a private repo you'd deploy `docs/` to Cloudflare Pages
   instead. Public is the 5-minute path; the page only exposes tickers and
   scores.)

2. Push this folder to it:

       cd ignition
       git init -b main && git add -A && git commit -m "ignition"
       git remote add origin https://github.com/<you>/<repo>.git
       git push -u origin main

3. Repo **Settings → Pages** → Source: *Deploy from a branch* →
   Branch: `main`, folder: `/docs` → Save.

4. Repo **Settings → Actions → General → Workflow permissions** →
   select **Read and write permissions** → Save.

5. **Actions tab** → `evening scan` → *Run workflow* (this seeds the first
   scan + the hub). A minute later your page is live. Open it on your phone →
   Share → **Add to Home Screen**. Done: it's an app now.

## What runs when (all times ET, all automatic)

| workflow | schedule | does |
|---|---|---|
| `evening scan` | 9:15pm Mon–Fri | grades yesterday's scan, runs tonight's scan, updates hub |
| `premarket confirm` | 7:45am Mon–Fri | re-ranks on pre-market tape, updates hub |
| `flow snapshot` | every 20 min, 9:32am–4pm | one flow reading (fade/rotation states), updates hub |

Cron runs in UTC, so after the November clock change everything fires an hour
earlier ET; either live with it or shift the three cron lines by +1 hour in
`.github/workflows/` for winter. GitHub also queues scheduled runs under load —
expect occasional 3–10 minute delays. The journal (SQLite) and state files are
committed to the repo by the bot, so history and the flow state machine's
memory survive between runs.

## Cost, honestly

$0.00. Public repo: Actions and Pages are free without limits that this
project can reach. Yahoo data: free, no key. Nothing here needs a card.
