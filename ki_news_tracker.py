#!/usr/bin/env python3
"""KI-News-Tracker: prüft RSS/Atom-Feeds von KI-Firmen und meldet neue Beiträge.

Benachrichtigungen (alle optional, per Umgebungsvariable aktiviert):
  ntfy      NTFY_TOPIC [, NTFY_SERVER, NTFY_TOKEN]
  Telegram  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  Discord   DISCORD_WEBHOOK_URL
  E-Mail    SMTP_HOST, SMTP_USER, SMTP_PASSWORD, EMAIL_TO [, SMTP_PORT, EMAIL_FROM]

Nur Python-Standardbibliothek, keine Abhängigkeiten.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "sources.json"
STATE_FILE = ROOT / "data" / "state.json"
NEWS_MD = ROOT / "NEWS.md"

USER_AGENT = "Mozilla/5.0 (compatible; KI-News-Tracker/1.0)"
HTTP_TIMEOUT = 25
SEEN_RETENTION_DAYS = 180


@dataclass
class Item:
    source: str
    title: str
    link: str
    published: str  # ISO-8601, leer wenn unbekannt
    summary: str
    key: str


# --------------------------------------------------------------------------- HTTP


def http_request(url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


def post_json(url: str, payload: dict, headers: dict | None = None) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return http_request(url, body, {"Content-Type": "application/json", **(headers or {})})


# ------------------------------------------------------------------------ Parsing


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child(el: ET.Element, *names: str) -> ET.Element | None:
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _text(el: ET.Element, *names: str) -> str:
    c = _child(el, *names)
    return (c.text or "").strip() if c is not None else ""


def parse_date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def clean_summary(raw: str, limit: int = 280) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _atom_link(entry: ET.Element) -> str:
    fallback = ""
    for c in entry:
        if _local(c.tag) != "link":
            continue
        href = c.get("href", "").strip()
        if c.get("rel", "alternate") == "alternate" and href:
            return href
        fallback = fallback or href
    return fallback


def _rss_link(item: ET.Element) -> str:
    for c in item:
        if _local(c.tag) == "link" and (c.text or "").strip():
            return c.text.strip()
    return _atom_link(item)


def parse_feed(xml_bytes: bytes, source: str) -> list[Item]:
    """Parst RSS 2.0, RSS 1.0 (RDF) und Atom."""
    root = ET.fromstring(xml_bytes.lstrip())
    kind = _local(root.tag)
    if kind == "feed":
        entries = [e for e in root if _local(e.tag) == "entry"]
    elif kind == "rss":
        channel = _child(root, "channel")
        entries = [e for e in (channel if channel is not None else []) if _local(e.tag) == "item"]
    else:  # RDF
        entries = [e for e in root if _local(e.tag) == "item"]

    items = []
    for e in entries:
        title = clean_summary(_text(e, "title"), 300)
        if kind == "feed":
            link = _atom_link(e)
            guid = _text(e, "id")
            date = _text(e, "published", "updated")
            summary = _text(e, "summary", "content")
        else:
            link = _rss_link(e)
            guid = _text(e, "guid")
            date = _text(e, "pubDate", "date", "published", "updated")
            summary = _text(e, "description", "encoded")
        if not (title or link):
            continue
        dt = parse_date(date)
        items.append(
            Item(
                source=source,
                title=title or link,
                link=link,
                published=dt.isoformat() if dt else "",
                summary=clean_summary(summary),
                key=normalize_key(link or guid or title),
            )
        )
    return items


def normalize_key(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://(www\.)?", "", value)
    value = re.sub(r"[?&]utm_[^&]+", "", value)
    return value.rstrip("/?").lower()


# -------------------------------------------------------------------------- State


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prune_seen(seen: dict, now: datetime) -> dict:
    cutoff = (now - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    return {k: v for k, v in seen.items() if v >= cutoff}


# ------------------------------------------------------------------ Notifications


class Notifier:
    def __init__(self, env=os.environ):
        self.env = env
        self.channels = [
            name
            for name, needed in [
                ("ntfy", ["NTFY_TOPIC"]),
                ("telegram", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]),
                ("discord", ["DISCORD_WEBHOOK_URL"]),
                ("email", ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]),
            ]
            if all(env.get(v) for v in needed)
        ]

    def send_item(self, item: Item) -> bool:
        """Sendet ein Item sofort (ntfy/Telegram/Discord). True, wenn mind. ein Kanal klappte."""
        instant = [c for c in self.channels if c != "email"]
        if not instant:
            return True
        ok = False
        for channel in instant:
            try:
                getattr(self, f"_send_{channel}")(item)
                ok = True
            except Exception as exc:  # noqa: BLE001 – ein Kanal darf die anderen nicht blockieren
                print(f"  ! {channel} fehlgeschlagen: {exc}", file=sys.stderr)
        return ok

    def send_digest(self, items: list[Item], skipped: int = 0) -> None:
        if "email" in self.channels and items:
            try:
                self._send_email(items, skipped)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! email fehlgeschlagen: {exc}", file=sys.stderr)

    def _send_ntfy(self, item: Item) -> None:
        server = (self.env.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
        headers = {}
        if self.env.get("NTFY_TOKEN"):
            headers["Authorization"] = f"Bearer {self.env['NTFY_TOKEN']}"
        payload = {
            "topic": self.env["NTFY_TOPIC"],
            "title": f"{item.source}: {item.title}"[:250],
            "message": item.summary or item.link,
            "tags": ["robot"],
        }
        if item.link:
            payload["click"] = item.link
            payload["actions"] = [{"action": "view", "label": "Öffnen", "url": item.link}]
        post_json(server, payload, headers)

    def _send_telegram(self, item: Item) -> None:
        text = f"🤖 <b>{html.escape(item.source)}</b>\n<b>{html.escape(item.title)}</b>"
        if item.summary:
            text += f"\n\n{html.escape(item.summary)}"
        if item.link:
            text += f"\n\n{html.escape(item.link)}"
        url = f"https://api.telegram.org/bot{self.env['TELEGRAM_BOT_TOKEN']}/sendMessage"
        post_json(url, {"chat_id": self.env["TELEGRAM_CHAT_ID"], "text": text, "parse_mode": "HTML"})

    def _send_discord(self, item: Item) -> None:
        embed = {"title": item.title[:256], "description": item.summary[:4000], "author": {"name": item.source}}
        if item.link:
            embed["url"] = item.link
        post_json(self.env["DISCORD_WEBHOOK_URL"], {"embeds": [embed]})
        time.sleep(0.5)  # Discord-Ratelimit

    def _send_email(self, items: list[Item], skipped: int) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"KI-News: {len(items) + skipped} neue Beiträge"
        msg["From"] = self.env.get("EMAIL_FROM") or self.env["SMTP_USER"]
        msg["To"] = self.env["EMAIL_TO"]
        lines = []
        for it in items:
            lines.append(f"[{it.source}] {it.title}\n{it.summary}\n{it.link}\n")
        if skipped:
            lines.append(f"… und {skipped} weitere (siehe NEWS.md)")
        msg.set_content("\n".join(lines))
        port = int(self.env.get("SMTP_PORT") or 465)
        if port == 465:
            with smtplib.SMTP_SSL(self.env["SMTP_HOST"], port, timeout=HTTP_TIMEOUT) as s:
                s.login(self.env["SMTP_USER"], self.env["SMTP_PASSWORD"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(self.env["SMTP_HOST"], port, timeout=HTTP_TIMEOUT) as s:
                s.starttls()
                s.login(self.env["SMTP_USER"], self.env["SMTP_PASSWORD"])
                s.send_message(msg)


# -------------------------------------------------------------------------- NEWS.md


def render_news_md(history: list[dict]) -> str:
    out = [
        "# KI-News",
        "",
        "_Automatisch erzeugt von `ki_news_tracker.py`. Neueste zuerst._",
        "",
    ]
    current_day = None
    for h in history:
        day = (h.get("published") or h["found"])[:10]
        if day != current_day:
            out += ["", f"## {day}", ""]
            current_day = day
        title = h["title"].replace("[", "\\[").replace("]", "\\]")
        line = f"- **{h['source']}**: [{title}]({h['link']})" if h["link"] else f"- **{h['source']}**: {title}"
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


def _sort_key(h: dict) -> str:
    return h.get("published") or h["found"]


# --------------------------------------------------------------------------- Main


def matches_keywords(item: Item, keywords: list[str]) -> bool:
    if not keywords:
        return True
    hay = f"{item.title} {item.summary}".lower()
    return any(k.lower() in hay for k in keywords)


def run(dry_run: bool = False, only: str | None = None) -> int:
    config = load_json(CONFIG_FILE, {})
    settings = config.get("settings", {})
    max_age = timedelta(days=settings.get("max_age_days", 7))
    max_notify = settings.get("max_notifications_per_run", 25)
    md_items = settings.get("news_md_items", 150)

    state = load_json(STATE_FILE, {"initialized_feeds": [], "seen": {}, "history": []})
    seen: dict = state["seen"]
    initialized = set(state["initialized_feeds"])
    history: list[dict] = state["history"]
    history_keys = {h["key"] for h in history}

    now = datetime.now(timezone.utc)
    notifier = Notifier()
    print(f"Kanäle: {', '.join(notifier.channels) or 'keine (nur NEWS.md)'}")

    sources = [s for s in config.get("sources", []) if s.get("enabled", True)]
    if only:
        sources = [s for s in sources if only.lower() in s["name"].lower()]

    new_items: list[Item] = []
    failures = 0
    for src in sources:
        name, url = src["name"], src["url"]
        try:
            items = parse_feed(http_request(url), name)
        except Exception as exc:  # noqa: BLE001 – ein kaputter Feed soll den Lauf nicht abbrechen
            failures += 1
            print(f"✗ {name}: {exc}", file=sys.stderr)
            continue
        items = [i for i in items if matches_keywords(i, src.get("keywords", []))]

        if url not in initialized:
            # Erster Lauf für diesen Feed: alles als gesehen markieren, nicht benachrichtigen.
            for it in items:
                seen.setdefault(it.key, now.isoformat())
                if it.key not in history_keys:
                    history.append({**asdict(it), "found": now.isoformat()})
                    history_keys.add(it.key)
            initialized.add(url)
            print(f"• {name}: {len(items)} Beiträge (erster Lauf, keine Benachrichtigung)")
            continue

        fresh = []
        for it in items:
            if it.key in seen:
                continue
            if it.published and now - datetime.fromisoformat(it.published) > max_age:
                seen[it.key] = now.isoformat()  # zu alt – merken, aber nicht melden
                continue
            fresh.append(it)
        print(f"• {name}: {len(items)} Beiträge, {len(fresh)} neu")
        new_items.extend(fresh)

    # Duplikate über mehrere Feeds hinweg entfernen, älteste zuerst melden
    unique: dict[str, Item] = {}
    for it in new_items:
        unique.setdefault(it.key, it)
    new_items = sorted(unique.values(), key=lambda i: i.published or now.isoformat())

    to_notify = new_items[-max_notify:] if max_notify else new_items
    notify_keys = {i.key for i in to_notify}
    skipped = len(new_items) - len(to_notify)

    delivered: list[Item] = []
    for it in new_items:
        print(f"  NEU [{it.source}] {it.title}\n      {it.link}")
        if dry_run:
            continue
        if it.key in notify_keys and not notifier.send_item(it):
            continue  # kein Kanal erreichbar → beim nächsten Lauf erneut versuchen
        delivered.append(it)
        seen[it.key] = now.isoformat()
        if it.key not in history_keys:
            history.append({**asdict(it), "found": now.isoformat()})
            history_keys.add(it.key)

    if not dry_run:
        notifier.send_digest([i for i in delivered if i.key in notify_keys], skipped)
        history.sort(key=_sort_key, reverse=True)
        state = {
            "initialized_feeds": sorted(initialized),
            "seen": prune_seen(seen, now),
            "history": history[:md_items],
        }
        save_json(STATE_FILE, state)
        NEWS_MD.write_text(render_news_md(state["history"]), encoding="utf-8")

    print(f"Fertig: {len(new_items)} neu, {failures}/{len(sources)} Feeds fehlgeschlagen.")
    return 1 if sources and failures == len(sources) else 0


def send_test() -> int:
    notifier = Notifier()
    if not notifier.channels:
        print("Kein Kanal konfiguriert – siehe README.md.", file=sys.stderr)
        return 1
    item = Item(
        source="KI-News-Tracker",
        title="Testnachricht ✅",
        link="https://github.com",
        published=datetime.now(timezone.utc).isoformat(),
        summary="Wenn du das liest, funktionieren die Benachrichtigungen.",
        key="test",
    )
    ok = notifier.send_item(item)
    notifier.send_digest([item])
    print(f"Test an {', '.join(notifier.channels)} gesendet." if ok else "Test fehlgeschlagen.")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="KI-News-Tracker")
    p.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts senden oder speichern")
    p.add_argument("--only", help="nur Quellen, deren Name diesen Text enthält")
    p.add_argument("--test", action="store_true", help="Testnachricht an alle Kanäle senden")
    args = p.parse_args()
    if args.test:
        return send_test()
    return run(dry_run=args.dry_run, only=args.only)


if __name__ == "__main__":
    sys.exit(main())
