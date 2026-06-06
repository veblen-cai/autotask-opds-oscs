#!/usr/bin/env python3
"""Generate and optionally deliver a Chinese research briefing on stretchable OPDs and OSCs.

The script intentionally uses only Python's standard library so it can run in a
minimal GitHub Actions environment. It retrieves recent scholarly metadata from
public APIs, asks an LLM to produce the final Chinese report when OPENAI_API_KEY
is configured, and falls back to a structured source digest otherwise.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import smtplib
import ssl
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_OUTPUT_DIR = Path("briefs")
OPENALEX_ENDPOINT = "https://api.openalex.org/works"
ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"

SEARCH_TOPICS = [
    {
        "label": "stretchable organic photodetectors",
        "queries": [
            '"stretchable" "organic photodetector"',
            '"intrinsically stretchable" "organic photodetector"',
            '"elastic" "organic photodetector"',
            '"organic photodiode" "stretchable"',
        ],
    },
    {
        "label": "stretchable organic solar cells",
        "queries": [
            '"stretchable" "organic solar cell"',
            '"stretchable" "organic photovoltaic"',
            '"intrinsically stretchable" "organic solar cell"',
            '"elastic" "organic photovoltaic"',
        ],
    },
    {
        "label": "polymer materials for organic photodetectors",
        "queries": [
            '"organic photodetector" polymer elastomer additive morphology',
            '"organic photodetector" elastomer interface encapsulation',
            '"organic photodetector" polymer additive mechanical performance',
        ],
    },
]

SYSTEM_PROMPT = """你是一名熟悉可拉伸有机光电探测器（OPDs）、有机太阳能电池（OSCs）和高分子材料的研究助理。请只基于给定参考源写作；如果信息不足，明确标注为“资料不足”或“基于文献的推断”。避免编造性能指标、DOI、发布日期或作者。"""

REPORT_INSTRUCTIONS = """
请用中文输出结构清晰的研究简报，聚焦近期可拉伸有机光电探测器（OPDs）以及有机太阳能电池（OSCs）的进展。

硬性要求：
1. 优先使用近 7-14 天内的新论文、预印本、期刊 Early View、会议摘要或可靠学术新闻；如近期资料不足，可扩展到近两年，并在简报开头注明实际时间范围。
2. 第一部分列出近期可拉伸 OPDs 或 OSCs 的重要进展。每条都要说明：核心材料体系、器件结构、关键性能指标、拉伸/弯折/循环测试结果、创新点、潜在局限；缺失项写“文摘/元数据未提供”。
3. 第二部分给出参考文件清单：题名、作者、期刊/平台、发布日期或在线日期、DOI/链接。
4. 第三部分单独写一份“高分子专业视角文献简报”：查阅哪些高分子材料、弹性体、聚合物添加剂、交联网络、界面层、封装层、共混策略或形貌调控材料掺入有机光电探测器，能够同时或分别提升光电性能与机械性能；逐项阐述提升原因、作用机制、创新点、适用器件、证据指标与参考文献。
5. 最后一部分给出 3-5 个值得跟进的研究假设或实验设计建议。
6. 必须明确区分“已由文献证明”和“基于文献的推断”。避免泛泛而谈。
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Chinese OPD/OSC research briefing.")
    parser.add_argument("--force", action="store_true", help="Run regardless of weekday/time gating.")
    parser.add_argument(
        "--force-if-after-8",
        action="store_true",
        help="Run immediately when current China time is 08:00 or later and today is eligible.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch sources but skip LLM and email delivery.")
    parser.add_argument("--lookback-days", type=int, default=14, help="Preferred recent search window.")
    parser.add_argument("--fallback-days", type=int, default=730, help="Fallback window when recent sources are sparse.")
    parser.add_argument("--min-recent-sources", type=int, default=8, help="Minimum recent sources before fallback.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for Markdown reports.")
    return parser.parse_args()


def env_dates(name: str) -> set[dt.date]:
    values = os.getenv(name, "")
    parsed: set[dt.date] = set()
    for raw in values.split(","):
        raw = raw.strip()
        if not raw:
            continue
        parsed.add(dt.date.fromisoformat(raw))
    return parsed


def is_eligible_china_day(now: dt.datetime) -> bool:
    local_date = now.date()
    holidays = env_dates("CHINA_HOLIDAYS")
    extra_workdays = env_dates("CHINA_EXTRA_WORKDAYS")
    if local_date in extra_workdays:
        return True
    if local_date in holidays:
        return False
    return now.weekday() < 5 or now.weekday() == 5  # Monday-Friday plus every Saturday.


def should_run(args: argparse.Namespace, now: dt.datetime) -> bool:
    if args.force:
        return True
    if not is_eligible_china_day(now):
        return False
    if args.force_if_after_8 and now.time() >= dt.time(8, 0):
        return True
    return now.time() < dt.time(8, 0)


def http_get(url: str, params: dict[str, Any] | None = None, timeout: int = 30) -> str:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "opds-oscs-briefing-bot/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def inverted_abstract_to_text(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in index.items():
        for position in positions:
            positioned.append((position, word))
    return " ".join(word for _, word in sorted(positioned))


def normalize_openalex_work(work: dict[str, Any], topic: str) -> dict[str, Any]:
    authorships = work.get("authorships") or []
    authors = [a.get("author", {}).get("display_name") for a in authorships[:8] if a.get("author")]
    venue = (work.get("primary_location") or {}).get("source") or {}
    doi = work.get("doi")
    return {
        "source": "OpenAlex",
        "topic": topic,
        "title": work.get("display_name"),
        "authors": [a for a in authors if a],
        "venue": venue.get("display_name") or work.get("type_crossref") or work.get("type"),
        "date": work.get("publication_date"),
        "doi": doi,
        "url": doi or work.get("id") or ((work.get("primary_location") or {}).get("landing_page_url")),
        "abstract": inverted_abstract_to_text(work.get("abstract_inverted_index"))[:1600],
    }


def search_openalex(query: str, from_date: dt.date, to_date: dt.date, topic: str, per_page: int = 8) -> list[dict[str, Any]]:
    params = {
        "search": query,
        "filter": f"from_publication_date:{from_date.isoformat()},to_publication_date:{to_date.isoformat()}",
        "sort": "publication_date:desc",
        "per-page": per_page,
    }
    mailto = os.getenv("OPENALEX_MAILTO")
    if mailto:
        params["mailto"] = mailto
    try:
        payload = json.loads(http_get(OPENALEX_ENDPOINT, params=params))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"OpenAlex search failed for {query!r}: {exc}", file=sys.stderr)
        return []
    return [normalize_openalex_work(work, topic) for work in payload.get("results", [])]


def strip_xml(text: str) -> str:
    import re

    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def search_arxiv(query: str, from_date: dt.date, topic: str, max_results: int = 5) -> list[dict[str, Any]]:
    # arXiv query syntax is less suited to exact phrases; use a broad all-field query.
    broad_query = " AND ".join(part.strip('"') for part in query.split()[:4])
    params = {
        "search_query": f"all:{broad_query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    try:
        xml = http_get(ARXIV_ENDPOINT, params=params)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"arXiv search failed for {query!r}: {exc}", file=sys.stderr)
        return []

    entries = []
    for raw_entry in xml.split("<entry>")[1:]:
        entry = raw_entry.split("</entry>", 1)[0]
        date_text = strip_xml(entry.split("<published>", 1)[1].split("</published>", 1)[0]) if "<published>" in entry else ""
        try:
            published = dt.datetime.fromisoformat(date_text.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if published < from_date:
            continue
        title = strip_xml(entry.split("<title>", 1)[1].split("</title>", 1)[0]) if "<title>" in entry else ""
        summary = strip_xml(entry.split("<summary>", 1)[1].split("</summary>", 1)[0]) if "<summary>" in entry else ""
        authors = [strip_xml(a.split("<name>", 1)[1].split("</name>", 1)[0]) for a in entry.split("<author>")[1:] if "<name>" in a]
        link = ""
        if "<id>" in entry:
            link = strip_xml(entry.split("<id>", 1)[1].split("</id>", 1)[0])
        entries.append(
            {
                "source": "arXiv",
                "topic": topic,
                "title": title,
                "authors": authors[:8],
                "venue": "arXiv",
                "date": published.isoformat(),
                "doi": "",
                "url": link,
                "abstract": summary[:1600],
            }
        )
    return entries


def collect_sources(days: int, now: dt.datetime) -> list[dict[str, Any]]:
    to_date = now.date()
    from_date = to_date - dt.timedelta(days=days)
    results: list[dict[str, Any]] = []
    for topic in SEARCH_TOPICS:
        for query in topic["queries"]:
            results.extend(search_openalex(query, from_date, to_date, topic["label"]))
            results.extend(search_arxiv(query, from_date, topic["label"]))
    return deduplicate(results)


def deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        key = (item.get("doi") or item.get("url") or item.get("title") or "").lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    unique.sort(key=lambda item: item.get("date") or "", reverse=True)
    return unique


def build_source_digest(sources: list[dict[str, Any]], window_label: str) -> str:
    lines = [f"# 可拉伸 OPDs/OSCs 自动检索简报（{window_label}）", ""]
    lines.append("> 未配置 OPENAI_API_KEY 或处于 dry-run 模式时，本文件为结构化来源摘要；配置后会生成完整中文研究简报。")
    lines.append("")
    for index, source in enumerate(sources, start=1):
        authors = ", ".join(source.get("authors") or []) or "作者未提供"
        lines.extend(
            [
                f"## {index}. {source.get('title') or '题名未提供'}",
                f"- 主题：{source.get('topic')}",
                f"- 作者：{authors}",
                f"- 期刊/平台：{source.get('venue') or source.get('source')}",
                f"- 日期：{source.get('date') or '日期未提供'}",
                f"- DOI/链接：{source.get('doi') or source.get('url') or '未提供'}",
                f"- 摘要/元数据：{source.get('abstract') or '未提供'}",
                "",
            ]
        )
    return "\n".join(lines)


def generate_with_openai(sources: list[dict[str, Any]], window_label: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return build_source_digest(sources, window_label)

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    source_json = json.dumps(sources[:40], ensure_ascii=False, indent=2)
    user_prompt = f"检索窗口：{window_label}\n\n{REPORT_INSTRUCTIONS}\n\n参考源 JSON：\n{source_json}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    response = http_post_json(
        OPENAI_ENDPOINT,
        payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    return response["choices"][0]["message"]["content"]


def send_email(subject: str, body: str) -> None:
    recipients = [item.strip() for item in os.getenv("BRIEFING_EMAIL_TO", "").split(",") if item.strip()]
    if not recipients:
        return
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT") or "587")
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("BRIEFING_EMAIL_FROM") or username or recipients[0]

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls(context=context)
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)


def main() -> int:
    args = parse_args()
    now = dt.datetime.now(CHINA_TZ)
    if not should_run(args, now):
        print(f"Skipping: {now.isoformat()} is not an eligible briefing time/day.")
        return 0

    recent_sources = collect_sources(args.lookback_days, now)
    if len(recent_sources) >= args.min_recent_sources:
        sources = recent_sources
        window_label = f"近 {args.lookback_days} 天（{(now.date() - dt.timedelta(days=args.lookback_days)).isoformat()} 至 {now.date().isoformat()}）"
    else:
        fallback_sources = collect_sources(args.fallback_days, now)
        sources = fallback_sources
        window_label = (
            f"近 {args.lookback_days} 天资料不足（仅 {len(recent_sources)} 条），"
            f"已扩展至近 {args.fallback_days} 天（{(now.date() - dt.timedelta(days=args.fallback_days)).isoformat()} 至 {now.date().isoformat()}）"
        )

    if args.dry_run:
        report = build_source_digest(sources, window_label)
    else:
        report = generate_with_openai(sources, window_label)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"opds-oscs-briefing-{now.date().isoformat()}.md"
    output_path.write_text(report + "\n", encoding="utf-8")
    print(f"Wrote {output_path} with {len(sources)} source(s).")

    if not args.dry_run:
        send_email(f"OPDs/OSCs 中文研究简报 - {now.date().isoformat()}", report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyError as exc:
        print(f"Missing required environment variable: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")[:1000]
        print(f"HTTP error {exc.code}: {details}", file=sys.stderr)
        raise SystemExit(1)
