# Trading System OpenClaw Skills

This folder contains 7 OpenClaw skills that wrap the trading system with chat-based monitoring, alerts, and control via Telegram.

## Skills

| Skill | Type | When |
|---|---|---|
| scalper-status | on-demand | User asks about current state |
| trading-alerts | cron (2min, market hours) | Position open/close, P&L thresholds |
| trading-control | on-demand | User commands pause/resume/close |
| morning-briefing | cron (8:45 AM CT daily) | Pre-market summary |
| evening-recap | cron (4:30 PM CT daily) | End-of-day performance review |
| market-watchdog | cron (5min, market hours) | VIX spikes, SPY gaps, headline risk |
| scalper-insights | on-demand | User asks for pattern analysis |

## Setup

1. Ensure OpenClaw is running and Telegram is connected
2. The skills auto-load from this workspace folder
3. Set up cron schedules for the background skills:
   - `trading-alerts`: every 2 minutes, Mon-Fri 8:30 AM – 3:00 PM CT
   - `market-watchdog`: every 5 minutes, Mon-Fri 8:30 AM – 3:00 PM CT
   - `morning-briefing`: 8:45 AM CT, Mon-Fri
   - `evening-recap`: 4:30 PM CT, Mon-Fri

Use `openclaw cron add` to register each scheduled skill — the OpenClaw agent itself can set these up if you tell it to.

## Testing

After installation, message your Telegram bot:
- "How's the scalper?" → triggers scalper-status
- "Show me recent logs" → triggers trading-control (read-only)
- "Analyze my last 20 trades" → triggers scalper-insights

## Config

See `config.json` for shared paths and alert thresholds.
