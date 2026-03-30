
   #!/usr/bin/env python3

import os
import json
import time
import logging
import asyncio
import urllib.request
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

# ────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SITE_BASE = "https://cyb3rr00t.tech"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": SITE_BASE + "/",
    "Accept": "application/json",
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

auto_alert_chats = set()
last_seen_issue = ""

# ────────────────────────────────────────────────
# FETCH
# ────────────────────────────────────────────────

def fetch_json(url: str):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

async def fetch_async():
    t = int(time.time() * 1000)
    url = f"{SITE_BASE}/api/trx-data?t={t}"
    return await asyncio.get_event_loop().run_in_executor(None, lambda: fetch_json(url))

# ────────────────────────────────────────────────
# HELPERS
# ────────────────────────────────────────────────

def short_issue(s):
    return s[-6:]

def result_of(n):
    return "BIG" if n >= 5 else "SMALL"

def dir_emoji(d):
    return "🟢 BIG" if d == "BIG" else "🔴 SMALL" if d == "SMALL" else "⚪ VOID"

# ────────────────────────────────────────────────
# HASH AI MODEL
# ────────────────────────────────────────────────

def build_hash_model(history):
    model = {}
    digit_map = {}

    for i in range(len(history) - 1):
        curr = history[i]
        nxt = history[i + 1]

        num = int(curr["number"])
        h = curr.get("hash", "") or curr.get("blockHash", "")
        if not h:
            continue

        digit = h[-1]
        nxt_result = result_of(int(nxt["number"]))

        key = f"{num}_{digit}"

        if key not in model:
            model[key] = {"BIG": 0, "SMALL": 0}
        model[key][nxt_result] += 1

        if digit not in digit_map:
            digit_map[digit] = {"BIG": 0, "SMALL": 0}
        digit_map[digit][nxt_result] += 1

    return model, digit_map


def hash_predict(history, model):
    if not history:
        return "VOID", 0, 50

    curr = history[-1]
    num = int(curr["number"])
    h = curr.get("hash", "") or curr.get("blockHash", "")

    if not h:
        return "VOID", 0, 50

    digit = h[-1]
    key = f"{num}_{digit}"

    if key not in model:
        return "VOID", 0, 50

    big = model[key]["BIG"]
    small = model[key]["SMALL"]
    total = big + small

    if total < 5:
        return "VOID", total, 50

    if big > small:
        return "BIG", total, int(big / total * 100)
    else:
        return "SMALL", total, int(small / total * 100)


def build_heatmap(digit_map):
    lines = []

    for d in sorted(digit_map.keys()):
        big = digit_map[d]["BIG"]
        small = digit_map[d]["SMALL"]
        total = big + small

        if total == 0:
            continue

        big_pct = int(big / total * 100)
        small_pct = 100 - big_pct

        lines.append(f"{d} → 🟢 {big_pct}% | 🔴 {small_pct}%")

    return "\n".join(lines)

# ────────────────────────────────────────────────
# ANALYSIS
# ────────────────────────────────────────────────

def analyze(history):
    model, digit_map = build_hash_model(history)

    direction, samples, confidence = hash_predict(history, model)

    last = history[-1]

    results = [result_of(int(h["number"])) for h in history[-20:]]
    visual = "".join("🟢" if r == "BIG" else "🔴" for r in results)

    return {
        "issue": short_issue(last["issueNumber"]),
        "next": str(int(short_issue(last["issueNumber"])) + 1).zfill(6),
        "num": int(last["number"]),
        "res": result_of(int(last["number"])),
        "direction": direction,
        "confidence": confidence,
        "samples": samples,
        "visual": visual,
        "heatmap": build_heatmap(digit_map),
    }

# ────────────────────────────────────────────────
# MESSAGE
# ────────────────────────────────────────────────

def build_message(d):
    return (
        f"🤖 *HASH AI PREDICT*\n\n"
        f"Issue `{d['issue']}` → `{d['next']}`\n"
        f"Result `{d['num']}` → *{d['res']}*\n\n"
        f"Direction: *{dir_emoji(d['direction'])}*\n"
        f"Confidence: *{d['confidence']}%*\n"
        f"Samples: `{d['samples']}`\n\n"
        f"Last 20:\n{d['visual']}\n\n"
        f"Heatmap:\n{d['heatmap']}"
    )

# ────────────────────────────────────────────────
# TELEGRAM
# ────────────────────────────────────────────────

def keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔮 Predict", callback_data="p")],
        [InlineKeyboardButton("🔔 Auto ON", callback_data="on"),
         InlineKeyboardButton("🔕 Auto OFF", callback_data="off")]
    ])

async def predict(update, context):
    msg = update.message or update.callback_query.message

    wait = await msg.reply_text("⏳ Loading...")
    data = await fetch_async()

    result = analyze(data)
    await wait.delete()

    await msg.reply_text(build_message(result), parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard())

async def start(update, context):
    await update.message.reply_text("HASH AI BOT READY", reply_markup=keyboard())

async def button(update, context):
    q = update.callback_query
    await q.answer()

    if q.data == "p":
        await predict(update, context)
    elif q.data == "on":
        auto_alert_chats.add(q.message.chat_id)
    elif q.data == "off":
        auto_alert_chats.discard(q.message.chat_id)

# ────────────────────────────────────────────────
# WATCHER
# ────────────────────────────────────────────────

async def watcher(context):
    global last_seen_issue

    data = await fetch_async()
    latest = data[-1]["issueNumber"]

    if latest == last_seen_issue:
        return

    last_seen_issue = latest

    result = analyze(data)
    text = build_message(result)

    for chat_id in auto_alert_chats:
        await context.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN)

# ────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("predict", predict))
    app.add_handler(CallbackQueryHandler(button))

    app.job_queue.run_repeating(watcher, interval=15, first=5)

    app.run_polling()

if __name__ == "__main__":
    main()
