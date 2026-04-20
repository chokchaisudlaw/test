"""
ดึง RSS → (ถ้าเปิด) ดึงเนื้อหาจากหน้าบทความ → แปลเป็นภาษาไทย

ใช้งาน:
  pip install -r requirements.txt
  python gui.py           # หน้าต่างเลือกหมวด
  python brief.py
  python brief.py --section world
  python brief.py --no-fetch          # ไม่ดึงเต็มจากลิงก์ (เร็วขึ้น)
  python brief.py --no-translate      # คงภาษาอังกฤษ แต่ยังดึงเนื้อหาได้
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import feedparser
import trafilatura
from deep_translator import GoogleTranslator

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
    req = Request(url, headers={"User-Agent": "daily-news-brief/1.1 (RSS reader; +local)"})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return feedparser.parse(raw)


def stable_id(title: str, link: str) -> str:
    base = (title.strip().lower() + "|" + link.strip()).encode("utf-8", errors="ignore")
    return hashlib.sha256(base).hexdigest()[:16]


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(str(CONFIG_PATH))
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def run_brief(
    *,
    section_keys: list[str] | None,
    translate: bool,
    fetch_body: bool,
    out_path: Path | None = None,
) -> tuple[Path, list[str]]:
    """รันการดึงข่าวและเขียนไฟล์ผลลัพธ์ — คืนค่า (path ไฟล์, รายการข้อความแจ้งปัญหา)"""
    cfg = load_config()
    opts = merged_options(cfg)
    all_sections = cfg.get("sections") or {}
    all_keys = list(all_sections.keys())

    if section_keys is not None and len(section_keys) == 0:
        raise ValueError("ต้องเลือกอย่างน้อยหนึ่งหมวด")

    if section_keys is None:
        sections = all_sections
        name_suffix = ""
    else:
        sections = {}
        for key in section_keys:
            if key not in all_sections:
                raise KeyError(f"ไม่มีหมวดใน feeds.json: {key}")
            sections[key] = all_sections[key]
        sel = set(section_keys)
        full = set(all_keys)
        name_suffix = ""
        if sel != full:
            name_suffix = "-" + "-".join(sorted(section_keys))

    rss_summary_max = int(opts["rss_summary_max_chars"])

    seen: set[str] = set()
    errors: list[str] = []
    all_items: dict[str, list[dict]] = {}

    for key, sec in sections.items():
        rows = collect_section(key, sec, seen, errors, rss_summary_max)
        enrich_section_items(rows, opts, translate=translate, fetch_body=fetch_body, errors=errors)
        all_items[key] = rows

    now = datetime.now(timezone.utc)
    md = render_markdown(
        now,
        all_items,
        errors,
        translated=translate,
        fetched_body=fetch_body,
    )

    final_out = out_path
    if final_out is None:
        DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        suf = "-daily-brief-th.md" if translate else "-daily-brief.md"
        final_out = DEFAULT_OUT_DIR / f"{now.strftime('%Y-%m-%d')}{name_suffix}{suf}"

    final_out.parent.mkdir(parents=True, exist_ok=True)
    final_out.write_text(md, encoding="utf-8")
    return final_out.resolve(), errors


def merged_options(cfg: dict) -> dict:
    defaults = {
        "translate_to_thai": True,
        "fetch_article_body": True,
        "rss_summary_max_chars": 1500,
        "max_article_chars": 9000,
        "translate_chunk_size": 4500,
        "delay_seconds": 0.35,
        "max_fulltext_per_section": 8,
    }
    opts = cfg.get("options") or {}
    out = {**defaults, **opts}
    return out


def translate_text(
    text: str,
    *,
    translator: GoogleTranslator | None,
    chunk_size: int,
    delay: float,
    errors: list[str],
    label: str,
) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if translator is None:
        return text
    parts: list[str] = []
    try:
        for i in range(0, len(text), chunk_size):
            chunk = text[i : i + chunk_size]
            parts.append(translator.translate(chunk))
            time.sleep(delay)
        return "\n".join(parts).strip()
    except Exception as e:
        errors.append(f"{label}: {e}")
        return text


def extract_article_text(url: str, max_chars: int, timeout: int = 25) -> tuple[str | None, str | None]:
    try:
        downloaded = trafilatura.fetch_url(url, no_ssl=False)
        if not downloaded:
            return None, "ดึงหน้าเว็บไม่ได้ (ว่าง)"
        extracted = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
        )
        if not extracted:
            return None, "แยกเนื้อหาหลักไม่ได้ (เว็บบล็อก/ล็อกอิน/สคริปต์)"
        plain = re.sub(r"\s+", " ", extracted).strip()
        if len(plain) > max_chars:
            plain = plain[:max_chars].rsplit(" ", 1)[0] + " …"
        return plain, None
    except Exception as e:
        return None, str(e)


def collect_section(
    section_key: str,
    section: dict,
    seen: set[str],
    errors: list[str],
    rss_summary_max_chars: int,
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
                    "summary": summary[:rss_summary_max_chars],
                    "published": published,
                    "source_feed": name,
                    "section_key": section_key,
                    "section_title_th": title_th,
                }
            )
            added_from_this_feed += 1

    items.sort(key=lambda x: x.get("published") or "", reverse=True)
    return items[:max_items]


def enrich_section_items(
    rows: list[dict],
    opts: dict,
    *,
    translate: bool,
    fetch_body: bool,
    errors: list[str],
) -> None:
    translator = None
    if translate:
        translator = GoogleTranslator(source="auto", target="th")

    chunk = int(opts["translate_chunk_size"])
    delay = float(opts["delay_seconds"])
    max_chars = int(opts["max_article_chars"])
    max_fetch = int(opts["max_fulltext_per_section"])

    for idx, row in enumerate(rows):
        row["title_th"] = translate_text(
            row["title"],
            translator=translator,
            chunk_size=min(chunk, 500),
            delay=delay,
            errors=errors,
            label=f"แปลหัวข้อ ({row.get('link', '')[:48]})",
        )

        summary_en = row.get("summary") or ""
        row["summary_th"] = translate_text(
            summary_en,
            translator=translator,
            chunk_size=chunk,
            delay=delay,
            errors=errors,
            label=f"แปลคำโปรย ({row.get('link', '')[:48]})",
        )

        row["body_en"] = ""
        row["body_th"] = ""
        row["body_note"] = ""

        if fetch_body and idx < max_fetch:
            body, why = extract_article_text(row["link"], max_chars)
            if body:
                row["body_en"] = body
                row["body_th"] = translate_text(
                    body,
                    translator=translator,
                    chunk_size=chunk,
                    delay=delay,
                    errors=errors,
                    label=f"แปลเนื้อหาเต็ม ({row.get('link', '')[:48]})",
                )
            else:
                row["body_note"] = why or "ไม่ทราบสาเหตุ"

        if not translate:
            row["title_th"] = row["title"]
            row["summary_th"] = summary_en
            row["body_th"] = row.get("body_en") or ""


def render_markdown(
    generated_at: datetime,
    all_items: dict[str, list[dict]],
    errors: list[str],
    *,
    translated: bool,
    fetched_body: bool,
) -> str:
    lines: list[str] = []
    lines.append("# ข่าวรายวัน (RSS + แปลไทย / ดึงเนื้อหาเมื่อทำได้)")
    lines.append("")
    lines.append(f"- สร้างเมื่อ: `{generated_at.strftime('%Y-%m-%d %H:%M')} UTC`")
    lines.append("- แปลภาษา: Google Translate (ผ่าน `deep-translator`) — อาจผิดพลาดในศัพท์เทคนิค")
    if fetched_body:
        lines.append("- เนื้อหายาว: ดึงจากหน้าเว็บด้วย `trafilatura` — บางลิงก์ดึงไม่ได้")
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
            display_title = row.get("title_th") or row["title"]
            lines.append(f"### {i}. {display_title}")
            lines.append("")
            lines.append(f"- **ลิงก์ต้นฉบับ:** {row['link']}")
            lines.append(f"- **แหล่งฟีด:** {row['source_feed']}")
            if row.get("published"):
                lines.append(f"- **เผยแพร่ (จากฟีด):** {row['published']}")
            lines.append("")

            if translated:
                lines.append("#### สรุปจากฟีด (ภาษาไทย)")
                lines.append("")
                lines.append(row.get("summary_th") or "—")
                lines.append("")
                lines.append("<details><summary>ข้อความจากฟีด (ภาษาอังกฤษ)</summary>")
                lines.append("")
                lines.append(row.get("summary") or "—")
                lines.append("")
                lines.append("</details>")
                lines.append("")
            else:
                lines.append("#### จากฟีด (อังกฤษ)")
                lines.append("")
                lines.append(row.get("summary") or "—")
                lines.append("")

            if fetched_body:
                body_th = (row.get("body_th") or "").strip()
                body_en = (row.get("body_en") or "").strip()
                note = row.get("body_note") or ""

                if body_th or body_en:
                    lines.append("#### เนื้อหาจากหน้าบทความ")
                    lines.append("")
                    if translated and body_th:
                        lines.append(body_th)
                    elif body_en:
                        lines.append(body_en)
                    lines.append("")
                    lines.append("<details><summary>หัวข้อภาษาอังกฤษ (ต้นฉบับฟีด)</summary>")
                    lines.append("")
                    lines.append(row.get("title") or "—")
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")
                elif note:
                    lines.append(f"*ไม่ได้ดึงเนื้อหาเต็มจากลิงก์:* {note}")
                    lines.append("")

            lines.append("---")
            lines.append("")

    if errors:
        lines.append("## ข้อความแจ้งปัญหา")
        lines.append("")
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")

    lines.append(
        "*หมายเหตุ: การแปลเป็นเครื่องมืออัตโนมัติ — ควรเช็คกับต้นฉบับที่ลิงก์เมื่อสำคัญ*"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="ดึงข่าว RSS แปลไทยและดึงเนื้อหาเมื่อทำได้")
    parser.add_argument("--section", help="เฉพาะหมวด world | markets | sports")
    parser.add_argument(
        "--out",
        type=Path,
        help="ไฟล์ผลลัพธ์ (ค่าเริ่มต้น output/YYYY-MM-DD-daily-brief.md)",
    )
    parser.add_argument("--no-translate", action="store_true", help="ไม่แปล — คงภาษาอังกฤษ")
    parser.add_argument("--no-fetch", action="store_true", help="ไม่ดึงเนื้อหาเต็มจากลิงก์")
    args = parser.parse_args()

    try:
        cfg = load_config()
    except FileNotFoundError:
        print(f"ไม่พบ {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)

    opts = merged_options(cfg)
    sections_map = cfg.get("sections") or {}

    section_keys: list[str] | None = None
    if args.section:
        if args.section not in sections_map:
            print(f"ไม่มีหมวด `{args.section}` — มี: {', '.join(sections_map)}", file=sys.stderr)
            sys.exit(1)
        section_keys = [args.section]

    translate = bool(opts["translate_to_thai"]) and not args.no_translate
    fetch_body = bool(opts["fetch_article_body"]) and not args.no_fetch

    try:
        out_path, errors = run_brief(
            section_keys=section_keys,
            translate=translate,
            fetch_body=fetch_body,
            out_path=args.out,
        )
    except (ValueError, KeyError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(f"เขียนแล้ว: {out_path}")
    if errors:
        print(f"มีคำเตือน {len(errors)} รายการ (ดูในไฟล์)", file=sys.stderr)


if __name__ == "__main__":
    main()
