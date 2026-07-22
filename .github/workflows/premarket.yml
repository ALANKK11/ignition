name: premarket confirm
on:
  schedule:
    - cron: "45 11 * * 1-5"    # 7:45am ET during daylight time (cron is UTC)
  workflow_dispatch:            # lets you run it manually from the Actions tab
permissions:
  contents: write
concurrency:
  group: ignition
  cancel-in-progress: false
jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - name: premarket scan · render hub
        env:
          IGNITION_HOME: ${{ github.workspace }}/data
          ALPACA_KEY_ID: ${{ secrets.ALPACA_KEY_ID }}
          ALPACA_SECRET_KEY: ${{ secrets.ALPACA_SECRET_KEY }}
          FINNHUB_KEY: ${{ secrets.FINNHUB_KEY }}
        run: |
          python ignition.py scan --premarket
          python ignition.py hub
      - name: publish
        run: |
          git config user.name ignition-bot
          git config user.email ignition@users.noreply.github.com
          git add -A data docs
          git commit -m "premarket confirm $(date -u +%F)" || exit 0
          git pull --rebase --autostash
          git push
