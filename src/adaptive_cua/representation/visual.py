"""Conservative screenshot coverage heuristic and viewport-coordinate crops."""

import hashlib
import io
import math
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageStat

from adaptive_cua.browser.models import BrowserSnapshot


class ObservationError(ValueError):
    """Capture data cannot be safely turned into a model observation."""


@dataclass(frozen=True)
class Visual:
    id: str
    kind: str
    bounds: dict[str, int]
    png: bytes


def box(bounds: dict, width: int, height: int, padding: int = 0):
    left = max(0, math.floor(bounds.get("x", 0)) - padding)
    top = max(0, math.floor(bounds.get("y", 0)) - padding)
    right = min(width, math.ceil(bounds.get("x", 0) + bounds.get("width", 0)) + padding)
    bottom = min(height, math.ceil(bounds.get("y", 0) + bounds.get("height", 0)) + padding)
    return (left, top, right, bottom) if right > left and bottom > top else None


def merge_boxes(boxes, gap=6):
    merged = []
    for candidate in boxes:
        changed = True
        while changed:
            changed = False
            for existing in merged[:]:
                if (
                    candidate[0] <= existing[2] + gap
                    and existing[0] <= candidate[2] + gap
                    and candidate[1] <= existing[3] + gap
                    and existing[1] <= candidate[3] + gap
                ):
                    merged.remove(existing)
                    candidate = (
                        min(candidate[0], existing[0]),
                        min(candidate[1], existing[1]),
                        max(candidate[2], existing[2]),
                        max(candidate[3], existing[3]),
                    )
                    changed = True
        merged.append(candidate)
    return sorted(merged, key=lambda b: (b[1], b[0]))


def screenshot_image(snapshot: BrowserSnapshot) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(snapshot.screenshot)).convert("RGB")
    except OSError as exc:
        raise ObservationError("The captured screenshot could not be decoded; observe again.") from exc
    expected = (snapshot.viewport["width"], snapshot.viewport["height"])
    if image.size != expected:
        raise ObservationError(
            "Screenshot dimensions do not match the CSS viewport; refusing ambiguous crop coordinates."
        )
    return image


def changed_pixels(previous: bytes | None, current: Image.Image) -> bool:
    if previous is None:
        return True
    old = Image.open(io.BytesIO(previous)).convert("RGB")
    if old.size != current.size:
        return True
    difference = ImageChops.difference(old, current).convert("L")
    histogram = difference.histogram()
    # Ignore tiny changes such as a blinking caret; this is a stall signal, not success grading.
    return sum(histogram[25:]) >= 64


def visual_candidates(snapshot: BrowserSnapshot, image: Image.Image) -> list[tuple[int, int, int, int]]:
    width, height = image.size
    uncovered = Image.new("L", image.size, 255)
    draw = ImageDraw.Draw(uncovered)
    candidates = []
    for element in snapshot.elements:
        if not element.visible:
            continue
        rect = box(element.bounds, width, height, padding=4)
        if rect is None:
            continue
        visual = (
            element.tag in {"canvas", "iframe", "video"}
            or (element.tag in {"img", "svg"} and not element.name.strip())
            or (
                element.role in {"button", "link", "checkbox", "radio"}
                and not (element.name.strip() or element.text.strip())
            )
        )
        if visual:
            candidates.append(rect)
            draw.rectangle((rect[0], rect[1], rect[2] - 1, rect[3] - 1), fill=0)
            continue
        regions = list(element.text_rects)
        if element.tag in {"input", "textarea", "select"} or (
            element.role in {"button", "link", "checkbox", "radio", "switch"} and element.name.strip()
        ):
            regions.append(element.bounds)
        # A named container does not explain all pixels inside its rectangle.
        for region in regions:
            covered = box(region, width, height, padding=2)
            if covered:
                draw.rectangle((covered[0], covered[1], covered[2] - 1, covered[3] - 1), fill=0)
    gray = image.convert("L")
    tile = 32
    for y in range(0, height, tile):
        for x in range(0, width, tile):
            region = (x, y, min(x + tile, width), min(y + tile, height))
            mask = uncovered.crop(region)
            if mask.histogram()[255] < 32:
                continue
            stats = ImageStat.Stat(gray.crop(region), mask)
            if stats.var[0] > 180:
                candidates.append(region)
    return merge_boxes(candidates)


def make_visual(image: Image.Image, region: tuple, kind="crop") -> Visual:
    left, top, right, bottom = region
    crop = image.crop(region)
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    png = buffer.getvalue()
    identifier = hashlib.sha256(str(region).encode() + png).hexdigest()[:16]
    return Visual(identifier, kind, {"x": left, "y": top, "width": right - left, "height": bottom - top}, png)
