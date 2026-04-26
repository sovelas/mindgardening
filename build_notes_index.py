#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

EXTENSIONS = {'.md', '.markdown', '.txt'}
IGNORE_DIRS = {
    '.git', '.github', '.obsidian', '.vscode', 'node_modules',
    'dist', 'build', '.next', '.cache', '__pycache__'
}

FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
INLINE_TAG_RE = re.compile(r"(?<!\S)#([A-Za-z0-9][A-Za-z0-9/_-]*)\b")
WIKILINK_RE = re.compile(r"!?\[\[([^\]]+)\]\]")
MARKDOWN_LINK_RE = re.compile(r"!?(?<!\!)\[[^\]]*\]\(([^)]+)\)")


def split_front_matter(text: str) -> Tuple[str, str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return '', text
    return match.group(1), text[match.end():]


def normalize_note_path(path: str) -> str:
    value = str(path or '').replace('\\', '/').strip()
    value = re.sub(r'^\./+', '', value)
    value = re.sub(r'^/+', '', value)
    value = re.sub(r'\.(md|markdown|txt)$', '', value, flags=re.IGNORECASE)
    return value.strip('/')


def front_matter_value(front_matter: str, key: str) -> str | None:
    pattern = re.compile(rf"(?mi)^{re.escape(key)}:\s*(.+?)\s*$")
    match = pattern.search(front_matter)
    if not match:
        return None
    value = match.group(1).strip().strip('"').strip("'")
    return value or None


def front_matter_bool(front_matter: str, key: str) -> bool | None:
    value = front_matter_value(front_matter, key)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {'true', 'yes', 'y', '1', 'on'}:
        return True
    if normalized in {'false', 'no', 'n', '0', 'off'}:
        return False
    return None


def front_matter_tags(front_matter: str) -> List[str]:
    tags: List[str] = []

    inline_list = re.search(r"(?mi)^tags:\s*\[(.*?)\]\s*$", front_matter)
    if inline_list:
        parts = [p.strip().strip('"').strip("'") for p in inline_list.group(1).split(',')]
        tags.extend([p for p in parts if p])

    csv_line = re.search(r"(?mi)^tags:\s*([^\[\n].+?)\s*$", front_matter)
    if csv_line:
        raw = csv_line.group(1).strip()
        if ',' in raw:
            parts = [p.strip().strip('"').strip("'") for p in raw.split(',')]
            tags.extend([p for p in parts if p])
        elif raw and not raw.startswith('- '):
            tags.append(raw.strip('"').strip("'"))

    block_list = re.search(r"(?ms)^tags:\s*\n((?:\s*-\s*.+\n?)*)", front_matter)
    if block_list:
        for line in block_list.group(1).splitlines():
            line = line.strip()
            if line.startswith('- '):
                tags.append(line[2:].strip().strip('"').strip("'"))

    seen = set()
    result = []
    for tag in tags:
        norm = tag.strip()
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


def strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", ' ', text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", ' ', text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", ' ', text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"<[^>]+>", ' ', text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", '', text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s?", '', text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", '', text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", '', text, flags=re.MULTILINE)
    text = re.sub(r"[*_~]", '', text)
    text = re.sub(r"\|", ' ', text)
    text = re.sub(r"\s+", ' ', text)
    return text.strip()


def extract_inline_tags(body: str) -> List[str]:
    body_without_headings = re.sub(r"^\s*#{1,6}\s+.*$", '', body, flags=re.MULTILINE)
    tags = INLINE_TAG_RE.findall(body_without_headings)
    seen = set()
    result = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            result.append(tag)
    return result


def extract_targets(body: str) -> List[str]:
    targets: List[str] = []

    def should_ignore(target: str) -> bool:
        return bool(re.search(r"\.(png|jpe?g|gif|svg|webp|bmp|pdf|mp3|wav|m4a|mov|mp4)$", target, flags=re.IGNORECASE))

    for match in WIKILINK_RE.finditer(body or ''):
        raw_target = (match.group(1) or '').split('|')[0].split('#')[0].strip()
        target = normalize_note_path(raw_target)
        if target and not should_ignore(target):
            targets.append(target)

    for match in MARKDOWN_LINK_RE.finditer(body or ''):
        href = (match.group(1) or '').strip()
        if not href or href.startswith('#'):
            continue
        if re.match(r'^(https?:|mailto:|tel:)', href, flags=re.IGNORECASE):
            continue
        target = normalize_note_path(href.split('#')[0].strip())
        if not target or should_ignore(target):
            continue
        if '.' in href and not re.search(r'\.(md|markdown|txt)$', href, flags=re.IGNORECASE):
            continue
        targets.append(target)

    seen = set()
    result = []
    for target in targets:
        if target not in seen:
            seen.add(target)
            result.append(target)
    return result


def extract_headings(body: str, max_items: int = 20) -> List[Dict[str, str | int]]:
    headings = []
    for match in HEADING_RE.finditer(body):
        level = len(match.group(1))
        text = match.group(2).strip()
        if text:
            headings.append({'level': level, 'text': text})
        if len(headings) >= max_items:
            break
    return headings


def guess_title(path: Path, front_matter: str, body: str) -> str:
    fm_title = front_matter_value(front_matter, 'title')
    if fm_title:
        return fm_title
    heading_match = HEADING_RE.search(body)
    if heading_match:
        return heading_match.group(2).strip()
    return path.stem.replace('-', ' ').replace('_', ' ').strip()


def make_excerpt(plain_text: str, max_chars: int = 240) -> str:
    if len(plain_text) <= max_chars:
        return plain_text
    cut = plain_text[:max_chars].rsplit(' ', 1)[0].strip()
    return cut + '…'


def iter_note_files(root: Path):
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in EXTENSIONS:
            continue
        yield path


def file_fingerprint(root: Path) -> Dict[str, Tuple[int, int]]:
    snapshot: Dict[str, Tuple[int, int]] = {}
    for path in iter_note_files(root):
        stat = path.stat()
        snapshot[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def build_index(root: Path) -> Dict:
    notes = []

    for path in iter_note_files(root):
        try:
            raw = path.read_text(encoding='utf-8', errors='ignore')
        except Exception as exc:
            print(f'Skipping {path}: {exc}')
            continue

        front_matter, body = split_front_matter(raw)
        plain = strip_markdown(body)
        stat = path.stat()
        rel_path = normalize_note_path(path.relative_to(root).as_posix())

        title = guess_title(path, front_matter, body)
        headings = extract_headings(body)
        targets = extract_targets(body)
        tags = []
        tags.extend(front_matter_tags(front_matter))
        tags.extend(extract_inline_tags(body))

        seen = set()
        deduped_tags = []
        for tag in tags:
            t = tag.strip()
            if t and t not in seen:
                seen.add(t)
                deduped_tags.append(t)

        reviewed = front_matter_bool(front_matter, 'reviewed?')
        if reviewed is None:
            reviewed = front_matter_bool(front_matter, 'reviewed')
        if reviewed is None:
            reviewed = False

        notes.append({
            'path': rel_path,
            'title': title,
            'modified': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            'size_bytes': stat.st_size,
            'word_count': len(plain.split()),
            'excerpt': make_excerpt(plain),
            'tags': deduped_tags,
            'headings': headings,
            'targets': targets,
            'reviewed': reviewed,
            'content': raw,
        })

    notes.sort(key=lambda n: n['modified'], reverse=True)

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'root': str(root.resolve()),
        'count': len(notes),
        'notes': notes,
    }


def write_json(data: Dict, output_file: Path):
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='Build a local notes JSON index for the graph page.')
    parser.add_argument('notes_dir', help='Folder containing your notes')
    parser.add_argument('output_json', help='Where to write notes-index.json')
    parser.add_argument('--watch', action='store_true', help='Keep watching the folder and rebuild when files change')
    parser.add_argument('--interval', type=float, default=2.0, help='Watch polling interval in seconds (default: 2.0)')
    args = parser.parse_args()

    root = Path(args.notes_dir).resolve()
    output_file = Path(args.output_json).resolve()

    if not root.exists() or not root.is_dir():
        raise SystemExit(f'Notes folder does not exist or is not a directory: {root}')

    def rebuild():
        data = build_index(root)
        write_json(data, output_file)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Wrote {data['count']} notes to {output_file}")

    rebuild()

    if args.watch:
        last_snapshot = file_fingerprint(root)
        print(f'Watching {root} for changes... Press Ctrl+C to stop.')
        try:
            while True:
                time.sleep(args.interval)
                current_snapshot = file_fingerprint(root)
                if current_snapshot != last_snapshot:
                    rebuild()
                    last_snapshot = current_snapshot
        except KeyboardInterrupt:
            print('\nStopped watching.')


if __name__ == '__main__':
    main()
