"""
Run this once after your Render service is live, to tell Telegram where to send messages.

Usage:
    python set_webhook.py <BOT_TOKEN> <RENDER_URL>

Example:
    python set_webhook.py 123456:ABC-DEF https://poultry-bot.onrender.com
"""
import sys
import requests

def main():
    if len(sys.argv) != 3:
        print("Usage: python set_webhook.py <BOT_TOKEN> <RENDER_URL>")
        sys.exit(1)

    bot_token = sys.argv[1]
    render_url = sys.argv[2].rstrip("/")
    webhook_url = f"{render_url}/webhook"

    resp = requests.get(
        f"https://api.telegram.org/bot{bot_token}/setWebhook",
        params={"url": webhook_url},
    )
    print(resp.json())

if __name__ == "__main__":
    main()
