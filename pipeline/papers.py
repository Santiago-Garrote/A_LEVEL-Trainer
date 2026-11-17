#!/usr/bin/env python3
"""CAIE past-paper pipeline: download papers, split them into questions, build study packs.

  python pipeline/papers.py fetch 9702 --years 2019-2024 --components 1,2,4
  python pipeline/papers.py build 9702
  python pipeline/papers.py pack 9702      # also refreshes data/packs/index.json

Output pack: data/packs/<code>.zip  (pack.json + img/*.webp). index.json lists every pack
for the app's Subjects page (served at /packs/ by `nix run .#serve`).
Papers are © UCLES: keep raw files and packs for personal study, don't publish them.
"""
import argparse
import json
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import pymupdf as fitz
from PIL import Image
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SUBJECTS = json.loads((Path(__file__).parent / "subjects.json").read_text())
BASE_URL = "https://pastpapers.papacambridge.com/directories/CAIE/CAIE-pastpapers/upload/"
SESSIONS = {"m": "Feb/March", "s": "May/June", "w": "Oct/Nov"}
DPI = 150
PAPER_RE = re.compile(r"^(\d{4})_([msw])(\d{2})_qp_(\d)(\d)$")


def subject(code):
    if code not in SUBJECTS:
        sys.exit(f"Unknown subject {code}. Add it to pipeline/subjects.json")
    return SUBJECTS[code]


# ---------------------------------------------------------------- fetch

def fetch(code, years, components, delay):
    out = DATA / "raw" / code
    out.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (A-Level Trainer personal study)"
    got = 0
    for year in years:
        yy = f"{year % 100:02d}"
        for sess in SESSIONS:
            # Feb/March is only published as variant 2
            variants = [2] if sess == "m" else [1, 2, 3]
            for comp in components:
                for var in variants:
                    stem = f"{code}_{sess}{yy}_%s_{comp}{var}"
                    qp, ms = stem % "qp", stem % "ms"
                    if not download(s, qp, out, delay):
                        continue
                    download(s, ms, out, delay)
                    got += 1
    print(f"{got} question papers available in {out}")


def download(s, stem, out, delay):
    path = out / f"{stem}.pdf"
    if path.exists():
        return True
    try:
        r = s.get(BASE_URL + stem + ".pdf", timeout=60)
    except requests.RequestException as e:
        print(f"  ! {stem}: {e}")
        return False
    time.sleep(delay)
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        return False
    path.write_bytes(r.content)
    print(f"  + {stem}")
    return True


# ---------------------------------------------------------------- question papers

END_MARKERS = ("BLANK PAGE", "Permission to reproduce", "Additional page", "lined page")


def page_bounds(page):
    """Vertical content area of a page, excluding running header and © UCLES footer."""
    top, bottom = 45.0, page.rect.height - 40
    for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
        if "UCLES" in text and y0 > page.rect.height * 0.8:
            bottom = min(bottom, y0 - 4)
        elif (y1 < 72 and y1 - y0 < 18 and "DO NOT WRITE" not in text
              and not (x0 < 70 and re.match(r"\s*\d", text))):
            # running header: page number, plus a barcode strip on 2024+ papers;
            # small blocks only, so a question starting high on the page is kept
            top = max(top, y1 + 1)
    return top, bottom


def is_end_page(page):
    text = page.get_text()
    words = "".join(l for l in text.splitlines() if not DOTS_RE.match(l))
    return any(m in text for m in END_MARKERS) and len(words.strip()) < 600


def question_starts(doc, need_bold=True):
    """Question numbers in the left margin, in sequence 1, 2, 3, ...

    Normally bold; some papers (e.g. Oct/Nov 2024) use obfuscated fonts with no bold
    flag, so if nothing is found we retry with a tighter position check instead.
    """
    starts, expected = [], 1
    for pno, page in enumerate(doc):
        lines = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                lines.append(l["spans"][0])
        for s in sorted(lines, key=lambda s: s["bbox"][1]):
            m = re.match(r"^(\d{1,2})(\s|$)", s["text"].strip())
            bold = "Bold" in s["font"] or s["flags"] & 16
            in_margin = s["bbox"][0] < (70 if need_bold else 58) and s["bbox"][0] > 40
            if m and (bold or not need_bold) and in_margin and int(m.group(1)) == expected:
                starts.append((expected, pno, s["bbox"][1]))
                expected += 1
    if not starts and need_bold:
        return question_starts(doc, need_bold=False)
    return starts


DOTS_RE = re.compile(r"^[\s.…]*$")


def content_bands(page, rect, gap=14):
    """Vertical bands of rect that hold real content: drops blank space and dotted answer lines."""
    w = page.rect.width
    spans = []

    def add(r):
        if r.y1 > rect.y0 and r.y0 < rect.y1 and r.x1 > 40 and r.x0 < w - 40:
            spans.append((max(r.y0, rect.y0), min(r.y1, rect.y1)))

    for b in page.get_text("dict")["blocks"]:
        if b["type"] == 1:
            add(fitz.Rect(b["bbox"]))
        for l in b.get("lines", []):
            text = "".join(s["text"] for s in l["spans"])
            if not DOTS_RE.match(text):
                add(fitz.Rect(l["bbox"]))
    for d in page.get_drawings():
        add(d["rect"])
    spans.sort()
    bands = []
    for y0, y1 in spans:
        if bands and y0 - bands[-1][1] < gap:
            bands[-1][1] = max(bands[-1][1], y1)
        else:
            bands.append([y0, y1])
    return [fitz.Rect(rect.x0, max(rect.y0, a - 4), rect.x1, min(rect.y1, b + 4))
            for a, b in bands if b - a > 2]


def question_regions(doc, starts):
    """For each question, the list of (page_no, rect) it occupies."""
    regions = {}
    w = doc[0].rect.width
    for i, (num, pno, y) in enumerate(starts):
        if i + 1 < len(starts):
            _, end_p, end_y = starts[i + 1]
        else:
            end_p = pno
            while end_p + 1 < len(doc) and not is_end_page(doc[end_p + 1]):
                end_p += 1
            end_y = None
        parts = []
        for p in range(pno, end_p + 1):
            page = doc[p]
            if p != pno and is_end_page(page):
                continue
            top, bottom = page_bounds(page)
            y0 = max(y - 6, top) if p == pno else top
            y1 = end_y - 6 if (p == end_p and end_y is not None) else bottom
            if y1 - y0 < 20:
                continue
            parts += [(p, r) for r in content_bands(page, fitz.Rect(36, y0, w - 36, y1))]
        regions[num] = parts
    return regions


def marks_in(text):
    return sum(int(m) for m in re.findall(r"\[(\d{1,2})\]", text))


# ---------------------------------------------------------------- mark schemes

def mcq_answers(ms):
    keys = {}
    for page in ms:
        for m in re.finditer(r"^\s*(\d{1,2})\s+([A-D])\s+1\s*$", page.get_text(), re.M):
            keys[int(m.group(1))] = m.group(2)
    return keys


LABEL_RE = re.compile(r"^[.,Q]?(\d{1,2})(\([a-z]+\))*$")


def content_bottom(page, y0, y1):
    """Lowest text or table line between y0 and y1 (display coords), to drop trailing blank space."""
    mtx = page.rotation_matrix
    ys = [r.y1 for r in (fitz.Rect(w[:4]) * mtx for w in page.get_text("words")) if y0 <= r.y0 and r.y1 <= y1]
    ys += [r.y1 for r in (d["rect"] * mtx for d in page.get_drawings()) if y0 <= r.y0 and r.y1 <= y1 + 1]
    return max(ys, default=y1) + 3


def ms_regions(ms, n_questions):
    """Locate each question's rows in a structured mark scheme (landscape table pages)."""
    rows = []  # (question, page_no, y) in display coordinates
    page_info = {}
    for pno, page in enumerate(ms):
        mtx = page.rotation_matrix
        words = [(fitz.Rect(w[:4]) * mtx, w[4]) for w in page.get_text("words")]
        headers = sorted((r for r, t in words if t in ("Question", "uestion") and r.x0 < 120
                          and any(t2 == "Answer" and abs(r2.y0 - r.y0) < 4 for r2, t2 in words)),
                         key=lambda r: r.y0)
        if not headers:
            continue
        header = headers[0]
        # "© UCLES" footer; words can come out split ("U" "CLES"), and some schemes
        # only fill the top of a portrait page, so search below the header, not the page bottom
        footer = min([r.y0 for r, t in words if ("CLES" in t or t.startswith("©")) and r.y0 > header.y1]
                     or [page.rect.height - 30])
        label_x = (header.x0 + header.x1) / 2 + 5  # labels sit under the "Question" heading
        # later headers start new tables on the same page
        page_info[pno] = (header.y1 + 2, footer - 4, [r.y0 for r in headers[1:]])
        for r, t in words:
            m = LABEL_RE.match(t)
            if m and r.x0 < label_x and r.y0 > header.y1 and r.y1 < footer:
                rows.append((int(m.group(1)), pno, r.y0))
    rows.sort(key=lambda t: (t[1], t[2]))
    # first appearance of each question number, keeping only increasing sequence
    firsts, last = [], 0
    for q, pno, y in rows:
        if q == last + 1 and q <= n_questions:
            firsts.append((q, pno, y))
            last = q
    regions = {}
    pages = sorted(page_info)
    for i, (q, pno, y) in enumerate(firsts):
        if i + 1 < len(firsts):
            _, end_p, end_y = firsts[i + 1]
        else:
            end_p, end_y = pages[-1], None
        parts = []
        for p in [p for p in pages if pno <= p <= end_p]:
            top, bottom, tables = page_info[p]
            y0 = y - 4 if p == pno else top
            y1 = end_y - 4 if (p == end_p and end_y is not None) else bottom
            for hy in tables:  # stop at a new table unless this question continues in it
                if y0 < hy < y1 and not any(rq == q and rp == p and hy < ry < y1 for rq, rp, ry in rows):
                    y1 = hy - 12
                    break
            y1 = min(y1, content_bottom(ms[p], y0, y1))
            if y1 - y0 > 15:
                parts.append((p, fitz.Rect(20, y0, ms[p].rect.width - 20, y1)))
        regions[q] = parts
    return regions


# ---------------------------------------------------------------- rendering

def render(doc, parts, path, gap=10):
    """Render (page, rect) parts stacked into one grayscale PNG; return extracted text."""
    pixes, texts = [], []
    for p, rect in parts:
        page = doc[p]
        pixes.append(page.get_pixmap(clip=rect, dpi=DPI, colorspace=fitz.csGRAY))
        text = page.get_text("text", clip=rect * page.derotation_matrix)
        texts += [l for l in text.splitlines() if not DOTS_RE.match(l)]
    gap = int(gap * DPI / 72)
    width = max(px.width for px in pixes)
    height = sum(px.height for px in pixes) + gap * (len(pixes) - 1)
    out = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, width, height), False)
    out.clear_with(255)
    y = 0
    for px in pixes:
        px.set_origin(0, y)
        out.copy(px, px.irect)
        y += px.height + gap
    # 16 gray levels + lossless WebP: crisp text at ~45% of the PNG size
    img = Image.frombytes("L", (out.width, out.height), out.samples)
    img.quantize(16, dither=Image.Dither.NONE).convert("L").save(path, "WEBP", lossless=True, method=4)
    return "\n".join(texts).strip()


def tag_topics(code, component, text):
    scores = []
    for t in subject(code).get("topics", []):
        if component not in t["components"]:
            continue
        low = text.lower()
        score = sum(low.count(k.lower()) for k in t["keywords"])
        if score:
            scores.append((score, t["id"]))
    scores.sort(reverse=True)
    if not scores:
        return []
    best = scores[0][0]
    return [tid for s, tid in scores[:2] if s >= max(2, best * 0.6)] or [scores[0][1]]


def build_paper(code, qp_path, ms_path, img_dir):
    m = PAPER_RE.match(qp_path.stem)
    _, sess, yy, comp, var = m.groups()
    comp = int(comp)
    paper_id = qp_path.stem.replace("_qp", "")
    qp = fitz.open(qp_path)
    ms = fitz.open(ms_path) if ms_path.exists() else None
    starts = question_starts(qp)
    if not starts:
        print(f"  ! {qp_path.stem}: no questions found")
        return []
    regions = question_regions(qp, starts)
    answers = mcq_answers(ms) if ms else {}
    is_mcq = len(answers) >= len(starts) * 0.8
    msr = ms_regions(ms, len(starts)) if ms and not is_mcq else {}

    questions = []
    for num, parts in regions.items():
        if not parts:
            continue
        qid = f"{paper_id}_q{num:02d}"
        text = render(qp, parts, img_dir / f"{qid}.webp")
        q = {
            "id": qid, "subject": code, "paper": paper_id, "component": comp,
            "variant": f"{comp}{var}", "session": sess, "year": 2000 + int(yy),
            "number": num, "image": f"{qid}.webp", "text": text,
            "topics": tag_topics(code, comp, text),
        }
        if is_mcq:
            q.update(type="mcq", marks=1, answer=answers.get(num))
        else:
            q.update(type="structured", marks=marks_in(text) or None)
            if msr.get(num):
                q["msImage"] = f"{qid}_ms.webp"
                q["msText"] = render(ms, msr[num], img_dir / q["msImage"], gap=0)
        questions.append(q)
    found = f"{len(answers)} MCQ keys" if is_mcq else f"{len(msr)} mark-scheme sections"
    print(f"  {paper_id}: {len(questions)} questions, {found}", flush=True)
    return questions


def build_one(code, qp_path, img_dir):
    ms_path = qp_path.with_name(qp_path.name.replace("_qp_", "_ms_"))
    try:
        return build_paper(code, qp_path, ms_path, img_dir)
    except Exception as e:  # one bad paper shouldn't stop the batch
        print(f"  ! {qp_path.stem}: {e}", flush=True)
        return []


def build(code, only=None, jobs=None):
    raw = DATA / "raw" / code
    out = DATA / "build" / code
    img_dir = out / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    index = out / "questions.json"
    papers = sorted(raw.glob(f"{code}_*_qp_*.pdf"))
    if only:
        # rebuild just the matching papers, keeping everything else already built
        papers = [p for p in papers if only in p.stem]
        rebuilt = {p.stem.replace("_qp", "") for p in papers}
        kept = json.loads(index.read_text()) if index.exists() else []
        questions = [q for q in kept if q["paper"] not in rebuilt]
    else:
        questions = []
    # papers are independent, so render them in parallel (one process per core by default)
    with ProcessPoolExecutor(jobs or os.cpu_count()) as pool:
        for qs in pool.map(build_one, [code] * len(papers), papers, [img_dir] * len(papers)):
            questions += qs
    questions.sort(key=lambda q: q["id"])
    index.write_text(json.dumps(questions, indent=1))
    print(f"{len(questions)} questions -> {out / 'questions.json'}")


def pack(code):
    out = DATA / "build" / code
    questions = json.loads((out / "questions.json").read_text())
    meta = subject(code)
    manifest = {
        "format": 1, "code": code, "name": meta["name"], "built": datetime.now().isoformat(timespec="seconds"),
        "components": meta.get("components", {}),
        "topics": [{"id": t["id"], "name": t["name"], "components": t["components"]}
                   for t in meta.get("topics", [])],
        "questions": questions,
    }
    dest = DATA / "packs" / f"{code}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w") as z:
        z.writestr("pack.json", json.dumps(manifest), compress_type=zipfile.ZIP_DEFLATED)
        for q in questions:
            for name in filter(None, [q["image"], q.get("msImage")]):
                z.write(out / "img" / name, f"img/{name}", compress_type=zipfile.ZIP_STORED)
    print(f"{dest} ({dest.stat().st_size / 1e6:.1f} MB, {len(questions)} questions)")
    catalog()


def catalog():
    """Write data/packs/index.json listing every built pack; the app's Subjects page reads it."""
    subjects = []
    for zp in sorted((DATA / "packs").glob("*.zip")):
        with zipfile.ZipFile(zp) as z:
            m = json.loads(z.read("pack.json"))
        years = [q["year"] for q in m["questions"]]
        subjects.append({
            "code": m["code"], "name": m["name"], "file": zp.name, "bytes": zp.stat().st_size,
            "count": len(m["questions"]), "papers": len({q["paper"] for q in m["questions"]}),
            "years": [min(years), max(years)] if years else [], "built": m["built"],
            "components": m["components"],
        })
    (DATA / "packs" / "index.json").write_text(json.dumps({"format": 1, "subjects": subjects}, indent=1))


def year_range(s):
    a, _, b = s.partition("-")
    return range(int(a), int(b or a) + 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download question papers + mark schemes")
    f.add_argument("code")
    f.add_argument("--years", default="2019-2024")
    f.add_argument("--components", help="comma list, default from subjects.json")
    f.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    b = sub.add_parser("build", help="split downloaded papers into questions")
    b.add_argument("code")
    b.add_argument("--only", help="only papers whose name contains this, e.g. s23_qp_22")
    b.add_argument("--jobs", type=int, help="parallel processes, default: number of CPU cores")
    p = sub.add_parser("pack", help="bundle built questions into data/packs/<code>.zip")
    p.add_argument("code")
    sub.add_parser("catalog", help="rewrite data/packs/index.json from the packs present")
    a = sub.add_parser("all", help="fetch + build + pack")
    a.add_argument("code")
    a.add_argument("--years", default="2019-2024")
    a.add_argument("--components")
    a.add_argument("--delay", type=float, default=0.5)
    a.add_argument("--jobs", type=int, help="parallel build processes, default: number of CPU cores")
    args = ap.parse_args()
    if args.cmd == "catalog":
        return catalog()

    meta = subject(args.code)
    if args.cmd in ("fetch", "all"):
        comps = [int(c) for c in (args.components or ",".join(meta["components"])).split(",")]
        fetch(args.code, year_range(args.years), comps, args.delay)
    if args.cmd in ("build", "all"):
        build(args.code, getattr(args, "only", None), args.jobs)
    if args.cmd in ("pack", "all"):
        pack(args.code)


if __name__ == "__main__":
    main()
