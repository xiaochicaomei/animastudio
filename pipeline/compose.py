"""AnimaStudio page composer: panels + Chinese dialogue -> finished comic page.

Why not let the diffusion model draw the text: anime checkpoints trained on
booru data render pseudo-text glyphs, not readable Chinese. Overlaying real
type gives deterministic, legible dialogue - and it is the same reason the
negative prompt now bans text/speech bubbles.

CLI:
  python compose.py --cols 2 --rows 2 --panels p001,p002,p003,p003c --title "秘密"
  python compose.py --spec page.json
"""
import argparse
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(r"D:\AnimaStudio")
PANELS = ROOT / "workspace" / "out" / "panels"
PAGES = ROOT / "workspace" / "out" / "pages"
JOBS = ROOT / "workspace" / "jobs" / "jobs.jsonl"
FONT_CANDIDATES = [
    ROOT / "workspace" / "assets" / "fonts" / "NotoSansSC-Regular.ttf",
    ROOT / "workspace" / "assets" / "fonts" / "NotoSansSC[wght].ttf",
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
]

PAGE_W, PAGE_H = 1600, 2260          # B5-ish portrait at working scale
MARGIN = 46
GUTTER = 26
BORDER = 6


def find_font(size):
    for f in FONT_CANDIDATES:
        if f.exists():
            try:
                return ImageFont.truetype(str(f), size)
            except Exception:
                continue
    return ImageFont.load_default()


def load_dialogues():
    out = {}
    if JOBS.exists():
        for line in JOBS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                job = json.loads(line)
            except Exception:
                continue
            if job.get("id"):
                out[job["id"]] = job.get("dialogue") or ""
    return out


def fit_panel(img, w, h):
    """Cover-fit: fill the cell, crop the overflow, keep the subject centered."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = int((nh - h) * 0.35)  # bias upward: faces sit above center
    return img.crop((left, top, left + w, top + h))


def wrap_cjk(text, font, max_w, draw):
    """Character-wise wrapping: CJK has no spaces, so textwrap alone fails."""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def draw_bubble(draw, box, text, font, tail_to=None):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle([x0, y0, x1, y1], radius=28, fill=(255, 255, 255),
                           outline=(20, 20, 20), width=5)
    if tail_to:
        tx, ty = tail_to
        cx = (x0 + x1) // 2
        draw.polygon([(cx - 26, y1 - 8), (cx + 26, y1 - 8), (tx, ty)],
                     fill=(255, 255, 255), outline=(20, 20, 20))
        draw.line([(cx - 24, y1 - 6), (tx, ty - 2), (cx + 24, y1 - 6)],
                  fill=(20, 20, 20), width=5, joint="curve")
    pad = 18
    lines = wrap_cjk(text, font, (x1 - x0) - 2 * pad, draw)
    lh = font.size + 10
    total = lh * len(lines)
    ty = y0 + ((y1 - y0) - total) // 2
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        draw.text((x0 + ((x1 - x0) - tw) / 2, ty), ln, font=font, fill=(10, 10, 10))
        ty += lh


def load_text_detector():
    """The comic text detector, when its model has been downloaded."""
    try:
        from text_detect import DEFAULT_MODEL, TextDetector

        if Path(DEFAULT_MODEL).exists():
            return TextDetector()
    except Exception:
        pass
    return None


def overlay_dialogue(page, text, detector=None, min_frac=0.004):
    """Write `text` onto a finished page, inside a speech bubble.

    In page mode the model owns the layout, so the only place a code-drawn bubble
    can go without covering art is where the model already put one. The comic text
    detector finds them; EVERY bubble it finds is painted white - which erases the
    pseudo-glyphs the model drew inside - and the line is centred in the largest one.
    With no bubble detected at all, one is drawn in the lower third so the page still
    carries its line.

    Blanking all of them, not just the chosen one, is the whole point: the planner
    emits ONE line per page, but a diffusion model asked for a "speech bubble" draws
    as many as it likes and fills every one with glyph-shaped texture. Measured on a
    five-bubble page, erasing only the largest left four bubbles of gibberish in the
    published image.
    """
    draw = ImageDraw.Draw(page)
    w, h = page.size
    bubbles = []
    if detector is not None:
        try:
            hits = detector.detect(page, score_th=0.5)["hits"]
        except Exception:
            hits = []
        bubbles = [t for t in hits if t["label"] in ("bubble", "text_bubble")]

    biggest = None
    for t in bubbles:
        bx0, by0, bx1, by1 = t["box"]
        if (bx1 - bx0) * (by1 - by0) < min_frac * w * h:
            continue
        draw.rounded_rectangle([bx0, by0, bx1, by1],
                               radius=min(28, max(8, (by1 - by0) // 3)),
                               fill=(255, 255, 255))
        if biggest is None or (bx1 - bx0) * (by1 - by0) > ((biggest[2] - biggest[0])
                                                           * (biggest[3] - biggest[1])):
            biggest = (bx0, by0, bx1, by1)

    # text_free is glyph-shaped texture drawn straight onto the art: a corner signature,
    # a username, a date stamp. The negative prompt bans all of those by name and a strong
    # style LoRA still leaks them (measured: "@2UYTKOR" bottom-right of every page).
    # Only small, confident hits are erased: a big one is far more likely to be a false
    # positive on the artwork itself, and painting that white would be worse than the
    # mark it removed.
    if detector is not None:
        for t in hits:
            if t["label"] != "text_free" or t["score"] < 0.5:
                continue
            bx0, by0, bx1, by1 = t["box"]
            if (bx1 - bx0) * (by1 - by0) > 0.01 * w * h:
                continue
            draw.rectangle([bx0, by0, bx1, by1], fill=(255, 255, 255))

    if biggest is None:
        bw, bh = int(w * 0.56), int(h * 0.12)
        x0, y0 = (w - bw) // 2, int(h * 0.80)
        draw_bubble(draw, (x0, y0, x0 + bw, y0 + bh), text, find_font(34))
        return page

    x0, y0, x1, y1 = biggest
    pad = 14
    draw.rounded_rectangle([x0, y0, x1, y1], radius=min(28, max(8, (y1 - y0) // 3)),
                           fill=(255, 255, 255))
    inner_w, inner_h = (x1 - x0) - 2 * pad, (y1 - y0) - 2 * pad
    # shrink the type until the line fits whatever bubble the model happened to draw
    lines, font = [], find_font(17)
    for size in (40, 36, 32, 28, 24, 20, 17):
        font = find_font(size)
        lines = wrap_cjk(text, font, inner_w, draw)
        if len(lines) * (size + 8) <= inner_h:
            break
    lh = font.size + 8
    ty = y0 + max(pad, ((y1 - y0) - lh * len(lines)) // 2)
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        draw.text((x0 + ((x1 - x0) - tw) / 2, ty), ln, font=font, fill=(10, 10, 10))
        ty += lh
    return page


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panels", default=None, help="comma separated job ids, in page order")
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--rows", type=int, default=2)
    ap.add_argument("--title", default=None)
    ap.add_argument("--dialogue-scale", type=float, default=1.0)
    ap.add_argument("--cell", nargs=2, type=int, default=None, metavar=("W", "H"),
                    help="force the cell size; default sizes the sheet from the panels so "
                         "nothing is downscaled on the way in")
    ap.add_argument("--out", default=None)
    ap.add_argument("--page", default=None,
                    help="overlay --dialogue onto this finished page image and exit")
    ap.add_argument("--dialogue", default=None)
    args = ap.parse_args()

    if args.page:
        src = Path(args.page)
        if not src.exists():
            print("page not found: %s" % src)
            return 1
        with Image.open(src) as im:
            page = im.convert("RGB")
        text = (args.dialogue or "").strip()
        if text:
            overlay_dialogue(page, text, load_text_detector())
        out = Path(args.out) if args.out else src
        page.save(out)
        print("page dialogue: %s (%dx%d, %d chars)"
              % (out, page.size[0], page.size[1], len(text)))
        return 0

    ids = [s.strip() for s in (args.panels or "").split(",") if s.strip()]
    if not ids:
        ids = sorted(p.stem for p in PANELS.glob("*.png"))
    ids = ids[: args.cols * args.rows]
    if not ids:
        print("no panels to compose")
        return 1

    dialogues = load_dialogues()

    # Size the sheet from the panels themselves. The old fixed B5 canvas (1600x2260) is
    # SMALLER than a single hires'd panel (1248x1824), which left each cell ~741x1071 and
    # silently downscaled every panel - throwing away exactly the detail the refine pass
    # had just spent ~95s per panel producing. `--cell` overrides when a specific cell
    # size is wanted.
    if args.cell:
        cell_native = (int(args.cell[0]), int(args.cell[1]))
    else:
        cell_native = (0, 0)
        for jid in ids:
            f = PANELS / ("%s.png" % jid)
            if f.exists():
                with Image.open(f) as im:
                    cell_native = (max(cell_native[0], im.width),
                                   max(cell_native[1], im.height))
        if not cell_native[0]:
            cell_native = (600, 900)

    page_w = MARGIN * 2 + args.cols * cell_native[0] + (args.cols - 1) * GUTTER
    page_h = (MARGIN * 2 + (70 if args.title else 0) + args.rows * cell_native[1]
              + (args.rows - 1) * GUTTER)

    page = Image.new("RGB", (page_w, page_h), (250, 250, 248))
    draw = ImageDraw.Draw(page)

    inner_w = page_w - 2 * MARGIN
    inner_h = page_h - 2 * MARGIN - (70 if args.title else 0)
    cell_w = (inner_w - (args.cols - 1) * GUTTER) // args.cols
    cell_h = (inner_h - (args.rows - 1) * GUTTER) // args.rows

    top0 = MARGIN + (70 if args.title else 0)
    if args.title:
        tf = find_font(52)
        tw = draw.textlength(args.title, font=tf)
        draw.text(((page_w - tw) / 2, MARGIN + 6), args.title, font=tf, fill=(15, 15, 15))

    font = find_font(int(30 * args.dialogue_scale))

    for idx, jid in enumerate(ids):
        r, c = divmod(idx, args.cols)
        x = MARGIN + c * (cell_w + GUTTER)
        y = top0 + r * (cell_h + GUTTER)
        src = PANELS / ("%s.png" % jid)
        if not src.exists():
            draw.rectangle([x, y, x + cell_w, y + cell_h], fill=(228, 228, 228),
                           outline=(30, 30, 30), width=BORDER)
            draw.text((x + 20, y + 20), "missing: %s" % jid, font=find_font(26), fill=(90, 90, 90))
            continue
        with Image.open(src) as im:
            page.paste(fit_panel(im.convert("RGB"), cell_w, cell_h), (x, y))
        draw.rectangle([x, y, x + cell_w, y + cell_h], outline=(15, 15, 15), width=BORDER)

        text = (dialogues.get(jid) or "").strip()
        if text:
            bw = int(cell_w * 0.62)
            lines = max(2, len(text) // 9 + 1)
            bh = min(int(cell_h * 0.42), 34 * lines + 40)
            bx0 = x + 18
            by0 = y + cell_h - bh - 46
            # keep the tail inside the panel: a tail poking past the frame
            # reads as a rendering bug
            tail_y = min(by0 + bh + 34, y + cell_h - BORDER - 10)
            draw_bubble(draw, (bx0, by0, bx0 + bw, by0 + bh), text, font,
                        tail_to=(bx0 + bw // 2, tail_y))

    PAGES.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else PAGES / ("page_%s.png" % "-".join(ids[:2]))
    page.save(out)
    print("page written: %s (%dx%d, panels=%s)" % (out, page_w, page_h, ",".join(ids)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())