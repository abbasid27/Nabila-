#!/usr/bin/env python3
"""
Grok DEX Profit Edge Scanner v2 - Telegram Bot
Improved version with better scoring + simple watchlist
"""

import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, CallbackQueryHandler
)
from grok_dex_edge import analyze, fetch_dexscreener, compute_profit_edge, generate_links

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Simple in-memory watchlist (per user) - resets on bot restart
# For production you would use a database or Redis
WATCHLIST = {}  # {user_id: [{"chain": ..., "token": ..., "symbol": ..., "added": ...}]}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
🚀 *Grok DEX Profit Edge Scanner v2*

Your unique on-chain edge tool for profitable DEX trading.

*Commands:*
/start - This message
/help - How to use
/analyze `<chain> <token>` - Full analysis
/watch `<chain> <token>` - Add to your personal watchlist
/watchlist - Show your watched tokens
/unwatch `<token>` - Remove from watchlist
/score - Quick tip on reading the score

*Quick usage:*
Just send:
`solana TOKEN_ADDRESS`
`base 0x...`
`ethereum 0x...`

I return Profit Edge Score, risk level, buy pressure, age, volume quality + direct links to GMGN (Smart Money/KOL), Deep Blue Alpha, Arkham, Bubblemaps...

Ready. Drop a token.
"""
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def score_explain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
📊 *How to read the Profit Edge Score*

*80–100 (A+)* → Strong edge setup. Good liquidity + volume conviction + buy pressure + healthy age. Still check GMGN/Arkham for smart money.

*70–79 (A)* → Solid. Worth deeper look on smart money tools.

*55–69 (B)* → Decent / watchlist material. Needs confirmation from whales/KOLs.

*40–54 (C)* → Neutral. Only trade if clear smart money signal.

*<40 (D/F)* → Weak or high risk. Usually skip unless you have strong external conviction.

*Risk Level* is calculated separately from liquidity, age, sell pressure and dumps.

Always combine the score with:
• GMGN smart money / KOL feed
• Bubblemaps holder clusters
• Arkham entity labels
"""
    await update.message.reply_text(text, parse_mode="Markdown")


async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: `/analyze solana TOKEN_ADDRESS`", parse_mode="Markdown")
        return
    chain = context.args[0]
    token = context.args[1]
    await run_analysis(update, chain, token)


async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: `/watch solana TOKEN_ADDRESS`", parse_mode="Markdown")
        return

    chain = context.args[0].lower()
    token = context.args[1]

    pair = fetch_dexscreener(chain, token)
    symbol = "?"
    if pair:
        symbol = pair.get("baseToken", {}).get("symbol", "?")

    if user_id not in WATCHLIST:
        WATCHLIST[user_id] = []

    # Avoid duplicates
    for item in WATCHLIST[user_id]:
        if item["token"].lower() == token.lower():
            await update.message.reply_text(f"Already watching {symbol}.")
            return

    WATCHLIST[user_id].append({
        "chain": chain,
        "token": token,
        "symbol": symbol,
        "added": datetime.utcnow().isoformat()
    })
    await update.message.reply_text(f"✅ Added *{symbol}* (`{token[:8]}...`) on {chain} to your watchlist.\nUse /watchlist to see all.", parse_mode="Markdown")


async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items = WATCHLIST.get(user_id, [])
    if not items:
        await update.message.reply_text("Your watchlist is empty.\nAdd tokens with `/watch solana TOKEN`", parse_mode="Markdown")
        return

    text = "👀 *Your Watchlist*\n\n"
    for i, item in enumerate(items, 1):
        text += f"{i}. *{item['symbol']}* ({item['chain']})\n`{item['token']}`\n\n"
    text += "Send the chain + address again to re-analyze any of them."
    await update.message.reply_text(text, parse_mode="Markdown")


async def unwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: `/unwatch TOKEN_ADDRESS`", parse_mode="Markdown")
        return
    token = context.args[0].lower()
    items = WATCHLIST.get(user_id, [])
    new_items = [x for x in items if x["token"].lower() != token]
    if len(new_items) == len(items):
        await update.message.reply_text("Token not found in your watchlist.")
        return
    WATCHLIST[user_id] = new_items
    await update.message.reply_text("✅ Removed from watchlist.")


async def run_analysis(update: Update, chain: str, token: str):
    msg = await update.message.reply_text("🔍 Scanning on-chain data + computing Profit Edge Score...")

    try:
        report = analyze(chain, token)
        if len(report) > 4000:
            report = report[:3900] + "\n\n...(truncated)"

        pair = fetch_dexscreener(chain, token)
        keyboard = []
        if pair:
            links = generate_links(chain, token, pair)
            buttons = []
            # Prioritize the most useful links
            priority = ["GMGN Smart Money + KOL", "GMGN", "Deep Blue Alpha (Whales)", "Bubblemaps", "Arkham", "DexScreener"]
            for name in priority:
                if name in links:
                    buttons.append(InlineKeyboardButton(name[:22], url=links[name]))
            # Add remaining
            for name, url in links.items():
                if name not in priority:
                    buttons.append(InlineKeyboardButton(name[:22], url=url))

            for i in range(0, min(len(buttons), 6), 2):
                keyboard.append(buttons[i:i+2])

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await msg.edit_text(report, reply_markup=reply_markup)
    except Exception as e:
        logger.exception(e)
        await msg.edit_text(f"❌ Error: {str(e)[:300]}")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) >= 2:
        chain = parts[0].lower()
        token = parts[1]
        # Basic sanity
        if len(token) >= 20:
            await run_analysis(update, chain, token)
            return
    await update.message.reply_text(
        "Send in format:\n`solana TOKEN_ADDRESS`\nor use /analyze /watch /watchlist",
        parse_mode="Markdown"
    )


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("=" * 60)
        print("Missing TELEGRAM_BOT_TOKEN")
        print()
        print("1. Talk to @BotFather on Telegram → /newbot")
        print("2. Copy the token")
        print("3. Run:")
        print('   export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."')
        print("   python telegram_bot.py")
        print("=" * 60)
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("score", score_explain))
    app.add_handler(CommandHandler("analyze", analyze_cmd))
    app.add_handler(CommandHandler("watch", watch_cmd))
    app.add_handler(CommandHandler("watchlist", watchlist_cmd))
    app.add_handler(CommandHandler("unwatch", unwatch_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("🤖 Grok DEX Profit Edge Bot v2 is live...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
