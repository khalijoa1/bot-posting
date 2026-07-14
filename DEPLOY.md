# Deploying to Railway

This deploys the bot as a standalone worker (no HTTP port needed) with a persistent disk
for the SQLite database, so it survives redeploys and stays online independent of your PC.

## 1. Create a Railway account and project

1. Go to https://railway.app and sign up (GitHub login is easiest).
2. You don't need to push this code to GitHub — we'll deploy straight from your computer
   using Railway's CLI instead.

## 2. Install the Railway CLI

In PowerShell:
```powershell
iwr https://railway.app/install.ps1 | iex
```
(Alternative if you have Node/npm installed: `npm install -g @railway/cli`)

Then log in (opens a browser to authenticate):
```powershell
railway login
```

## 3. Initialize and deploy the project

From inside this folder (`telegram-poster-bot`):
```powershell
railway init
```
Pick "Create new project" and give it a name (e.g. `telegram-poster-bot`).

```powershell
railway up
```
This builds the `Dockerfile` and deploys it. Railway auto-detects the Dockerfile — no extra config needed.

## 4. Add a persistent volume (so the SQLite database survives redeploys)

1. Open the project on https://railway.app/dashboard, click your service.
2. Go to the **Settings** tab → **Volumes** → **Add Volume**.
3. Set the mount path to `/data`.

## 5. Set environment variables

In the service's **Variables** tab, add:
| Variable | Value |
|---|---|
| `BOT_TOKEN` | your bot token from BotFather |
| `ALLOWED_USER_IDS` | your Telegram user id (comma-separate for more than one) |
| `DATABASE_URL` | `sqlite+aiosqlite:////data/poster.db` |

(Note the volume mount path `/data` matches step 4, and the 4 slashes in the URL are correct —
it's `sqlite+aiosqlite://` + the absolute path `/data/poster.db`.)

Railway redeploys automatically whenever you save variables or run `railway up` again.

## 6. Confirm it's running

```powershell
railway logs
```
You should see the same `Run polling for bot @helpingkhalibot ...` line you saw running locally.
Message the bot on Telegram to confirm it responds — it's now online independent of your PC.

## Redeploying after future code changes

From this folder:
```powershell
railway up
```

## Local vs cloud database

The local `poster.db` file (used when you ran the bot on your PC) is **not** automatically
copied to Railway's volume. If you already registered channels/posts locally and want to keep
them, ask and we can add a step to upload that file to the volume; otherwise the cloud bot starts
with a fresh, empty database.
