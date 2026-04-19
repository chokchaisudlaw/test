"""
สรุปข่าวรายวันแบบง่าย: ดึง RSS แล้วจัดหมวดเป็น Markdown (ไม่ใช้ AI = ไม่เปลืองโทเคน)

ใช้งาน:
  pip install -r requirements.txt
  python brief.py
  python brief.py --section world
  python brief.py --out my-brief.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import feedparser

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "feeds.json"
DEFAULT_OUT_DIR = ROOT / "output"


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_rss(url: str, timeout: int = 20) -> feedparser.FeedParserDict:
    req = Request(url, headers={"User-Agent": "daily-news-brief/1.0 (RSS reader; +local)"})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return feedparser.parse(raw)


def stable_id(title: str, link: str) -> str:
    base = (title.strip().lower() + "|" + link.strip()).encode("utf-8", errors="ignore")
    return hashlib.sha256(base).hexdigest()[:16]


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"ไม่พบ {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def collect_section(
    section_key: str,
    section: dict,
    seen: set[str],
    errors: list[str],
) -> list[dict]:
    items: list[dict] = []
    max_items = int(section.get("max_items", 10))
    title_th = section.get("title_th", section_key)
    feed_list = section.get("feeds") or []
    n_feeds = len(feed_list)
    per_feed_cap = max(1, (max_items + n_feeds - 1) // n_feeds) if n_feeds else max_items

    for feed in feed_list:
        name = feed.get("name", "?")
        url = feed.get("url")
        if not url:
            continue
        try:
            parsed = fetch_rss(url)
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            errors.append(f"[{section_key}] {name}: {e}")
            continue

        if getattr(parsed, "bozo", False) and not parsed.entries:
            errors.append(f"[{section_key}] {name}: parse error / empty")
            continue

        added_from_this_feed = 0
        for entry in parsed.entries:
            if added_from_this_feed >= per_feed_cap:
                break
            title = strip_html(entry.get("title") or "")
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue
            sid = stable_id(title, link)
            if sid in seen:
                continue
            seen.add(sid)
            summary = strip_html(entry.get("summary") or entry.get("description") or "")
            published = entry.get("published") or entry.get("updated") or ""
            items.append(
                {
                    "title": title,
                    "link": link,
                    "summary": summary[:400],
                    "published": published,
                    "source_feed": name,
                    "section_key": section_key,
                    "section_title_th": title_th,
                }
            )
            added_from_this_feed += 1

    items.sort(key=lambda x: x.get("published") or "", reverse=True)
    return items[:max_items]


def render_markdown(
    generated_at: datetime,
    all_items: dict[str, list[dict]],
    errors: list[str],
) -> str:
    lines: list[str] = []
    lines.append(f"# ข่าวรายวัน (RSS เท่านั้น — ไม่ผ่าน AI)")
    lines.append("")
    lines.append(f"- สร้างเมื่อ: `{generated_at.strftime('%Y-%m-%d %H:%M')} UTC`")
    lines.append("- แหล่งที่มา: ชื่อที่ระบุในแต่ละข้อ — คลิกลิงก์เพื่ออ่านต้นฉบับ")
    lines.append("")
    lines.append("---")
    lines.append("")

    for section_key in sorted(all_items.keys()):
        rows = all_items[section_key]
        if not rows:
            continue
        title_th = rows[0]["section_title_th"]
        lines.append(f"## {title_th}")
        lines.append("")
        for i, row in enumerate(rows, 1):
            lines.append(f"### {i}. {row['title']}")
            lines.append(f"- ลิงก์: {row['link']}")
            lines.append(f"- แหล่งฟีด: {row['source_feed']}")
            if row.get("published"):
                lines.append(f"- เผยแพร่ (จากฟีด): {row['published']}")
            if row.get("summary"):
                lines.append(f"- คำโปรยจากฟีด: {row['summary']}")
            lines.append("")

    if errors:
        lines.append("---")
        lines.append("")
        lines.append("## ข้อความแจ้งปัญหา (ฟีดบางตัวอาจล่มชั่วคราว)")
        lines.append("")
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*หมายเหตุ: นี่คือการรวมหัวข้อจาก RSS — ไม่ใช่การสรุปความโดยโมเดลภาษา*")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="ดึงข่าวจาก RSS เป็นหมวด (ไม่ใช้ AI)")
    parser.add_argument(
        "--section",
        help="เลือกเฉพาะคีย์หมวด เช่น world, markets, sports",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="ไฟล์ผลลัพธ์ (ถ้าไม่ระบุจะเขียนไปที่ output/YYYY-MM-DD-daily-brief.md)",
    )
    args = parser.parse_args()

    cfg = load_config()
    sections = cfg.get("sections") or {}
    if args.section:
        if args.section not in sections:
            print(f"ไม่มีหมวด `{args.section}` — มี: {', '.join(sections)}", file=sys.stderr)
            sys.exit(1)
        sections = {args.section: sections[args.section]}

    seen: set[str] = set()
    errors: list[str] = []
    all_items: dict[str, list[dict]] = {}

    for key, sec in sections.items():
        rows = collect_section(key, sec, seen, errors)
        all_items[key] = rows

    now = datetime.now(timezone.utc)
    md = render_markdown(now, all_items, errors)

    out_path = args.out
    if out_path is None:
        DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DEFAULT_OUT_DIR / f"{now.strftime('%Y-%m-%d')}-daily-brief.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"เขียนแล้ว: {out_path}")
    if errors:
        print(f"มีคำเตือน {len(errors)} รายการ (ดูในไฟล์)", file=sys.stderr)


if __name__ == "__main__":
    main()
