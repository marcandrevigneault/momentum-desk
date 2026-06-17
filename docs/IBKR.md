# IBKR connection — the one-phone-tap flow

momentum-desk connects to Interactive Brokers the same way `bravos-interactive-link`
does: through the **IBKR Client Portal Gateway** (a local REST server on
`localhost:5000`), with login automated by [**ibeam**](https://github.com/voyz/ibeam).
You never type credentials into a browser and you never run TWS. The only manual
step is **approving the IBKR Mobile push (IB Key 2FA) on your phone** — one tap.

```
ibeam (headless Chromium, auto-fills user/pass)  ──►  CP Gateway :5000  ──►  api.ibkr.com
        ▲ approve push on phone (once / ~24h)            ▲ ib_proxy :5002 (TLS un-fingerprint)
        │                                                │
   IBKR_USERNAME / IBKR_PASSWORD (secrets)        momentum-desk REST client + /tickle keepalive
```

There are **two** IBKR adapters in the codebase:
- `broker/ibkr.py` — legacy `ib_async` socket adapter (TWS / IB Gateway desktop). Unused by this flow.
- `broker/ibkr_cp.py` + `broker/cp/` — **this** Client Portal REST flow. Paper-first, dry-run by default.

## Cloud (the existing Fly app) — recommended

Everything (gateway + ibeam + dashboard) is baked into one image and runs under
supervisord. The machine stays always-on (`auto_stop_machines = "off"`) because
the IBKR session lives in the gateway process and dies if the machine stops.

```bash
# 1. Set the login secrets (consumed by ibeam; never stored in app code/repo)
fly secrets set IBKR_USERNAME=you IBKR_PASSWORD=•••• IBKR_PAPER=true --app momentum-desk-mav

# 2. Deploy the multi-process image
fly deploy

# 3. One-time 2FA: tunnel to the in-container gateway and approve the push
fly proxy 5000 --app momentum-desk-mav
#   → open https://localhost:5000, accept the self-signed cert
#   → ibeam has already filled your credentials; submit, then APPROVE THE PUSH on your phone
#   → you'll see "Client login succeeds". Ctrl-C the proxy.

# 4. Confirm
open https://momentum-desk-mav.fly.dev/          # dashboard shows the IBKR banner
curl -s https://momentum-desk-mav.fly.dev/api/health   # {"ok":true} (auth-exempt)
```

`GET /api/ibkr/status` returns `{enabled, ok, authenticated, connected, competing,
account_id, paper, last_tickle_at}` — that's what the dashboard banner reads.

### Re-auth
The session lasts ~24h and drops on any competing login (TWS, the mobile app,
another machine). ibeam auto-retries; if it can't, repeat step 3. The `/tickle`
keepalive (every 60s, started in `server.py`'s lifespan) holds the session open
in between.

## Local development

```bash
./scripts/install_gateway.sh          # one-time: download the CP gateway into ./gateway
./scripts/run_gateway.sh              # starts it on https://localhost:5000 (leave running)
#   → open https://localhost:5000, log in, approve the phone push
IBKR_ENABLED=true uvicorn momentum_desk.server:app --reload    # dashboard + keepalive
```

Locally you log in through the browser yourself (no ibeam); the REST client and
keepalive behave identically.

## Safety posture

- **Paper-first** — `IBKRCPBroker(require_paper=True)` refuses to route orders if the
  gateway is authenticated into a non-paper account (live ids don't start with `DU`)
  unless `allow_live=True` is passed explicitly. Keep `IBKR_PAPER=true` until you've
  watched paper runs.
- **Dry-run by default** — `place_order` returns `dry_run` without transmitting unless
  the adapter is constructed with `dry_run=False`.
- **Order warnings** — only a conservative allowlist (price cap, cash-quantity,
  mandatory cap, "without market data") is auto-acked; anything else halts the order
  for manual review.
