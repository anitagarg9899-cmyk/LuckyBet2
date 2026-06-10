# LuckyBet2 — Discord Casino Bot

A Discord bot offering virtual-currency casino games including Crash, Blackjack, Mines, Coin Flip, Slots, and Roulette. Uses a "Provably Fair" system, rank-based roles, and dynamic image generation for game results.

## Setup

1. Add your `DISCORD_TOKEN` secret in the Secrets tab (Discord Developer Portal → Bot → Token).
2. Run the **Start application** workflow to launch the bot.

## Project Layout

- `bot.py` — Main entry point; all game commands and bot logic.
- `images.py` — Pillow-based image card generation for game results.
- `bot/user_data.json` — Local JSON database for user balances, stats, and configuration.
- `requirements.txt` — Python dependencies (discord.py, python-dotenv, Pillow).

## Running

```
python bot.py
```

Requires `DISCORD_TOKEN` environment secret to be set.

## User preferences

