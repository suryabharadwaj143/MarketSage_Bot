from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
import yfinance as yf
import ta
import feedparser
import re
import requests
import os
from datetime import datetime, timedelta

# 🔐 TOKEN FROM RENDER ENV VARIABLE
BOT_TOKEN = os.getenv("BOT_TOKEN")


# --------------------------------------------------
# Utility: Clean HTML
# --------------------------------------------------
def clean_html(raw_html):
    clean_text = re.sub('<.*?>', '', raw_html)
    clean_text = clean_text.replace('\n', ' ').strip()
    clean_text = clean_text.replace('&nbsp;', ' ')
    return clean_text


# --------------------------------------------------
# NSE Corporate Actions (Last 3)
# --------------------------------------------------
def fetch_nse_corporate_actions(symbol):
    try:
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com/"
        }

        session.get("https://www.nseindia.com", headers=headers, timeout=5)

        url = f"https://www.nseindia.com/api/corporates-corporateActions?index=equities&symbol={symbol}"
        response = session.get(url, headers=headers, timeout=5)

        if response.status_code != 200:
            return "Corporate actions temporarily unavailable."

        data = response.json()
        if not data:
            return "No recent corporate actions."

        sorted_data = sorted(
            data,
            key=lambda x: datetime.strptime(x.get("exDate", "01-Jan-1900"), "%d-%b-%Y"),
            reverse=True
        )

        section = ""
        for item in sorted_data[:3]:
            section += (
                f"📌 {item.get('subject','N/A')}\n"
                f"   📅 Ex-Date: {item.get('exDate','N/A')}\n"
                f"   📆 Record Date: {item.get('recDate','N/A')}\n\n"
            )

        return section

    except Exception as e:
        print("Corporate Action Error:", e)
        return "Corporate actions temporarily unavailable."


# --------------------------------------------------
# NSE Corporate Announcements (Top 5)
# --------------------------------------------------
def fetch_nse_announcements(symbol):
    try:
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com/"
        }

        session.get("https://www.nseindia.com", headers=headers, timeout=5)

        url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}"
        response = session.get(url, headers=headers, timeout=5)

        if response.status_code != 200:
            return "Announcements temporarily unavailable."

        data = response.json()
        if not data:
            return "No recent announcements."

        sorted_data = sorted(
            data,
            key=lambda x: x.get("sort_date", ""),
            reverse=True
        )

        section = ""
        for item in sorted_data[:5]:
            desc = item.get("desc", "N/A")
            text = item.get("attchmntText", "")
            date = item.get("an_dt", "N/A")

            short_text = text[:120] + "..." if len(text) > 120 else text

            section += (
                f"📣 {desc}\n"
                f"   📅 {date.split()[0]}\n"
                f"   📝 {short_text}\n\n"
            )

        return section

    except Exception as e:
        print("Announcement Error:", e)
        return "Announcements temporarily unavailable."


# --------------------------------------------------
# Hybrid News Engine (Yahoo + Google Fallback)
# --------------------------------------------------
def fetch_news(symbol, ticker):
    cutoff_date = datetime.now() - timedelta(days=30)
    section = ""
    count = 0

    # Step 1: Yahoo Finance
    try:
        news_items = ticker.news or []

        for item in news_items:
            if count >= 3:
                break

            publish_time = None

            if "providerPublishTime" in item:
                publish_time = datetime.fromtimestamp(item["providerPublishTime"])
            elif "published" in item:
                try:
                    publish_time = datetime.strptime(item["published"], "%Y-%m-%dT%H:%M:%SZ")
                except:
                    continue
            else:
                continue

            if publish_time < cutoff_date:
                continue

            section += (
                f"📰 {item.get('title','')}\n"
                f"   🏷 {item.get('publisher','Unknown')}\n"
                f"   📅 {publish_time.strftime('%d %b %Y')}\n\n"
            )

            count += 1

    except:
        pass

    # Step 2: Google RSS fallback
    if section == "":
        try:
            news_url = f"https://news.google.com/rss/search?q={symbol}+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en"
            feed = feedparser.parse(news_url)

            for entry in feed.entries:
                if count >= 3:
                    break

                title_clean = clean_html(entry.title)

                if symbol not in title_clean.upper():
                    continue

                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published_date = datetime(*entry.published_parsed[:6])

                    if published_date < cutoff_date:
                        continue

                    source = entry.get("source", {}).get("title", "Unknown")

                    section += (
                        f"📰 {title_clean}\n"
                        f"   🏷 {source}\n"
                        f"   📅 {published_date.strftime('%d %b %Y')}\n\n"
                    )

                    count += 1

        except:
            pass

    if section == "":
        section = "No relevant stock news in last 30 days."

    return section


# --------------------------------------------------
# Main Bot Handler
# --------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = update.message.text.strip().upper()

    try:
        ticker = yf.Ticker(f"{symbol}.NS")

        # SAFE FIX ONLY
        company_name = symbol

        try:
            fast_info = ticker.fast_info
        except:
            pass
            
        try:
            info = ticker.get_info()
            company_name = info.get("longName", symbol)
        except:
            pass

        daily_data = ticker.history(period="6mo", interval="1d")

        if daily_data.empty:
            await update.message.reply_text("Invalid NSE Symbol ❌")
            return

        daily_data["EMA20"] = ta.trend.ema_indicator(daily_data["Close"], window=20)
        daily_data["EMA50"] = ta.trend.ema_indicator(daily_data["Close"], window=50)
        daily_data["RSI"] = ta.momentum.rsi(daily_data["Close"], window=14)

        latest = daily_data.iloc[-1]

        weekly_data = daily_data.resample("W").last()
        weekly_data["EMA20"] = ta.trend.ema_indicator(weekly_data["Close"], window=20)
        latest_weekly = weekly_data.iloc[-1]

        daily_trend = "🟢 Bullish" if latest["Close"] > latest["EMA20"] and latest["Close"] > latest["EMA50"] else "🔴 Bearish"
        weekly_trend = "🟢 Bullish" if latest_weekly["Close"] > latest_weekly["EMA20"] else "🔴 Bearish"

        previous_close = daily_data["Close"].iloc[-2]
        percent_change = ((latest["Close"] - previous_close) / previous_close) * 100

        news_section = fetch_news(symbol, ticker)
        actions_section = fetch_nse_corporate_actions(symbol)
        announcements_section = fetch_nse_announcements(symbol)

        message = (
            f"📊 {company_name} ({symbol} - NSE)\n\n"
            f"💰 Price: ₹{latest['Close']:.2f} ({percent_change:.2f}%)\n"
            f"📦 Volume: {int(latest['Volume']):,}\n\n"
            f"📈 EMA20: ₹{latest['EMA20']:.2f}\n"
            f"📉 EMA50: ₹{latest['EMA50']:.2f}\n"
            f"⚡ RSI(14): {latest['RSI']:.2f}\n\n"
            f"📊 Daily Trend: {daily_trend}\n"
            f"🚀 Weekly Trend: {weekly_trend}\n\n"
            f"📰 Latest News:\n{news_section}\n\n"
            f"🏢 Corporate Actions:\n{actions_section}\n\n"
            f"📣 Corporate Announcements:\n{announcements_section}"
        )

        await update.message.reply_text(message)

    except Exception as e:
        print("Main error:", e)
        await update.message.reply_text("Error fetching stock data ❌")


# --------------------------------------------------
# Run in Webhook Mode (Render)
# --------------------------------------------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    PORT = int(os.environ.get("PORT", 8000))
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

    print("Bot starting in webhook mode...")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL
    )
