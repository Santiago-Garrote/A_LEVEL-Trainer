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
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pymupdf as fitz
from PIL import Image
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SUBJECTS = json.loads((Path(__file__).parent / "subjects.json").read_text())
# subjects first seen through `links`, created automatically (no topic keywords yet)
LOCAL_SUBJECTS = DATA / "subjects-local.json"
if LOCAL_SUBJECTS.exists():
    SUBJECTS = {**json.loads(LOCAL_SUBJECTS.read_text()), **SUBJECTS}
BASE_URL = "https://pastpapers.papacambridge.com/directories/CAIE/CAIE-pastpapers/upload/"
SESSIONS = {"m": "Feb/March", "s": "May/June", "w": "Oct/Nov"}
DPI = 150
PAPER_RE = re.compile(r"^(\d{4})_([msw])(\d{2})_qp_(\d)(\d)$")


def subject(code):
    if code not in SUBJECTS:
        sys.exit(f"Unknown subject {code}. Add it to pipeline/subjects.json or convert a PapaCambridge link for it")
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
                    download(s, stem % "in", out, delay, remember_missing=True)  # source booklet, if any
                    got += 1
    print(f"{got} question papers available in {out}")


def download(s, stem, out, delay, remember_missing=False):
    """Fetch one PDF into out/. With remember_missing, a 404 leaves a marker so it isn't asked for again
    (used for inserts, which most papers don't have)."""
    path, missing = out / f"{stem}.pdf", out / f"{stem}.missing"
    if path.exists() and path.stat().st_size > 0:  # an empty file is a download that was cut off
        return True
    if remember_missing and missing.exists():
        return False
    try:
        r = s.get(BASE_URL + stem + ".pdf", timeout=60)
    except requests.RequestException as e:
        print(f"  ! {stem}: {e}")
        return False
    time.sleep(delay)
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        if remember_missing and r.status_code in (200, 404):
            missing.touch()
        return False
    tmp = path.with_suffix(".part")  # write then rename, so an interrupted run never leaves a half file
    tmp.write_bytes(r.content)
    tmp.replace(path)
    print(f"  + {stem}", flush=True)
    return True


# ---------------------------------------------------------------- PapaCambridge links

PAPA_HOSTS = {"pastpapers.papacambridge.com", "papacambridge.com", "www.papacambridge.com"}
STEM_RE = re.compile(r"(\d{4})_([msw])(\d{2})_(?:qp|ms)_(\d)(\d)\.pdf")
SLUG_RE = re.compile(r"as-and-a-level-([a-z-]+?)-?(\d{4})(?:-(\d{4}))?(?:-[a-z-]+)?/?$")
SESSION_LINK_RE = re.compile(r'href="([^"]*?as-and-a-level-[a-z-]+-?\d{4}-(\d{4})-[a-z-]+)"')
SUBJECT_LIST_URL = "https://pastpapers.papacambridge.com/papers/caie/as-and-a-level"
MIN_YEAR = 2018  # the splitter is checked against the 2018+ layout


def resolve_links(urls, delay=0.5, min_year=MIN_YEAR):
    """Expand PapaCambridge links into question papers.

    Accepts a PDF link (paper + its mark scheme), a session page (every paper on it) or a
    subject page (every session page from min_year). Returns ({code: {stem}}, {code: name}).
    """
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (A-Level Trainer personal study)"
    found, names = {}, {}

    def add(code, sess, yy, comp, var):
        found.setdefault(code, set()).add(f"{code}_{sess}{yy}_qp_{comp}{var}")

    def page(url):
        r = s.get(url, timeout=60)
        time.sleep(delay)
        r.raise_for_status()
        return r.text

    for url in urls:
        url = url.strip()
        host = requests.utils.urlparse(url).hostname or ""
        if host not in PAPA_HOSTS:
            raise ValueError(f"not a PapaCambridge link: {url}")
        if m := STEM_RE.search(url):  # direct PDF, also inside download_file.php?files=...
            add(*m.groups())
            continue
        slug = SLUG_RE.search(requests.utils.urlparse(url).path)
        if not slug:
            raise ValueError(f"don't know this kind of PapaCambridge page: {url}")
        name, code, year = slug.group(1).replace("-", " ").title(), slug.group(2), slug.group(3)
        names[code] = name
        if year:  # session page
            pages = [url]
        else:  # subject page: follow its session pages
            pages = sorted({requests.compat.urljoin("https://pastpapers.papacambridge.com/",
                                                    "papers/caie/" + href.rsplit("papers/caie/", 1)[-1])
                            for href, y in SESSION_LINK_RE.findall(page(url)) if int(y) >= min_year})
            print(f"  {name} ({code}): {len(pages)} session pages from {min_year}", flush=True)
        for pu in pages:
            for m in STEM_RE.finditer(page(pu)):
                if m.group(1) == code and 2000 + int(m.group(3)) >= min_year:
                    add(*m.groups())
    return found, names


def ensure_subject(code, name, comps):
    """Known subjects keep their config; new ones get a basic entry in data/subjects-local.json."""
    if code in SUBJECTS:
        return SUBJECTS[code]
    local = json.loads(LOCAL_SUBJECTS.read_text()) if LOCAL_SUBJECTS.exists() else {}
    entry = {"name": name or code, "components": {str(c): f"Paper {c}" for c in sorted(comps)}, "topics": []}
    local[code] = SUBJECTS[code] = entry
    LOCAL_SUBJECTS.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_SUBJECTS.write_text(json.dumps(local, indent=1))
    print(f"  new subject {code} {entry['name']}: components {', '.join(entry['components'])} (no topic tags yet)", flush=True)
    return entry


def from_links(urls, delay=0.5, jobs=None, dry_run=False, builder=None):
    """Download, build and pack the papers behind PapaCambridge links. Returns the subject codes built.

    With builder (an executor), each subject's build + pack is submitted to it instead of run inline,
    so the next subject can download meanwhile; the codes are then returned as futures' keys.
    """
    built = []
    found, names = resolve_links(urls, delay)
    if not found:
        sys.exit("No question papers found behind those links")
    for code, stems in sorted(found.items()):
        comps = {int(PAPER_RE.match(st).group(4)) for st in stems}
        meta = SUBJECTS.get(code) or {"components": {str(c): "" for c in comps}}
        since = meta.get("since", MIN_YEAR)
        wanted = sorted(st for st in stems if PAPER_RE.match(st).group(4) in meta["components"]
                        and 2000 + int(PAPER_RE.match(st).group(3)) >= since)
        skipped = len(stems) - len(wanted)
        print(f"{code}: {len(wanted)} question papers" + (f" ({skipped} skipped: components not configured, or before {since})" if skipped else ""), flush=True)
        if dry_run:
            print("  " + " ".join(wanted))
            continue
        ensure_subject(code, names.get(code), comps)
        out = DATA / "raw" / code
        out.mkdir(parents=True, exist_ok=True)
        s = requests.Session()
        s.headers["User-Agent"] = "Mozilla/5.0 (A-Level Trainer personal study)"
        ok = [st for st in wanted if download(s, st, out, delay) and download(s, st.replace("_qp_", "_ms_"), out, delay)]
        for st in ok:
            download(s, st.replace("_qp_", "_in_"), out, delay, remember_missing=True)
        if len(ok) < len(wanted):
            print(f"  ! {len(wanted) - len(ok)} papers couldn't be downloaded", flush=True)
        if ok:
            if builder:
                built.append(builder.submit(build_and_pack, code, ok, jobs))
            else:
                build_and_pack(code, ok, jobs)
                built.append(code)
    return built


def build_and_pack(code, stems, jobs=None):
    build(code, stems, jobs)
    pack(code)
    return code


def subject_page(code):
    """PapaCambridge subject page for a syllabus code, e.g. 9708 -> .../as-and-a-level-economics-9708."""
    html = requests.get(SUBJECT_LIST_URL, headers={"User-Agent": "Mozilla/5.0 (A-Level Trainer personal study)"}, timeout=60).text
    m = re.search(rf'href="[^"]*?(as-and-a-level-[a-z-]*?-?{code})"', html)
    if not m:
        sys.exit(f"{code}: no AS & A Level subject with that code on PapaCambridge")
    return f"https://pastpapers.papacambridge.com/papers/caie/{m.group(1)}"


def add(targets, delay=0.5, jobs=None):
    """Bring subjects up to date from PapaCambridge: each target is a syllabus code (whole subject,
    2018 onwards) or any PapaCambridge link (paper, session or subject page). Prints a check per subject."""
    # downloading is network-bound and kept slow on purpose; building is CPU-bound and parallel.
    # One background builder lets subject N build while subject N+1 downloads.
    with ThreadPoolExecutor(max_workers=1) as builder:
        pending = []
        for t in targets:
            url = subject_page(t) if re.fullmatch(r"\d{4}", t) else t
            print(f"== {t}" + (f"  ({url})" if url != t else ""), flush=True)
            pending += from_links([url], delay, jobs, builder=builder)
        codes = [f.result() for f in pending]
    problems = sum(check(c) for c in dict.fromkeys(codes))
    return problems


def check(code):
    """Sanity report for a built subject; returns the number of papers with problems."""
    qs = json.loads((DATA / "build" / code / "questions.json").read_text())
    raw = DATA / "raw" / code
    by = {}
    for q in qs:
        by.setdefault(q["paper"], []).append(q)
    since = SUBJECTS[code].get("since", MIN_YEAR)
    built_from = sorted(p.stem.replace("_qp", "") for p in raw.glob(f"{code}_*_qp_*.pdf")
                        if PAPER_RE.match(p.stem).group(4) in SUBJECTS[code]["components"]
                        and 2000 + int(PAPER_RE.match(p.stem).group(3)) >= since)
    ms_of = lambda p: raw / f"{p[:p.rindex('_')]}_ms{p[p.rindex('_'):]}.pdf"  # 9483_s24_11 -> 9483_s24_ms_11.pdf
    no_ms = [p for p in built_from if p not in by and not ms_of(p).exists()]
    bad = [f"{p}: no questions found" for p in built_from if p not in by and p not in no_ms]
    for paper, ps in sorted(by.items()):
        issues = []
        nums = [q["number"] for q in ps]
        if nums != sorted(set(nums)) or nums[0] != 1:
            issues.append("question numbers out of sequence")
        if ps[0]["type"] == "mcq":
            if any(not q.get("answer") for q in ps):
                issues.append("missing answers")
        else:
            if any(not q["marks"] for q in ps):
                issues.append(f"no marks for Q{','.join(str(q['number']) for q in ps if not q['marks'])}")
            if any(not q.get("msImage") and not q.get("grid") for q in ps):
                issues.append("no mark scheme for Q" + ",".join(str(q["number"]) for q in ps if not q.get("msImage") and not q.get("grid")))
        if issues:
            bad.append(f"{paper}: {'; '.join(issues)}")
    years = sorted({q["year"] for q in qs})
    print(f"\n{code} {SUBJECTS[code]['name']}: {len(by)} papers, {len(qs)} questions"
          + (f", {years[0]}–{years[-1]}" if years else "")
          + f", {sum(1 for q in qs if q.get('insert'))} with insert, {sum(1 for q in qs if not q['topics'])} without topic"
          + (f"\n  skipped (no mark scheme published): {', '.join(no_ms)}" if no_ms else "")
          + (f"\n  {len(bad)} papers need a look:" if bad else "\n  all papers OK"))
    for b in bad:
        print("  - " + b)
    return len(bad)


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


def question_starts(doc, need_bold=True, max_skip=0):
    """Question numbers in the left margin, in sequence 1, 2, 3, ...

    Normally bold; some papers (e.g. Oct/Nov 2024) use obfuscated fonts with no bold
    flag, so if nothing is found we retry with a tighter position check instead.
    Some numbers can be missing from the margin (e.g. gap-fill items 7–12 inside a passage):
    if the strict pass stops early, retry allowing jumps of up to 8.
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
            if m and (bold or not need_bold) and in_margin and expected <= int(m.group(1)) <= expected + max_skip:
                num = int(m.group(1))
                starts.append((num, pno, s["bbox"][1]))
                expected = num + 1
    if not starts and need_bold:
        return question_starts(doc, need_bold=False)
    if starts and not max_skip:
        tolerant = question_starts(doc, need_bold, max_skip=8)
        if len(tolerant) > len(starts) * 1.5:  # strict pass stopped at a gap
            return tolerant
    if not starts and not max_skip:
        starts = heading_starts(doc) or picture_pages(doc, question_starts(doc, need_bold, max_skip=8))
    return starts


def picture_pages(doc, starts):
    """Fill single missing question numbers printed as pictures (pages with no text layer): when
    question n is missing and a text-less image page sits where it should be, that page is question n."""
    if not starts:
        return starts
    pic = lambda pno: not doc[pno].get_text().strip() and doc[pno].get_images()
    out, prev_num, prev_page = [], 0, 0
    for num, pno, y in starts:
        if num == prev_num + 2:
            pics = [p for p in range(prev_page + 1, pno + 1) if p < len(doc) and pic(p)]
            if pics:
                out.append((num - 1, pics[0], 40.0))
        out.append((num, pno, y))
        prev_num, prev_page = num, pno
    return out


def heading_starts(doc):
    """Questions headed by a line "Question 1", "Question 2", ... (some essay papers, e.g. 8695)."""
    starts, expected = [], 1
    for pno, page in enumerate(doc):
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                text = "".join(sp["text"] for sp in l["spans"]).strip()
                if re.fullmatch(rf"Question {expected}", text):
                    starts.append((expected, pno, l["bbox"][1]))
                    expected += 1
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


def discounted(ms):
    """Questions Cambridge cancelled after the exam ("Question discounted", "removed from the question paper")."""
    text = "".join(page.get_text() for page in ms)
    return {int(m.group(1)) for m in re.finditer(
        r"(?m)^\s*(\d{1,2})\s*\n\s*(?:Question discounted|Due to an issue with question)", text, re.I)}


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


def content_box(page, top=0.0, bottom=None):
    """Bounding box (display coords) of text and drawings on a page, between top and bottom."""
    mtx = page.rotation_matrix
    bottom = page.rect.height if bottom is None else bottom
    rects = [fitz.Rect(w[:4]) * mtx for w in page.get_text("words")]
    rects += [d["rect"] * mtx for d in page.get_drawings()]
    rects += [fitz.Rect(i["bbox"]) * mtx for i in page.get_image_info()]
    box = fitz.Rect()
    for r in rects:
        if r.y0 >= top and r.y1 <= bottom and r.width < page.rect.width * 0.98:
            box |= r
    return box


def render_pages(doc, pnos, img_dir, prefix):
    """Render whole pages (minus running header/footer) one image each; returns the image names."""
    names = []
    for pno in pnos:
        page = doc[pno]
        top, bottom = page_bounds(page) if page.rotation == 0 else (40, page.rect.height - 30)
        footer = [r.y0 for r in (fitz.Rect(w[:4]) * page.rotation_matrix for w in page.get_text("words"))
                  if r.y0 > page.rect.height * 0.85]
        bottom = min([bottom] + [y - 3 for y in footer])
        box = content_box(page, top, bottom)
        if box.is_empty or box.height < 20:
            continue
        box = fitz.Rect(max(box.x0 - 6, 0), box.y0 - 6, min(box.x1 + 6, page.rect.width), box.y1 + 6)
        name = f"{prefix}{len(names) + 1}.webp"
        render(doc, [(pno, box)], img_dir / name)
        names.append(name)
    return names


def insert_pages(doc):
    """Pages of an insert (source booklet) worth showing: skip cover, blank and copyright pages."""
    return [pno for pno, page in enumerate(doc)
            if not is_end_page(page) and "BLANK PAGE" not in page.get_text()
            and not (pno == 0 and "INSERT" in page.get_text() and len(page.get_text()) < 900)]


GUIDE_RE = re.compile(r"\b[Ll]evels?\b|Table [A-Z]\b|Mark(?:ing)? [Gg]rids?|Quality of Language|Instructions for|[Ee]xaminers")
ANNOTATIONS_RE = re.compile(r"Annotations?\s+(?:Description|Meaning|Comment)|Annotations for")
BOILERPLATE_RE = re.compile(r"(Generic Marking|GENERIC MARKING|[\w ]*[-–]Specific Marking|Subject Specific Marking"
                            r"|\d+\s+(?:Calculation|Annotation)|PREPARATION FOR MARKING|Guidance on using levels)")


def guide_pages(ms):
    """Mark-scheme pages with level descriptors / marking grids that essay questions refer to.

    Skips the cover, the question tables, Cambridge's standard boilerplate and annotation lists,
    and pages repeated word for word (e.g. the same Quality of Language grid after each question).
    """
    pages, seen = [], set()
    for pno, page in enumerate(ms):
        if pno == 0:
            continue
        body = re.sub(r"^.*?Page \d+ of \d+\s*", "", " ".join(page.get_text().split()))
        if re.search(r"Question\s+Answer", body) or BOILERPLATE_RE.match(body) or ANNOTATIONS_RE.search(body[:150]):
            continue
        continues = pages and pages[-1] == pno - 1  # a grid running onto the next page
        if not continues and not GUIDE_RE.search(body):
            continue
        if body[:400] in seen:
            continue
        seen.add(body[:400])
        pages.append(pno)
    return pages


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
    # essay papers often state marks once on the cover instead of [n] after each question
    each = re.search(r"Each question is worth (\d+) marks", " ".join(qp[0].get_text().split()))
    answers = mcq_answers(ms) if ms else {}
    # a real MCQ key has about one letter per question (reading papers can list many lettered items)
    nums = [num for num, _, _ in starts]
    is_mcq = len(nums) >= 10 and sum(i in answers for i in nums) >= len(nums) * 0.8 and len(answers) <= max(nums) * 1.25
    msr = ms_regions(ms, max(nums)) if ms and not is_mcq else {}
    # shared by every question of the paper: the source booklet and the essay marking grids
    in_path = qp_path.with_name(qp_path.name.replace("_qp_", "_in_"))
    insert = render_pages(fitz.open(in_path), insert_pages(fitz.open(in_path)), img_dir, f"{paper_id}_in") if in_path.exists() else []
    insert_removed = in_path.exists() and any("removed due to copyright" in pg.get_text() for pg in fitz.open(in_path))
    grid = render_pages(ms, guide_pages(ms), img_dir, f"{paper_id}_grid") if ms and not is_mcq else []

    questions = []
    cancelled = discounted(ms) if ms and is_mcq else set()
    for num, parts in regions.items():
        if not parts or num in cancelled:
            continue
        qid = f"{paper_id}_q{num:02d}"
        text = render(qp, parts, img_dir / f"{qid}.webp")
        q = {
            "id": qid, "subject": code, "paper": paper_id, "component": comp,
            "variant": f"{comp}{var}", "session": sess, "year": 2000 + int(yy),
            "number": num, "image": f"{qid}.webp", "text": text,
            "topics": tag_topics(code, comp, text),
        }
        if insert:
            q["insert"] = insert
            if insert_removed:
                q["insertRemoved"] = True  # Cambridge blanked some third-party sources in the public copy
        if grid:
            q["grid"] = grid
        if is_mcq:
            q.update(type="mcq", marks=1, answer=answers.get(num))
        else:
            q.update(type="structured", marks=marks_in(text) or (int(each.group(1)) if each else None))
            if msr.get(num):
                q["msImage"] = f"{qid}_ms.webp"
                q["msText"] = render(ms, msr[num], img_dir / q["msImage"], gap=0)
        questions.append(q)
    found = f"{len(answers)} MCQ keys" if is_mcq else f"{len(msr)} mark-scheme sections"
    found += f", {len(insert)} insert pages" if insert else ""
    found += f", {len(grid)} grid pages" if grid else ""
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
        only = [only] if isinstance(only, str) else only
        papers = [p for p in papers if any(o in p.stem for o in only)]
        rebuilt = {p.stem.replace("_qp", "") for p in papers}
        kept = json.loads(index.read_text()) if index.exists() else []
        questions = [q for q in kept if q["paper"] not in rebuilt]
    else:
        questions = []
    # a subject can start later than 2018 (e.g. a syllabus change): drop older papers
    since = subject(code).get("since", MIN_YEAR)
    papers = [p for p in papers if 2000 + int(PAPER_RE.match(p.stem).group(3)) >= since]
    questions = [q for q in questions if q["year"] >= since]
    # papers are independent, so render them in parallel (one process per core by default)
    with ProcessPoolExecutor(jobs or os.cpu_count()) as pool:
        for qs in pool.map(build_one, [code] * len(papers), papers, [img_dir] * len(papers)):
            questions += qs
    fill_marks(questions)
    questions.sort(key=lambda q: q["id"])
    index.write_text(json.dumps(questions, indent=1))
    print(f"{len(questions)} questions -> {out / 'questions.json'}")


def fill_marks(questions):
    """Marks missing because a paper states them only on a cover with no text layer: use what the
    same question number of the same component is worth in the subject's other papers."""
    seen = {}
    for q in questions:
        if q["marks"]:
            seen.setdefault((q["component"], q["number"]), []).append(q["marks"])
    for q in questions:
        if not q["marks"] and (marks := seen.get((q["component"], q["number"]))):
            q["marks"] = max(set(marks), key=marks.count)


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
        names = {n for q in questions for n in [q["image"], q.get("msImage"), *q.get("insert", []), *q.get("grid", [])] if n}
        for name in sorted(names):  # inserts and grids are shared by a paper's questions: store once
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
    ad = sub.add_parser("add", help="bring subjects up to date: codes (whole subject) or PapaCambridge links")
    ad.add_argument("targets", nargs="+", help="e.g. 9708, or a PapaCambridge paper/session/subject link")
    ad.add_argument("--delay", type=float, default=0.5)
    ad.add_argument("--jobs", type=int)
    ck = sub.add_parser("check", help="report problems in a built subject")
    ck.add_argument("code")
    lk = sub.add_parser("links", help="download + build + pack the papers behind PapaCambridge links")
    lk.add_argument("urls", nargs="+", help="PDF, session page or subject page links")
    lk.add_argument("--delay", type=float, default=0.5)
    lk.add_argument("--jobs", type=int)
    lk.add_argument("--dry-run", action="store_true", help="only list the papers found")
    a = sub.add_parser("all", help="fetch + build + pack")
    a.add_argument("code")
    a.add_argument("--years", default="2019-2024")
    a.add_argument("--components")
    a.add_argument("--delay", type=float, default=0.5)
    a.add_argument("--jobs", type=int, help="parallel build processes, default: number of CPU cores")
    args = ap.parse_args()
    if args.cmd == "catalog":
        return catalog()
    if args.cmd == "add":
        return add(args.targets, args.delay, args.jobs) and 0  # problems are reported, not fatal
    if args.cmd == "check":
        return check(args.code) and 0
    if args.cmd == "links":
        return from_links(args.urls, args.delay, args.jobs, args.dry_run)

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
