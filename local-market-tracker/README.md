# Local Market Tracker

Local Kalshi and Polymarket tracking app with a backend proxy, persistent watchlist, live browser updates, and price-history charts.

Run:

```bash
python3 local-market-tracker/server.py --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

SSH tunnel from your laptop to a remote host running the app:

```bash
ssh -L 8765:127.0.0.1:8765 <host>
```

Then open `http://127.0.0.1:8765/` locally.

The app uses unauthenticated public market-data endpoints only. It does not place trades or store exchange credentials.
