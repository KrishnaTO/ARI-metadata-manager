"""Commit an ontology rewrite in the order of the file it was made from.

owlready2 writes subjects in the order it numbered them on load, which is the
order they are first *mentioned* — a disease's ``hasSymptom`` link numbers the
symptom before its own block is reached. Any file last written by something
else (a hand repair, a git merge) comes back reordered, so a one-synonym publish
moved ~1,100 lines (ARI#105) and buried the change it was made for.

The blocks themselves come out byte-identical, so the commit is the source
file's own text with each entity block swapped for its rewritten version. A
block the rewrite dropped goes; a new one goes at the end, where owlready2 puts
it too.
"""
import re

# A top-level entity: a node element at column 0 with an rdf:about. It ends at
# its own end tag on a line of its own, or on the same line when it is `/>`.
ENTITY_OPEN_RE = re.compile(r'<([\w:.-]+)\s[^>]*\brdf:about="([^"]*)"')


def _segments(text: str) -> list:
    """The file as ``[(about, lines)]`` — ``about`` is None for text between entities."""
    lines = text.split("\n")
    segments, gap, i = [], [], 0
    while i < len(lines):
        m = ENTITY_OPEN_RE.match(lines[i])
        if not m:
            gap.append(lines[i])
            i += 1
            continue
        if gap:
            segments.append((None, gap))
            gap = []
        start = i
        if not lines[i].rstrip().endswith("/>"):
            close = f"</{m.group(1)}>"
            while lines[i] != close:
                i += 1
                if i == len(lines):
                    raise ValueError(f"{m.group(2)} opens at line {start + 1} and never closes")
        segments.append((m.group(2), lines[start:i + 1]))
        i += 1
    segments.append((None, gap))
    return segments


def _blocks(segments: list) -> dict:
    out = {}
    for about, lines in segments:
        if about is None:
            continue
        if about in out:
            raise ValueError(f"{about} has more than one block, so which to replace is ambiguous")
        out[about] = lines
    return out


def splice(original: bytes, rewritten: bytes) -> bytes:
    """``rewritten``'s content laid out in ``original``'s order."""
    old = _segments(original.decode("utf-8"))
    new = _segments(rewritten.decode("utf-8"))
    _blocks(old)                        # refuse a source file with a duplicated subject
    blocks = _blocks(new)

    kept = [blocks.pop(about) for about, _ in old if about in blocks]
    kept += [lines for about, lines in new if about in blocks]    # new subjects

    # Header (namespaces) and footer are the rewrite's: they are whatever
    # owlready2 needs for the blocks it wrote. Blocks are one blank line apart,
    # as owlready2 writes them.
    out = list(new[0][1]) if new[0][0] is None else []
    for k, lines in enumerate(kept):
        if k:
            out.append("")
        out.extend(lines)
    out.extend(new[-1][1])
    return "\n".join(out).encode("utf-8")
