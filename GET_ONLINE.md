# Get Draftease on a web address

Two paths. Pick based on whether you need it *always on*.

---

## A · Instant public link (easiest — ~2 minutes)

Good for testing and sharing a demo. The link is live while your Mac is awake.

1. Put this folder anywhere (e.g. `~/Downloads/draftease`).
2. Open **Terminal** (⌘-Space → "Terminal").
3. Run:

   ```bash
   cd ~/Downloads/draftease
   bash share.sh
   ```

4. The first time, it installs what it needs (it may ask to install `cloudflared`
   via Homebrew — say yes). Then it prints a line like:

   ```
   https://brave-otter-1234.trycloudflare.com
   ```

   **That is your web address.** Open it in any browser, on any device. Click
   *Create one* to make your account, and you're in.

To stop sharing, press **Ctrl-C** in the Terminal.

> Just want it on your own machine (no public link)? Run `bash start.sh` and open
> http://127.0.0.1:8000 instead.

---

## B · Always-on URL (free hosting — ~10 minutes, one-time)

Good when you want a permanent address that's up even when your Mac is off.

**One-click version:** open **`deploy.html`** (double-click it — it opens in your
browser), paste your GitHub repo URL, and click the **Deploy to Render** button.
That's the whole thing. The manual steps are the same:

1. Create a free account at **https://render.com** and a free **GitHub** account.
2. Put this folder in a new GitHub repository (GitHub Desktop makes this drag-and-drop:
   https://desktop.github.com).
3. Either click the button in `deploy.html`, **or** in Render: **New + → Blueprint →
   pick your repo → Apply**. Render reads the included `render.yaml`, builds the app,
   generates your secret key, and gives you a permanent URL like
   `https://draftease.onrender.com`.
4. Done. To close public signup later, set `DRAFTEASE_ALLOW_SIGNUP=false` in the
   Render dashboard.

Railway (https://railway.app) and Fly.io work the same way off the included
`Dockerfile` if you prefer them.

---

### Why I can't just hand you a finished link

A permanent web address has to live in *your* account (so you own it, the billing,
and the data). I can't create that account or deploy on your behalf — but the steps
above are the whole job, and path A gets you a working public link almost instantly.
