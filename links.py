#!/usr/bin/env python3
from pathlib import Path
from collections import defaultdict
import os
import re
import sys

WIKILINK_RE = re.compile(r'(!)?\[\[([^\]]+)\]\]')

IGNORE_DIRS = {".obsidian", ".git", "__pycache__", "node_modules"}


def note_id(path: Path, vault: Path) -> str:
    return path.relative_to(vault).with_suffix("").as_posix()


def folder_id(path: Path, vault: Path) -> str:
    return path.relative_to(vault).as_posix()


def clean_link_target(raw: str) -> str:
    # [[note|alias]] -> note
    # [[note#heading]] -> note
    # [[note^block]] -> note
    target = raw.split("|", 1)[0]
    target = target.split("#", 1)[0]
    target = target.split("^", 1)[0]
    return target.strip().replace("\\", "/").strip("/")


def slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[ _]+", "-", s)
    s = re.sub(r"[^a-z0-9\-\/]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"/{2,}", "/", s)
    return s.strip("-/")


def export_graph(vault_path: Path, output_path: Path):
    folders = set()
    notes = set()
    note_files = []
    edges = set()
    unresolved_nodes = set()

    # basename index for matching "meaning-of-life" -> notes
    stem_index = defaultdict(list)

    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        root_path = Path(root)

        for f in files:
            if not f.lower().endswith(".md"):
                continue

            p = root_path / f
            nid = note_id(p, vault_path)
            fid = folder_id(p.parent, vault_path)

            folders.add(fid)
            notes.add(nid)
            note_files.append(p)

            stem_index[p.stem].append(nid)

            # folder -> note
            edges.add((fid, "contains", nid))

    for md_file in note_files:
        src_note = note_id(md_file, vault_path)
        text = md_file.read_text(encoding="utf-8", errors="ignore")

        for bang, raw_target in WIKILINK_RE.findall(text):
            if bang == "!":
                continue  # ignore embeds for now

            target = clean_link_target(raw_target)
            if not target:
                continue

            target_slug = slugify(target)

            # 1) exact basename match -> note
            if len(stem_index[target]) == 1:
                edges.add((src_note, "links_to", stem_index[target][0]))
                continue

            # 2) normalized slug match -> unresolved node + unresolved -> note
            if len(stem_index[target_slug]) == 1:
                unresolved_id = f"UNRESOLVED:{target}"
                unresolved_nodes.add(unresolved_id)

                edges.add((src_note, "links_to", unresolved_id))
                edges.add((unresolved_id, "links_to", stem_index[target_slug][0]))
                continue

            # 3) fully unresolved
            unresolved_id = f"UNRESOLVED:{target}"
            unresolved_nodes.add(unresolved_id)
            edges.add((src_note, "links_to", unresolved_id))

    with output_path.open("w", encoding="utf-8") as f:
        f.write("# NODES\n")
        for x in sorted(folders):
            f.write(f"folder\t{x}\n")
        for x in sorted(notes):
            f.write(f"note\t{x}\n")
        for x in sorted(unresolved_nodes):
            f.write(f"unresolved-wikilink\t{x.replace('UNRESOLVED:', '')}\n")

        f.write("\n# EDGES\n")
        for src, rel, dst in sorted(edges):
            f.write(f"{src}\t{rel}\t{dst}\n")

    print(f"Wrote: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python export_obsidian_graph.py /path/to/vault [output.txt]")
        sys.exit(1)

    vault = Path(sys.argv[1]).expanduser().resolve()
    if not vault.is_dir():
        print(f"Not a directory: {vault}")
        sys.exit(1)

    output = (
        Path(sys.argv[2]).expanduser().resolve()
        if len(sys.argv) > 2
        else vault / "vault_graph.txt"
    )

    export_graph(vault, output)