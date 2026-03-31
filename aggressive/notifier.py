"""
Trade Notification System.
Sends alerts via console log and optionally email.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

load_dotenv()


class TradeNotifier:

    def __init__(self):
        self.email_from = os.getenv("NOTIFY_EMAIL_FROM", "")
        self.email_to = os.getenv("NOTIFY_EMAIL_TO", "")
        self.email_pass = os.getenv("NOTIFY_EMAIL_PASS", "")
        self.smtp_host = os.getenv("NOTIFY_SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("NOTIFY_SMTP_PORT", "587"))
        self.enabled = bool(self.email_from and self.email_to and self.email_pass)

    def notify_entry(self, direction, symbol, strike, dte, qty, price, paper=True):
        mode = "PAPER" if paper else "LIVE"
        msg = (
            f"[{mode} ENTRY] {direction} {symbol} "
            f"${strike} {dte}DTE x{qty} @ ${price:.2f}"
        )
        logger.info(msg)
        if self.enabled:
            self._send_email(f"Trade Entry: {direction} {symbol}", msg)

    def notify_exit(self, symbol, pnl, reason, paper=True):
        mode = "PAPER" if paper else "LIVE"
        result = "WIN" if pnl > 0 else "LOSS"
        msg = (
            f"[{mode} EXIT] {symbol} ${pnl:+,.2f} "
            f"({result}) reason={reason}"
        )
        logger.info(msg)
        if self.enabled:
            self._send_email(f"Trade Exit: {symbol} ${pnl:+,.2f}", msg)

    def notify_daily_summary(self, summary):
        lines = [
            f"Daily Summary - {datetime.now().strftime('%Y-%m-%d')}",
            f"Cash: ${summary.get('cash', 0):,.2f}",
            f"Deployed: ${summary.get('deployed', 0):,.2f}",
            f"Open: {summary.get('open_positions', 0)}",
            f"Closed: {summary.get('closed_trades', 0)}",
            f"P&L: ${summary.get('total_pnl', 0):+,.2f}",
        ]
        msg = "\n".join(lines)
        logger.info(msg)
        if self.enabled:
            self._send_email("Daily Trading Summary", msg)

    def _send_email(self, subject, body):
        try:
            msg = MIMEMultipart()
            msg["From"] = self.email_from
            msg["To"] = self.email_to
            msg["Subject"] = f"TradingBot: {subject}"
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.email_from, self.email_pass)
                server.send_message(msg)
        except Exception as e:
            logger.debug(f"Email failed: {e}")
