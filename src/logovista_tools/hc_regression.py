"""Regression harness for confirmed HC renderer visual-parity locks."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .entries import DictionarySource, discover_dictionaries
from .hc_parity import CONFIRMED_RENDER_PARITY_LOCKS, RenderParityLock
from .hcrender import extract_hc_render_entries_for_source
from .parallel import parallel_map_ordered, worker_args
from .ssed import resolve_case_insensitive_path


HC_RENDER_LOCK_REFERENCE_QUERIES: dict[str, tuple[str, ...]] = {
    "ARCHSIC4": ("高し", "如何で", "ば-や", "悪し"),
    "DAIJIRN4": ("左", "フリー", "鍔", "犬が西向きや尾は東", "アイリッシュセッター"),
    "Dconci87": ("have", "hand"),
    "EJJE200": ("実出発時刻",),
    "GENKANA5": ("エクストリーム出社",),
    "GKKANAN3": ("ソーシャル・ネットワーキング・サービス",),
    "GKKEIGO": ("おみやげを買ってきた。みんなで食べてね。",),
    "GKKOJIKT": ("漁夫の利",),
    "GKTISIKI": ("小倉百人一首と現代翻訳", "袱紗の包み方"),
    "HABGESPA": ("eclipsar", "febril", "diadema", "cabal", "jabado, da", "favorito, ta"),
    "HKBYOIN4": ("インフルエンザ検査", "感染症の検査"),
    "HOUGAKU5": ("司法的統制", "エー・ディー・アール"),
    "IBIO4": ("卵割型",),
    "IBIO5": ("IPTG", "穎果"),
    "IPHYCHE5": ("フラーレン",),
    "JSSAURU2": ("係わる", "連合する", "就学率", "赤根来"),
    "KCOMPEJ2": ("license",),
    "KENROWA": ("人",),
    "KOJIEN7": ("かみなり", "右", "天の川", "朝顔市", "カルタゴ", "赤坂", "太平記", "むく - むく", "ひし - と"),
    "KQBIZEJ": ("money market",),
    "KQDENTAL": ("denticle",),
    "KQNEWJE5": ("打診",),
    "MEIKYOU": ("内閣",),
    "MEIKYOU2": ("訪れる", "思う"),
    "NANMED19": ("DNA複製",),
    "NANMED20": ("架橋仮骨",),
    "NGFINANC": ("registrar", "registration", "flat", "balance of bank notes in circulation", "confidence"),
    "PROYAL43": ("et", "問う"),
    "PROYAL53": ("gentil, le", "運ぶ", "avoir"),
    "SPEECH": ("媒酌人挨拶 (新郎の上司)",),
    "YHOUGO4": ("財産相続",),
    "YHOUGO5": ("特定秘密の保護に関する法律", "行政上の不服申立て"),
    "YUECONO5": ("cost function",),
}

HC_RENDER_LOCK_BLUE_HEADWORD_DICT_IDS = frozenset(
    {
        "EJJE200",
        "GENKANA5",
        "HABGESPA",
        "IBIO4",
        "IPHYCHE5",
        "KCOMPEJ2",
        "KENROWA",
        "KOJIEN7",
        "KQNEWJE5",
        "MEIKYOU",
        "NANMED19",
    }
)

VIEWER_NAVIGATION_NAMES = frozenset({"backh.gif", "forwardh.gif", "backv.gif", "forwardv.gif"})
LOCAL_ASSET_TAGS = frozenset({"img", "object", "link", "script"})
LOCAL_ASSET_ATTRS = {"img": "src", "object": "data", "link": "href", "script": "src"}
INLINE_ASSET_CLASS_RE = re.compile(
    r"(?:gaiji|img_inline|figureRL|akusent|accent|img_mark|img_icon|lv-hc-media)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HcRenderLockIssue:
    """One regression-lock failure or warning."""

    code: str
    message: str
    severity: str = "error"
    html_path: str | None = None
    reference: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "html_path": self.html_path,
            "reference": self.reference,
        }


class _HcLockHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.stack: list[str] = []
        self.max_depth = 0
        self.starts: dict[str, int] = {}
        self.ends: dict[str, int] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        folded = tag.casefold()
        attr_map = {key.casefold(): value or "" for key, value in attrs}
        self.tags.append((folded, attr_map))
        self.starts[folded] = self.starts.get(folded, 0) + 1
        if folded not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(folded)
            self.max_depth = max(self.max_depth, len(self.stack))

    def handle_endtag(self, tag: str) -> None:
        folded = tag.casefold()
        self.ends[folded] = self.ends.get(folded, 0) + 1
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index] == folded:
                del self.stack[index:]
                return


def _is_external_asset_ref(value: str) -> bool:
    stripped = html.unescape(value).strip()
    if not stripped or stripped.startswith("#"):
        return True
    if stripped.startswith("//"):
        return True
    return re.match(r"^[a-z][a-z0-9+.-]*:", stripped, flags=re.IGNORECASE) is not None


def _asset_path_from_ref(value: str) -> Path | None:
    if _is_external_asset_ref(value):
        return None
    clean = html.unescape(value).split("#", 1)[0].split("?", 1)[0].replace("\\", "/")
    path = Path(clean)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def _local_asset_issues(html_path: Path, dict_out: Path, parser: _HcLockHtmlParser) -> list[HcRenderLockIssue]:
    issues: list[HcRenderLockIssue] = []
    for tag, attrs in parser.tags:
        attr_name = LOCAL_ASSET_ATTRS.get(tag)
        if attr_name is None:
            continue
        value = attrs.get(attr_name)
        if not value:
            continue
        name = Path(html.unescape(value).split("#", 1)[0].split("?", 1)[0].replace("\\", "/")).name.casefold()
        if name in VIEWER_NAVIGATION_NAMES:
            issues.append(
                HcRenderLockIssue(
                    code="viewer_navigation_chrome",
                    message="Windows reader previous/next chrome leaked into standalone HC output.",
                    html_path=str(html_path),
                    reference=value,
                )
            )
        asset_path = _asset_path_from_ref(value)
        if asset_path is None:
            continue
        resolved = resolve_case_insensitive_path(dict_out, asset_path)
        if resolved is None or not resolved.is_file():
            issues.append(
                HcRenderLockIssue(
                    code="missing_asset",
                    message="Referenced local HC render asset is missing.",
                    html_path=str(html_path),
                    reference=value,
                )
            )
            continue
        actual = resolved.relative_to(dict_out).as_posix()
        requested = asset_path.as_posix()
        if actual != requested:
            issues.append(
                HcRenderLockIssue(
                    code="asset_case_mismatch",
                    message=f"Asset reference casing does not match disk casing: {requested} != {actual}.",
                    html_path=str(html_path),
                    reference=value,
                )
            )
    return issues


def _dimension_value(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"([0-9]+)", value)
    if match is None:
        return None
    return int(match.group(1))


def _style_dimension(style: str, name: str) -> int | None:
    match = re.search(rf"\b{name}\s*:\s*([0-9]+)px", style, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _huge_inline_asset_issues(html_path: Path, parser: _HcLockHtmlParser) -> list[HcRenderLockIssue]:
    issues: list[HcRenderLockIssue] = []
    for tag, attrs in parser.tags:
        if tag not in {"img", "object"}:
            continue
        class_name = attrs.get("class", "")
        ref = attrs.get("src") or attrs.get("data") or ""
        if not INLINE_ASSET_CLASS_RE.search(class_name) and not ref.casefold().endswith(".svg"):
            continue
        style = attrs.get("style", "")
        width = _dimension_value(attrs.get("width")) or _style_dimension(style, "width")
        height = _dimension_value(attrs.get("height")) or _style_dimension(style, "height")
        if (width is not None and width > 96) or (height is not None and height > 96):
            issues.append(
                HcRenderLockIssue(
                    code="huge_inline_asset",
                    message="Inline/SVG HC asset has explicit dimensions larger than the text-scale guardrail.",
                    html_path=str(html_path),
                    reference=ref,
                )
            )
    return issues


def _linked_stylesheet_text(html_path: Path, dict_out: Path, parser: _HcLockHtmlParser) -> str:
    parts: list[str] = []
    for tag, attrs in parser.tags:
        if tag != "link":
            continue
        href = attrs.get("href")
        asset_path = _asset_path_from_ref(href or "")
        if asset_path is None:
            continue
        resolved = resolve_case_insensitive_path(dict_out, asset_path)
        if resolved is not None and resolved.is_file():
            parts.append(resolved.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _style_invariant_issues(
    html_path: Path,
    text: str,
    stylesheet_text: str,
    lock: RenderParityLock,
    parser: _HcLockHtmlParser,
) -> list[HcRenderLockIssue]:
    issues: list[HcRenderLockIssue] = []
    combined = f"{text}\n{stylesheet_text}".casefold()
    if any(tag == "a" for tag, _attrs in parser.tags) and "5da24c" not in combined:
        issues.append(
            HcRenderLockIssue(
                code="missing_green_link_rule",
                message="Rendered reference contains links but the Windows green hyperlink rule is absent.",
                html_path=str(html_path),
            )
        )
    if lock.dict_id in HC_RENDER_LOCK_BLUE_HEADWORD_DICT_IDS and "3021f9" not in combined and "lv-hc-blue-headword" not in combined:
        issues.append(
            HcRenderLockIssue(
                code="missing_blue_headword_rule",
                message="Dictionary is locked as a blue-headword renderer, but the blue headword rule is absent.",
                html_path=str(html_path),
            )
        )
    return issues


def check_hc_render_file(html_path: Path, lock: RenderParityLock) -> tuple[HcRenderLockIssue, ...]:
    """Check one generated HC reference HTML file for regression hazards."""

    dict_out = html_path.parent
    text = html_path.read_text(encoding="utf-8", errors="replace")
    parser = _HcLockHtmlParser()
    parser.feed(text)
    issues: list[HcRenderLockIssue] = []
    issues.extend(_local_asset_issues(html_path, dict_out, parser))
    issues.extend(_huge_inline_asset_issues(html_path, parser))
    stylesheet_text = _linked_stylesheet_text(html_path, dict_out, parser)
    issues.extend(_style_invariant_issues(html_path, text, stylesheet_text, lock, parser))
    for tag in ("table", "tr", "td"):
        starts = parser.starts.get(tag, 0)
        ends = parser.ends.get(tag, 0)
        if starts != ends:
            issues.append(
                HcRenderLockIssue(
                    code="unbalanced_table_markup",
                    message=f"Unbalanced <{tag}> markup can create pathological browser table nesting: {starts} starts, {ends} ends.",
                    html_path=str(html_path),
                )
            )
    if parser.max_depth > 512:
        issues.append(
            HcRenderLockIssue(
                code="browser_hostile_dom_depth",
                message=f"Generated HC HTML DOM depth {parser.max_depth} exceeds the regression guardrail.",
                html_path=str(html_path),
            )
        )
    return tuple(issues)


def _lock_task(payload: tuple[RenderParityLock, DictionarySource | None, Path, argparse.Namespace]) -> dict[str, Any]:
    lock, source, out_dir, args = payload
    queries = HC_RENDER_LOCK_REFERENCE_QUERIES.get(lock.dict_id, ())
    if source is None:
        return {
            "dict_id": lock.dict_id,
            "package_id": lock.package_id,
            "status": "missing_package",
            "queries": list(queries),
            "issues": [
                HcRenderLockIssue(
                    code="missing_package",
                    message="Confirmed parity package was not discovered under the requested root.",
                ).as_dict()
            ],
        }
    if not queries:
        return {
            "dict_id": lock.dict_id,
            "package_id": lock.package_id,
            "status": "skipped_no_reference_queries",
            "queries": [],
            "issues": [],
        }
    render_args = argparse.Namespace(
        component=None,
        json=False,
        limit=args.limit,
        no_hash=args.no_hash,
        query=list(queries),
        reader_style=args.reader_style,
        vertical=args.vertical,
        verbose=getattr(args, "verbose", False),
    )
    summary = extract_hc_render_entries_for_source(source, out_dir / "entries", render_args)
    issues: list[HcRenderLockIssue] = []
    for row in summary.get("entries", []):
        if row.get("status") != "rendered":
            issues.append(
                HcRenderLockIssue(
                    code="reference_entry_not_rendered",
                    message=f"Reference query did not render: {row.get('query')!r} status={row.get('status')!r}.",
                )
            )
            continue
        html_path = Path(str(row.get("html_path")))
        issues.extend(check_hc_render_file(html_path, lock))
    return {
        "dict_id": lock.dict_id,
        "package_id": lock.package_id,
        "status": "failed" if issues else "ok",
        "queries": list(queries),
        "render_summary_path": str(Path(str(summary["matches_path"])).parent / "summary.json"),
        "issues": [issue.as_dict() for issue in issues],
    }


def run_hc_render_lock_for_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    selected = {item.casefold() for item in args.dict or []}
    locks = [lock for lock in CONFIRMED_RENDER_PARITY_LOCKS if not selected or lock.dict_id.casefold() in selected]
    dict_ids = [lock.dict_id for lock in locks]
    sources = discover_dictionaries(
        args.root or [Path(".")],
        jobs=getattr(args, "jobs", 1),
        dict_ids=dict_ids,
        include_gaiji=True,
        include_images=True,
    )
    source_by_dict = {source.dict_id.casefold(): source for source in sources}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    task_args = worker_args(args)
    rows = parallel_map_ordered(
        _lock_task,
        [(lock, source_by_dict.get(lock.dict_id.casefold()), args.out_dir, task_args) for lock in locks],
        jobs=getattr(args, "jobs", 1),
    )
    summary = {
        "schema": "logovista-hc-render-lock-summary-v1",
        "confirmed_lock_count": len(locks),
        "checked_count": sum(1 for row in rows if row["status"] in {"ok", "failed"}),
        "failed_count": sum(1 for row in rows if row["status"] == "failed"),
        "missing_package_count": sum(1 for row in rows if row["status"] == "missing_package"),
        "skipped_no_reference_queries_count": sum(1 for row in rows if row["status"] == "skipped_no_reference_queries"),
        "rows": rows,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [
        issue
        for row in rows
        for issue in row.get("issues", [])
        if isinstance(issue, dict) and issue.get("severity") == "error"
    ]
    if failures and not args.allow_failures:
        raise ValueError(f"hc-render-lock: {len(failures)} regression issue(s); see {args.out_dir / 'summary.json'}")
    return rows


def add_hc_render_lock_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, nargs="*", help="Corpus root, package directory, or direct .IDX path.")
    parser.add_argument("--out-dir", type=Path, default=Path("reports/hc-render-lock"))
    parser.add_argument("--dict", action="append", help="Limit to one confirmed dictionary id.")
    parser.add_argument("--limit", type=int, default=1, help="Maximum native index matches rendered for each reference query.")
    parser.add_argument("--vertical", action="store_true", help="Apply vertical-rendering class hints where profiles support them.")
    parser.add_argument("--reader-style", choices=("windows", "package"), default="windows")
    parser.add_argument("--no-hash", action="store_true", help="Skip SHA-256 calculation for HC plugin classification.")
    parser.add_argument("--allow-failures", action="store_true", help="Write the report but exit successfully when lock checks fail.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON rows.")


def print_hc_render_lock_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        print(
            f"{row['dict_id']:12s} {row['status']:28s} issues={len(row.get('issues') or [])}",
            file=sys.stderr,
        )
