# docs

## dashboard.png

The README hero image. It is reproducible — generated from seeded demo data
(not live traffic) so the fleet always looks representative:

```bash
# 1. Seed a throwaway DB + demo services file with a realistic fleet
PYTHONPATH=src MONITOR_DATABASE_URL="sqlite:///./demo.db" \
  python scripts/seed_demo.py ./demo-services.yaml

# 2. Serve it (huge check interval, so the scheduler never overwrites the seed)
PYTHONPATH=src MONITOR_DATABASE_URL="sqlite:///./demo.db" \
  MONITOR_SERVICES_FILE="./demo-services.yaml" \
  python -m uvicorn monitor.main:app --port 8765

# 3. Screenshot http://localhost:8765/ at ~1460x1015 and save as docs/dashboard.png
#    (any headless browser; e.g. Edge/Chrome --headless --screenshot=...)

# 4. Clean up: rm demo.db demo-services.yaml
```

`scripts/seed_demo.py` is a one-off tooling script, not part of the app.
