"""
Alert & Notification Engine.
Email + Slack.
"""

import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

load_dotenv()


class Notifier:

    def __init__(self):
        self.email_on = bool(os.getenv("SMTP_SERVER"))
        self.smtp_srv = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_pass = os.getenv("SMTP_PASSWORD", "")
        self.email_to = os.getenv("ALERT_EMAIL", "")
        self.slack_on = bool(os.getenv("SLACK_WEBHOOK"))
        self.slack_url = os.getenv("SLACK_WEBHOOK", "")

    def send(self, level, title, message, data=None, force_email=False):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        full = f"[{level}] {ts}\n\n{message}"
        if data:
            full += f"\n\nDetails:\n{json.dumps(data, indent=2, default=str)}"
        if level == "CRITICAL":
            self._slack(title, full, "danger")
            self._email(f"[CRITICAL] {title}", full)
        elif level == "WARNING":
            self._slack(title, full, "warning")
            if force_email:
                self._email(f"[WARNING] {title}", full)
        elif level == "INFO":
            self._slack(title, full, "good")
        elif level == "DAILY_REPORT":
            self._email(f"Trading Report: {title}", full)
            self._slack(f"Report: {title}", message[:500], "good")
        logger.info(f"Notification [{level}]: {title}")

    def _email(self, subject, body):
        if not self.email_on:
            return
        try:
            msg = MIMEMultipart()
            msg["From"] = self.smtp_user
            msg["To"] = self.email_to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))
            with smtplib.SMTP(self.smtp_srv, self.smtp_port) as s:
                s.starttls()
                s.login(self.smtp_user, self.smtp_pass)
                s.send_message(msg)
        except Exception as e:
            logger.error(f"Email failed: {e}")

    def _slack(self, title, message, color="good"):
        if not self.slack_on:
            return
        try:
            import urllib.request
            payload = {"attachments": [{"color": color, "title": title, "text": message[:3000]}]}
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(self.slack_url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            logger.error(f"Slack failed: {e}")

    def send_trade_alert(self, action, symbol, details):
        lines = [f"{k}: {v}" for k, v in details.items()]
        self.send("INFO", f"{action}: {symbol}", "\n".join(lines), details)

    def send_breaker_alert(self, reason, halt_until):
        self.send("CRITICAL", "CIRCUIT BREAKER", f"Reason: {reason}\nHalt until: {halt_until}", {"reason": reason, "halt_until": halt_until})
