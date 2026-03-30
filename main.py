#!/usr/bin/env python3
"""
CYB3R R00T TRX Prediction Telegram Bot
Data source: cyb3rr00t.tech (fetches 6lottery WinTRX game data)
BIG = number 5-9 | SMALL = number 0-4
Alerts fire on every NEW issue detected (not a fixed timer).
"""

import os
import json
import time
import logging
import asyncio
import urllib.request
from collections import Counter
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SITE_BASE = "https://cyb3rr00t.tech"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": SITE_BASE + "/",
    "Accept": "application/json",
}

# State
auto_alert_chats: set = set()
last_seen_issue: str = ""          # last issue we already sent an alert for
last_analysis: dict = {}


# ──────────────────────────────────────────────────
# DATA FETCHING
# ──────────────────────────────────────────────────

def fetch_json(url: str):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_site_data() -> dict:
    t = int(time.time() * 1000)
    history = fetch_json(f"{SITE_BASE}/api/trx-data?t={t}")
    try:
        signals = fetch_json(f"{SITE_BASE}/api/get-signals")
    except Exception:
        signals = []
    return {"history": history, "signals": signals}


async def fetch_async():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fetch_site_data)


# ──────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────

def short_issue(s: str) -> str:
    return s[-6:] if len(s) >= 6 else s


def result_of(n: int) -> str:
    return "BIG" if n >= 5 else "SMALL"


def dir_emoji(d: str) -> str:
    return {"BIG": "🟢 BIG", "SMALL": "🔴 SMALL", "VOID": "⚪ VOID"}.get(d, d)


# ──────────────────────────────────────────────────
# ANALYSIS ENGINE
# ──────────────────────────────────────────────────

def detect_patterns(results: list) -> list:
    patterns = []
    if len(results) < 6:
        return ["Not enough data"]

    recent = results[-20:]

    # Current streak
    streak = 1
    for r in reversed(recent[:-1]):
        if r == recent[-1]:
            streak += 1
        else:
            break
    if streak >= 4:
        patterns.append(f"{recent[-1]} Streak ×{streak} ⚠️ Reversal Risk")
    elif streak == 3:
        patterns.append(f"{recent[-1]} Streak ×3 — Watch Reversal")

    # Alternating
    alt_count = sum(1 for i in range(1, min(10, len(recent))) if recent[-i] != recent[-i - 1])
    alt_rate = alt_count / max(1, min(9, len(recent) - 1))
    if alt_rate >= 0.8:
        patterns.append("Zigzag (Alternating Pattern)")
    elif alt_rate <= 0.2:
        patterns.append("Repeating (Same-Side Pattern)")

    # Double pairs (BB/SS)
    last8 = recent[-8:] if len(recent) >= 8 else recent
    pairs = [last8[i] == last8[i + 1] for i in range(0, len(last8) - 1, 2)]
    if len(pairs) >= 3 and all(pairs):
        patterns.append("Double Pattern (BB/SS Pairs)")

    # Last-10 bias
    last10 = results[-10:]
    big10 = last10.count("BIG")
    if big10 >= 8:
        patterns.append("Heavy BIG Bias (Last 10)")
    elif big10 <= 2:
        patterns.append("Heavy SMALL Bias (Last 10)")
    elif big10 >= 7:
        patterns.append("BIG Dominant (Last 10)")
    elif big10 <= 3:
        patterns.append("SMALL Dominant (Last 10)")

    return patterns if patterns else ["Balanced / No Clear Pattern"]


def analyze_data(history: list, signals: list) -> dict:
    if not history:
        return {}

    recent = history[-100:]
    numbers = [int(h["number"]) for h in recent]
    results = [result_of(n) for n in numbers]

    last = recent[-1]
    current_issue = last["issueNumber"]
    next_issue_display = str(int(short_issue(current_issue)) + 1).zfill(6)
    current_number = int(last["number"])
    current_result = result_of(current_number)
    block_time = last.get("blockTime", "—")

    # Frequency over last 100
    total = len(results)
    big_count = results.count("BIG")
    big_pct = int(big_count / total * 100)
    small_pct = 100 - big_pct

    last5 = results[-5:]
    last10 = results[-10:]
    last20 = results[-20:]
    big5  = last5.count("BIG")
    big10 = last10.count("BIG")
    big20 = last20.count("BIG")

    # Streak
    streak = 1
    for r in reversed(results[:-1]):
        if r == results[-1]:
            streak += 1
        else:
            break
    streak_dir = results[-1]

    # Alternation rate last 10
    alt = sum(1 for i in range(1, min(10, len(results))) if results[-i] != results[-i - 1]) / max(1, min(9, len(results) - 1))

    patterns = detect_patterns(results)

    # ── Prediction Voting ─────────────────────────
    bull = 0   # votes for BIG
    bear = 0   # votes for SMALL
    total_votes = 0

    def vote(b, s):
        nonlocal bull, bear, total_votes
        bull += b
        bear += s
        total_votes += b + s

    # Streak reversal signal
    if streak >= 5:
        vote(3 if streak_dir == "SMALL" else 0, 3 if streak_dir == "BIG" else 0)
    elif streak >= 3:
        vote(1 if streak_dir == "SMALL" else 0, 1 if streak_dir == "BIG" else 0)

    # Alternating: follow the pattern
    if alt >= 0.7:
        opp = "SMALL" if results[-1] == "BIG" else "BIG"
        vote(2 if opp == "BIG" else 0, 2 if opp == "SMALL" else 0)

    # Last-5 bias
    if big5 >= 4:
        vote(0, 1)
    elif big5 <= 1:
        vote(1, 0)

    # Last-10 bias
    if big10 >= 8:
        vote(0, 2)
    elif big10 <= 2:
        vote(2, 0)
    elif big10 >= 6:
        vote(0, 1)
    elif big10 <= 4:
        vote(1, 0)

    # Last-20 balance
    if big20 >= 15:
        vote(0, 1)
    elif big20 <= 5:
        vote(1, 0)

    # Overall 100-issue frequency
    if big_pct >= 60:
        vote(0, 1)
    elif big_pct <= 40:
        vote(1, 0)

    # Number-value momentum (avg last 5)
    avg5 = sum(numbers[-5:]) / 5
    if avg5 >= 6.5:
        vote(0, 1)
    elif avg5 <= 3.5:
        vote(1, 0)

    # Site signal (trusted, weight ×3)
    site_direction = site_confidence = site_issue_display = None
    if signals:
        sig = signals[0]
        site_direction   = sig.get("direction")
        site_confidence  = sig.get("confidence")
        site_issue_display = short_issue(sig.get("issue", ""))
        vote(3 if site_direction == "BIG" else 0, 3 if site_direction == "SMALL" else 0)

    # ── Final verdict ─────────────────────────────
    net = bull - bear
    raw_pct = abs(net) / max(total_votes, 1)
    confidence = int(50 + raw_pct * 45)
    confidence = max(52, min(92, confidence))

    if net > 2:
        direction, void_issues = "BIG", 1
    elif net < -2:
        direction, void_issues = "SMALL", 1
    elif net > 0:
        direction, void_issues = "BIG", 2
        confidence = max(52, confidence - 5)
    elif net < 0:
        direction, void_issues = "SMALL", 2
        confidence = max(52, confidence - 5)
    else:
        direction, void_issues, confidence = "VOID", 3, 50

    # Visual history
    vis = "".join("🟢" if r == "BIG" else "🔴" for r in results[-20:])

    # Hot / cold (last 50)
    freq = Counter(numbers[-50:])
    hot  = sorted(freq, key=lambda x: -freq[x])[:3]
    cold = sorted(freq, key=lambda x:  freq[x])[:3]

    return {
        "current_issue":   short_issue(current_issue),
        "next_issue":      next_issue_display,
        "current_number":  current_number,
        "current_result":  current_result,
        "block_time":      block_time,
        "direction":       direction,
        "confidence":      confidence,
        "void_issues":     void_issues,
        "big_pct":         big_pct,
        "small_pct":       small_pct,
        "big5": big5, "big10": big10, "big20": big20,
        "streak":          streak,
        "streak_dir":      streak_dir,
        "alt_rate":        alt,
        "patterns":        patterns,
        "history_visual":  vis,
        "hot_numbers":     hot,
        "cold_numbers":    cold,
        "site_direction":  site_direction,
        "site_confidence": site_confidence,
        "site_issue":      site_issue_display,
        "bull_votes":      bull,
        "bear_votes":      bear,
        "total_votes":     total_votes,
        "timestamp":       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


# ──────────────────────────────────────────────────
# MESSAGE BUILDER
# ──────────────────────────────────────────────────

def build_message(data: dict, title: str = "PREDICTION") -> str:
    if not data:
        return "❌ Could not load data from cyb3rr00t.tech"

    bar_total = 20
    big_bars   = int(data["big_pct"] / 100 * bar_total)
    small_bars = bar_total - big_bars
    big_bar   = "🟩" * big_bars   + "⬜" * small_bars
    small_bar = "🟥" * small_bars + "⬜" * big_bars

    patterns_str = "\n".join(f"   • {p}" for p in data["patterns"])
    streak_str   = f"{data['streak_dir']} ×{data['streak']}"
    alt_str      = f"{int(data['alt_rate'] * 100)}% alternating"
    hot_str  = " ".join(str(n) for n in data["hot_numbers"])
    cold_str = " ".join(str(n) for n in data["cold_numbers"])

    site_str = ""
    if data["site_direction"]:
        site_str = (
            f"\n🌐 *Site Signal (Issue {data['site_issue']})*\n"
            f"   Direction: *{dir_emoji(data['site_direction'])}*\n"
            f"   Confidence: *{data['site_confidence']}%*"
        )

    return (
        f"╔══════════════════════════╗\n"
        f"║  🤖 *CYB3R R00T {title}*\n"
        f"╚══════════════════════════╝\n\n"
        f"📋 *Issue:*  `{data['current_issue']}` → Next: `{data['next_issue']}`\n"
        f"🎲 *Result:* `{data['current_number']}` → *{data['current_result']}*\n"
        f"🕐 `{data['block_time']}`\n\n"
        f"━━━━━━ NEXT PREDICTION ━━━━━━\n"
        f"Direction:   *{dir_emoji(data['direction'])}*\n"
        f"Confidence:  *{data['confidence']}%*\n"
        f"Valid for:   *{data['void_issues']} issue(s)*\n"
        f"{site_str}\n\n"
        f"━━━━━━ BIG / SMALL RATIO ━━━━━━\n"
        f"BIG   {big_bar} {data['big_pct']}%\n"
        f"SMALL {small_bar} {data['small_pct']}%\n\n"
        f"━━━━━━ RECENT RESULTS ━━━━━━\n"
        f"{data['history_visual']}\n"
        f"🟢=BIG  🔴=SMALL  (last 20)\n\n"
        f"━━━━━━ TREND ANALYSIS ━━━━━━\n"
        f"Last 5:   BIG {data['big5']}/5   SMALL {5  - data['big5']}/5\n"
        f"Last 10:  BIG {data['big10']}/10  SMALL {10 - data['big10']}/10\n"
        f"Last 20:  BIG {data['big20']}/20  SMALL {20 - data['big20']}/20\n"
        f"Streak:   *{streak_str}*\n"
        f"Pattern:  {alt_str}\n\n"
        f"━━━━━━ CHART PATTERNS ━━━━━━\n"
        f"{patterns_str}\n\n"
        f"━━━━━━ HOT / COLD NUMBERS ━━━━━━\n"
        f"🔥 Hot:  `{hot_str}`\n"
        f"❄️ Cold: `{cold_str}`\n\n"
        f"━━━━━━ SIGNAL SCORE ━━━━━━\n"
        f"🟢 BIG {data['bull_votes']} pts  |  🔴 SMALL {data['bear_votes']} pts  |  Total {data['total_votes']}\n\n"
        f"_Source: cyb3rr00t.tech / 6lottery WinTRX_"
    )


# ──────────────────────────────────────────────────
# KEYBOARD
# ──────────────────────────────────────────────────

def get_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔮 Predict Now",    callback_data="predict"),
            InlineKeyboardButton("📊 Full Analysis",  callback_data="analysis"),
        ],
        [
            InlineKeyboardButton("🔔 Auto ON",  callback_data="auto_on"),
            InlineKeyboardButton("🔕 Auto OFF", callback_data="auto_off"),
        ],
        [
            InlineKeyboardButton("📋 Latest Issues", callback_data="issue"),
            InlineKeyboardButton("🔄 Refresh",        callback_data="predict"),
        ],
    ])


# ──────────────────────────────────────────────────
# COMMAND HANDLERS
# ──────────────────────────────────────────────────

async def _reply_target(update: Update):
    if update.message:
        return update.message
    if update.callback_query:
        return update.callback_query.message
    return None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *CYB3R R00T TRX Prediction Bot*\n\n"
        "Data: *cyb3rr00t.tech* (6lottery WinTRX game)\n\n"
        "I watch every new round and send you:\n"
        "• 🟢 BIG / 🔴 SMALL / ⚪ VOID prediction\n"
        "• Confidence % + vote breakdown\n"
        "• BIG/SMALL ratio bars (last 100)\n"
        "• Last 20 results visual\n"
        "• Streak, zigzag & bias patterns\n"
        "• Hot & cold numbers\n"
        "• Site's own signal weighted in\n"
        "• Auto-alert on *every new issue* (not a timer!)\n\n"
        "*Commands:*\n"
        "/predict — Predict right now\n"
        "/analysis — Deep analysis\n"
        "/issue — Last 5 results\n"
        "/auto\\_on — Alert on every new issue\n"
        "/auto\\_off — Stop alerts\n"
        "/status — Bot status\n"
        "/help — This message"
    )
    msg = await _reply_target(update)
    if msg:
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_keyboard())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await _reply_target(update)
    if not msg:
        return
    wait = await msg.reply_text("⏳ Fetching data from cyb3rr00t.tech…")
    try:
        site = await fetch_async()
        data = analyze_data(site["history"], site["signals"])
        global last_analysis
        last_analysis = data
        await wait.delete()
        await msg.reply_text(
            build_message(data, "PREDICTION"),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_keyboard(),
        )
    except Exception as e:
        logger.error(f"predict error: {e}")
        await wait.delete()
        await msg.reply_text(
            f"❌ *Data fetch failed*\n\n`{str(e)[:300]}`",
            parse_mode=ParseMode.MARKDOWN,
        )


async def cmd_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await _reply_target(update)
    if not msg:
        return
    wait = await msg.reply_text("⏳ Running deep analysis…")
    try:
        site = await fetch_async()
        data = analyze_data(site["history"], site["signals"])
        global last_analysis
        last_analysis = data
        await wait.delete()
        await msg.reply_text(
            build_message(data, "FULL ANALYSIS"),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_keyboard(),
        )
    except Exception as e:
        logger.error(f"analysis error: {e}")
        await wait.delete()
        await msg.reply_text(f"❌ Error: `{str(e)[:300]}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_issue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await _reply_target(update)
    if not msg:
        return
    try:
        t = int(time.time() * 1000)
        history = await asyncio.get_event_loop().run_in_executor(
            None, lambda: fetch_json(f"{SITE_BASE}/api/trx-data?t={t}")
        )
        last5 = history[-5:]
        lines = []
        for h in reversed(last5):
            n = int(h["number"])
            r = result_of(n)
            em = "🟢" if r == "BIG" else "🔴"
            lines.append(f"{em} Issue `{short_issue(h['issueNumber'])}` → `{n}` → *{r}*")
        text = (
            "📋 *Latest 5 Issues — 6lottery WinTRX*\n\n"
            + "\n".join(lines)
            + f"\n\n🕐 `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
        )
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_keyboard())
    except Exception as e:
        logger.error(f"issue error: {e}")
        await msg.reply_text(f"❌ Error: `{str(e)[:200]}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_auto_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await _reply_target(update)
    if not msg:
        return
    auto_alert_chats.add(msg.chat_id)
    await msg.reply_text(
        "✅ *Auto-alerts ENABLED*\n\n"
        "I'll send a prediction the moment each new round result appears.\n"
        "Strong signals (≥75% confidence) get highlighted.\n\n"
        "Use /auto\\_off to stop.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_auto_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await _reply_target(update)
    if not msg:
        return
    auto_alert_chats.discard(msg.chat_id)
    await msg.reply_text(
        "🔕 *Auto-alerts DISABLED*\nUse /auto\\_on to re-enable.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await _reply_target(update)
    if not msg:
        return
    subs = len(auto_alert_chats)
    last_issue = last_analysis.get("current_issue", "—")
    last_dir   = last_analysis.get("direction", "—")
    last_conf  = last_analysis.get("confidence", "—")
    text = (
        "📡 *Bot Status*\n\n"
        f"• Auto-alert subscribers: *{subs}*\n"
        f"• Last seen issue: `{last_issue}`\n"
        f"• Last prediction: *{last_dir}* @ *{last_conf}%*\n"
        f"• Polling: every 15 seconds for new issues\n"
        f"• Data source: cyb3rr00t.tech\n"
        f"• Time: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`"
    )
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    handlers = {
        "predict":  cmd_predict,
        "analysis": cmd_analysis,
        "issue":    cmd_issue,
        "auto_on":  cmd_auto_on,
        "auto_off": cmd_auto_off,
    }
    fn = handlers.get(query.data)
    if fn:
        await fn(update, context)


# ──────────────────────────────────────────────────
# NEW-ISSUE WATCHER JOB (fires on every new issue)
# ──────────────────────────────────────────────────

async def new_issue_watcher(context: ContextTypes.DEFAULT_TYPE):
    """
    Polls cyb3rr00t.tech every 15 s.
    Sends prediction alert to all subscribers ONLY when a brand-new
    issue is detected — not on a fixed-time basis.
    """
    global last_seen_issue, last_analysis

    if not auto_alert_chats:
        return

    try:
        site = await fetch_async()
        history  = site["history"]
        signals  = site["signals"]

        if not history:
            return

        latest_issue = history[-1]["issueNumber"]

        # No new issue — skip
        if latest_issue == last_seen_issue:
            return

        # New issue detected!
        last_seen_issue = latest_issue
        data = analyze_data(history, signals)
        last_analysis = data

        is_strong = data["confidence"] >= 75 and data["direction"] != "VOID"

        if is_strong:
            header = (
                f"🚨 *STRONG SIGNAL — Issue {data['current_issue']}*\n"
                f"Direction:  *{dir_emoji(data['direction'])}*\n"
                f"Confidence: *{data['confidence']}%*\n\n"
            )
        else:
            header = (
                f"🔔 *New Issue {data['current_issue']} Result: {data['current_result']}*\n"
                f"Next prediction → *{dir_emoji(data['direction'])}* ({data['confidence']}%)\n\n"
            )

        text = header + build_message(data, "AUTO ALERT")

        for chat_id in list(auto_alert_chats):
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=get_keyboard(),
                )
            except Exception as e:
                logger.warning(f"Send failed for {chat_id}: {e}")

        logger.info(f"New issue {short_issue(latest_issue)} → {data['direction']} {data['confidence']}% — alerted {len(auto_alert_chats)} chats")

    except Exception as e:
        logger.error(f"Watcher error: {e}")


# ──────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set!")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("predict",  cmd_predict))
    app.add_handler(CommandHandler("analysis", cmd_analysis))
    app.add_handler(CommandHandler("issue",    cmd_issue))
    app.add_handler(CommandHandler("auto_on",  cmd_auto_on))
    app.add_handler(CommandHandler("auto_off", cmd_auto_off))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Poll every 15 seconds — fires prediction only on NEW issue
    app.job_queue.run_repeating(new_issue_watcher, interval=15, first=5)

    logger.info("CYB3R R00T TRX Bot started — issue-triggered alerts, 15s poll interval")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
