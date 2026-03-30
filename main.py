#!/usr/bin/env python3

import os
import json
import time
import asyncio
import urllib.request
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API = "https://cyb3rr00t.tech/api/trx-data"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}

auto_chats = set()
last_issue = ""

# ─────────────────────────────
# FETCH
# ─────────────────────────────

def fetch():
    url = f"{API}?t={int(time.time()*1000)}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

async def fetch_async():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fetch)

# ─────────────────────────────
# HELPERS
# ─────────────────────────────

def result_of(n):
    return "BIG" if n >= 5 else "SMALL"

def short(s):
    return s[-6:]

# ─────────────────────────────
# BUILD MODEL (WINRATE PER KEY)
# ─────────────────────────────

def build_model(history):
    model = defaultdict(lambda: {"BIG": 0, "SMALL": 0})

    for i in range(len(history) - 1):
        curr = history[i]
        nxt = history[i + 1]

        h = curr.get("hash", "") or curr.get("blockHash", "")
        if not h:
            continue

        num = int(curr["number"])
        digit = h[-1].lower()

        key = f"{num}_{digit}"

        nxt_res = result_of(int(nxt["number"]))
        model[key][nxt_res] += 1

    return model

# ─────────────────────────────
# PREDICT
# ─────────────────────────────

def predict(history, model):
    curr = history[-1]

    num = int(curr["number"])
    h = curr.get("hash", "") or curr.get("blockHash", "")

    if not h:
        return "VOID", 0, 0, "NO_HASH"

    digit = h[-1].lower()
    key = f"{num}_{digit}"

    stats = model.get(key)

    if not stats:
        return "VOID", 0, 0, key

    big = stats["BIG"]
    small = stats["SMALL"]
    total = big + small

    if total < 3:
        return "VOID", 50, total, key

    if big > small:
        return "BIG", int(big/total*100), total, key
    else:
        return "SMALL", int(small/total*100), total, key

# ─────────────────────────────
# TELEGRAM COMMAND
# ─────────────────────────────

async def cmd_predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Loading...")

    try:
        history = await fetch_async()

        model = build_model(history)
        direction, confidence, samples, key = predict(history, model)

        last = history[-1]
        num = int(last["number"])
        res = result_of(num)

        text = (
            f"🤖 *HASH AI BOT*\n\n"
            f"Key: `{key}`\n"
            f"Issue: `{short(last['issueNumber'])}`\n"
            f"Result: `{num}` → *{res}*\n\n"
            f"Prediction: *{direction}*\n"
            f"Confidence: *{confidence}%*\n"
            f"Samples: `{samples}`\n\n"
            f"_Auto-learning hash model_"
        )

        await msg.delete()
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await msg.edit_text(f"Error: {e}")

# ─────────────────────────────
# AUTO WATCHER (ONLY STRONG)
# ─────────────────────────────

async def watcher(context: ContextTypes.DEFAULT_TYPE):
    global last_issue

    try:
        history = await fetch_async()
        latest = history[-1]["issueNumber"]

        if latest == last_issue:
            return

        last_issue = latest

        model = build_model(history)
        direction, confidence, samples, key = predict(history, model)

        # 🎯 ONLY STRONG SIGNALS
        if samples < 5 or confidence < 60:
            return

        last = history[-1]
        num = int(last["number"])
        res = result_of(num)

        text = (
            f"🚨 *STRONG SIGNAL*\n\n"
            f"Key: `{key}`\n"
            f"Issue: `{short(last['issueNumber'])}`\n"
            f"Result: `{num}` → *{res}*\n\n"
            f"Next: *{direction}* ({confidence}%)\n"
            f"Samples: `{samples}`"
        )

        for chat_id in auto_chats:
            await context.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        print("Watcher error:", e)

# ─────────────────────────────
# COMMANDS
# ─────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    auto_chats.add(update.message.chat_id)
    await update.message.reply_text("✅ Bot Started — Auto alerts ON")

# ─────────────────────────────
# MAIN
# ─────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("predict", cmd_predict))

    app.job_queue.run_repeating(watcher, interval=15, first=5)

    print("🚀 Hash AI Bot Running...")
    app.run_polling()

if __name__ == "__main__":
    main()
