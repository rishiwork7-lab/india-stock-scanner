"""Report delivery. The owner reads results on the web page (GitHub Pages), so messaging apps (Telegram, WhatsApp,
Discord, Slack, ...) are deliberately not supported. The only optional channel is email (SMTP, HTML); it is off unless
EMAIL_SENDER and EMAIL_PASSWORD are set. Reports are always saved to reports/."""
from __future__ import annotations

import html
import logging
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

SMTP_BY_DOMAIN = {"gmail.com": ("smtp.gmail.com", 465), "outlook.com": ("smtp-mail.outlook.com", 587),
                  "hotmail.com": ("smtp-mail.outlook.com", 587), "yahoo.com": ("smtp.mail.yahoo.com", 465),
                  "yahoo.co.in": ("smtp.mail.yahoo.com", 465), "zoho.com": ("smtp.zoho.in", 465), "zohomail.in": ("smtp.zoho.in", 465),
                  "rediffmail.com": ("smtp.rediffmail.com", 465), "icloud.com": ("smtp.mail.me.com", 587)}


def chunk(text: str, limit: int) -> List[str]:
    """Split on paragraph boundaries so each piece is <= limit characters."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n\n"):
        piece = para + "\n\n"
        while len(piece) > limit:
            if cur:
                parts.append(cur.rstrip())
                cur = ""
            parts.append(piece[:limit])
            piece = piece[limit:]
        if len(cur) + len(piece) > limit:
            parts.append(cur.rstrip())
            cur = ""
        cur += piece
    if cur.strip():
        parts.append(cur.rstrip())
    return parts


def markdown_to_plain(md: str) -> str:
    s = re.sub(r"^#{1,6}\s*", "", md, flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", s)
    s = re.sub(r"</?sub>", "", s)
    s = re.sub(r"^\|[-| ]+\|$\n?", "", s, flags=re.M)
    return s


def markdown_to_html(md: str) -> str:
    """Small Markdown -> HTML converter for email: headings, bold/italic, lists, blockquotes, tables, rules."""
    out, in_list, table = [], False, []

    def inline(t: str) -> str:
        t = html.escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", t)
        return t.replace("&lt;sub&gt;", "<small>").replace("&lt;/sub&gt;", "</small>")

    def flush_table():
        if not table:
            return
        rows = [r for r in table if not re.match(r"^\|[-| :]+\|$", r)]
        h = "<table style='border-collapse:collapse;margin:6px 0'>"
        for i, r in enumerate(rows):
            cells = [c.strip() for c in r.strip("|").split("|")]
            tag = "th" if i == 0 else "td"
            h += "<tr>" + "".join(f"<{tag} style='border:1px solid #ddd;padding:4px 8px'>{inline(c)}</{tag}>" for c in cells) + "</tr>"
        out.append(h + "</table>")
        table.clear()

    for line in md.split("\n"):
        if line.startswith("|"):
            table.append(line)
            continue
        flush_table()
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
        elif line.startswith("> "):
            out.append(f"<blockquote style='border-left:3px solid #ccc;margin:4px 0;padding-left:8px'>{inline(line[2:])}</blockquote>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.strip():
            out.append(f"<p style='margin:4px 0'>{inline(line)}</p>")
    flush_table()
    if in_list:
        out.append("</ul>")
    return "<html><body style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:14px'>" + "\n".join(out) + "</body></html>"


class NotificationService:
    def __init__(self, config, post: Optional[Callable] = None, smtp_factory: Optional[Callable] = None):
        self.c = config
        self._post = post or requests.post
        self._smtp = smtp_factory

    def channels(self) -> List[str]:
        return ["email"] if self.c.email_sender and self.c.email_password else []

    def is_available(self) -> bool:
        return bool(self.channels())

    def save_report(self, content: str, directory: Path, filename: Optional[str] = None) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        p = directory / (filename or f"report_{datetime.now():%Y%m%d}.md")
        p.write_text(content, encoding="utf-8")
        return p

    def send(self, content: str, title: str = "India Stock Analysis") -> Dict[str, bool]:
        results = {}
        for ch in self.channels():
            try:
                getattr(self, f"_send_{ch}")(content, title)
                results[ch] = True
            except Exception as e:
                logger.error("notification via %s failed: %s", ch, e)
                results[ch] = False
        return results

    def _ok(self, r, what: str):
        if getattr(r, "status_code", 200) >= 300:
            raise RuntimeError(f"{what} HTTP {r.status_code}: {getattr(r, 'text', '')[:200]}")

    def _send_email(self, content, title):
        sender = self.c.email_sender
        domain = sender.split("@")[-1].lower()
        server, port = (self.c.smtp_server, self.c.smtp_port) if self.c.smtp_server else SMTP_BY_DOMAIN.get(domain, (f"smtp.{domain}", 465))
        port = port or 465
        to = self.c.email_receivers or [sender]
        msg = MIMEMultipart("alternative")
        msg["Subject"], msg["From"], msg["To"] = title, formataddr((self.c.email_sender_name, sender)), ", ".join(to)
        msg.attach(MIMEText(content, "plain", "utf-8"))
        msg.attach(MIMEText(markdown_to_html(content), "html", "utf-8"))
        factory = self._smtp or (smtplib.SMTP_SSL if port == 465 else smtplib.SMTP)
        with factory(server, port, timeout=30) as s:
            if port != 465 and hasattr(s, "starttls"):
                s.starttls()
            s.login(sender, self.c.email_password)
            s.sendmail(sender, to, msg.as_string())
