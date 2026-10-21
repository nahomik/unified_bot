# 🚀 Unified Bot Service

This project consolidates all **3 bots** into a single Render Web Service:
1. **Poultry Farm Tracker Bot** (Flask Webhook + Web Dashboard)
2. **Back Checker Bot** (Telegram Polling)
3. **Field Checker Bot** (Telegram Polling)

---

## 💡 Why This Setup?

* **100% Free on Render:** Running 3 separate services uses $3 \times 24\text{h} = 72\text{ hours/day}$, depleting Render's 750 free hours in ~10 days. Running all 3 in this unified service uses only $1 \times 24\text{h} = 24\text{ hours/day}$ ($744\text{ hours in a 31-day month} \le 750\text{ free hours}$), so you **never** get suspended.
* **Single Ping Keep-Alive:** One scheduled GitHub Action ping keeps all 3 bots alive 24/7 without consuming extra instance hours.

---

## 📁 Project Structure

```
unified-bot-service/
├── run.py                 # Master supervisor process running all bots
├── app.py                 # Flask web app (Poultry webhook + dashboard + healthz)
├── dashboard.html         # Poultry farm interactive dashboard
├── static/                # Static assets (images)
├── set_webhook.py         # Helper script to register Telegram webhook
├── bots/
│   ├── __init__.py
│   ├── back_checker.py    # Back Checker Telegram Bot
│   └── field_checker.py   # Field Checker Telegram Bot
├── requirements.txt       # Unified Python dependencies
├── Procfile               # Render / PaaS start definition
├── render.yaml            # Render blueprint configuration
└── .github/workflows/
    └── ping.yml           # Single keep-alive ping workflow
```

---

## 🛠️ Environment Variables on Render

In your Render service dashboard under **Environment**:

| Variable | Description |
| :--- | :--- |
| `POULTRY_BOT_TOKEN` | BotFather token for Poultry Bot |
| `POULTRY_SHEET_ID` | Google Sheet ID for poultry tracking |
| `BACK_CHECKER_BOT_TOKEN` | BotFather token for Back Checker Bot |
| `FIELD_CHECKER_BOT_TOKEN` | BotFather token for Field Checker Bot |
| `GOOGLE_CREDS_JSON` | Full JSON service account key (paste as 1 line) |

*(Note: If you already had `BOT_TOKEN` or `SHEET_ID`, they will automatically fall back to those if `POULTRY_*` is not set).*

---

## 🚀 Deploying to Render

1. **Create a GitHub Repository:**
   ```bash
   cd /home/nahom/Documents/Render/unified-bot-service
   git init
   git add .
   git commit -m "Initial commit of unified bot service"
   # Push to your GitHub repo
   ```

2. **Deploy on Render:**
   * Go to **[dashboard.render.com](https://dashboard.render.com)**.
   * Click **New +** → **Web Service** → Connect your repository.
   * **Settings:**
     * **Runtime:** `Python 3`
     * **Build Command:** `pip install -r requirements.txt`
     * **Start Command:** `python run.py`
     * **Instance Type:** `Free`
   * Add the **Environment Variables** listed above.
   * Click **Deploy Web Service**.

3. **Set Webhook for Poultry Bot (One-Time):**
   Once Render gives you your service URL (e.g., `https://your-service.onrender.com`), set the webhook by running:
   ```bash
   python set_webhook.py <POULTRY_BOT_TOKEN> https://your-service.onrender.com
   ```
   *(Or visit `https://api.telegram.org/bot<POULTRY_BOT_TOKEN>/setWebhook?url=https://your-service.onrender.com/webhook` in your browser).*

4. **Keep-Alive (Optional, to prevent 15-min idle sleep):**
   Update `.github/workflows/ping.yml` with your Render service URL and push to GitHub, or add the URL to **UptimeRobot** / **Cron-job.org** to ping `https://your-service.onrender.com/healthz` every 10–14 minutes.
