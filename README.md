# 📡 Opportunity Radar

Twice a day this finds **fully funded scholarships, CV/AI conferences and deadlines, new computer vision papers, and tutorials**, scores each one for *you* with an LLM, sends the best to **Telegram**, and publishes everything to a **web dashboard**. It runs on GitHub Actions for free, so you don't need a server.

```
RSS / arXiv / HF Papers / AI-Deadlines ──► radar.py ──► LLM score + summary ──► Telegram digest
                                                     └──► docs/items.json ──► GitHub Pages dashboard
```

## Setup (about 20 minutes)

### 1. Create the repo
1. Create a new **public** GitHub repo, e.g. `opportunity-radar`. GitHub Pages is free for public repos. Your API keys stay private as secrets either way.
2. Upload every file from this folder, including the hidden `.github/` folder.

### 2. Telegram bot
1. In Telegram, open **@BotFather** → `/newbot` → pick a name → copy the **token**.
2. Send your new bot any message, such as "hi".
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy `"chat":{"id": …}`. That number is your **chat ID**.
   *(For a Telegram channel instead: add the bot as an admin and use `@yourchannel` as the chat ID.)*

### 3. LLM key (pick one)
- **Gemini** (has a free tier): get a key at https://aistudio.google.com/apikey → secret `GEMINI_API_KEY`
- **Claude**: https://console.anthropic.com → secret `ANTHROPIC_API_KEY`

Without a key, the bot still runs and uses keyword scoring instead.

### 4. Add the secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `GEMINI_API_KEY` or `ANTHROPIC_API_KEY`.
Optional: under the **Variables** tab, set `LLM_MODEL` to change the model.

### 5. Turn on the dashboard
Repo → **Settings → Pages** → Source: *Deploy from a branch* → `main` / `/docs` → Save.
Your dashboard will be at `https://<your-username>.github.io/opportunity-radar/`.

### 6. First run
Repo → **Actions → Opportunity Radar → Run workflow**. The first digest arrives in Telegram in about a minute. After that it runs automatically at **07:17 and 18:17 Lagos time**.

## Customize — edit `config.yaml`
- `profile` — who you are. The LLM scores items against this text, so keep it current (for example, once you have an IELTS score or a published paper).
- `min_score` — raise it to 7 for fewer, better alerts.
- `sources` — add any RSS feed (university news, a lab's blog, a Medium tag `https://medium.com/feed/tag/computer-vision`) or another arXiv query.
- `keywords` — a cheap pre-filter that drops irrelevant posts before the LLM sees them.

## What you get
- 🎓 Scholarships tagged 💰 *Fully funded*, with deadlines pulled out
- ⏰ A one-time **"closing within 14 days"** reminder for scholarships you've already been sent
- 🗓 CV/ML/robotics conferences with their next submission deadline (expired ones are hidden)
- 📄 New arXiv cs.CV papers, plus a dedicated **mobile/edge/on-device CV** feed (your research niche)
- 📰 Tutorials from LearnOpenCV, PyImageSearch, Roboflow, Google Research

## Test locally
```bash
pip install -r requirements.txt
python radar.py --dry-run           # prints the digest, sends nothing
python radar.py --dry-run --no-llm  # no API key needed
```

## Later: WhatsApp
WhatsApp requires the Meta WhatsApp Cloud API (a business account, an approved message template, and per-message costs for proactive alerts). `send_telegram()` is the only function you'd need to mirror. Telegram is the better daily channel.
