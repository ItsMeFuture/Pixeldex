import os
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence, ImageStat

from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.models import BallInstance

# ---------------------------------------------------------------------------
# Constants (from your original — EXACTLY as they were)
# ---------------------------------------------------------------------------
SOURCES_PATH = Path(os.path.dirname(os.path.abspath(__file__)), "./src")

CARD_WIDTH = 1500
CARD_HEIGHT = 2020

CARD_CORNERS = ((42, 250), (1428, 992))
artwork_size = [b - a for a, b in zip(*CARD_CORNERS)]

# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
title_font = ImageFont.truetype(str(SOURCES_PATH / "AwfullyDigital.ttf"), 160)
capacity_name_font = ImageFont.truetype(str(SOURCES_PATH / "VCRosdNEUE.ttf"), 90)
capacity_description_font = ImageFont.truetype(str(SOURCES_PATH / "VCRosdNEUE.ttf"), 75)
stats_font = ImageFont.truetype(str(SOURCES_PATH / "The Impostor.ttf"), 81)
credits_font = ImageFont.truetype(str(SOURCES_PATH / "rainyhearts.ttf"), 50)
bot_credits_font = ImageFont.truetype(str(SOURCES_PATH / "rainyhearts.ttf"), 40)

# ---------------------------------------------------------------------------
# Font Cache
# ---------------------------------------------------------------------------
_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

def get_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(path, size)
    return _font_cache[key]

# ---------------------------------------------------------------------------
# Shared Asset Cache
# ---------------------------------------------------------------------------
class SharedAssetCache:
    def __init__(self, maxsize: int = 60):
        self._cache: OrderedDict[str, Image.Image] = OrderedDict()
        self.maxsize = maxsize

    def get(self, path: str, size: tuple | None = None) -> Image.Image:
        key = f"{path}:{size}"
        if key not in self._cache:
            img = Image.open(path).convert("RGBA")
            if size and img.size != size:
                img = ImageOps.fit(img, size, method=Image.Resampling.BILINEAR)
            self._cache[key] = img
            if len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
        return self._cache[key].copy()

_shared_cache = SharedAssetCache(maxsize=60)

# ---------------------------------------------------------------------------
# Layer helpers
# ---------------------------------------------------------------------------
def _apply_layer(
    canvas: Image.Image,
    layer_path: str | None,
    media_path: str,
) -> Image.Image:
    """Apply a full-card overlay image. Returns canvas unchanged if no path."""
    if not layer_path:
        return canvas

    path = str(Path(media_path) / layer_path.lstrip("/"))
    layer = _shared_cache.get(path, canvas.size)
    return Image.alpha_composite(canvas, layer)


def _collect_under_layers(ball_instance: "BallInstance") -> list[str]:
    """Collect layer_under paths. Special replaces regime — don't stack regime layers on special cards."""
    ball = ball_instance.countryball
    layers: list[str] = []

    # If special is active, it REPLACES the regime — only use special layers
    if ball_instance.specialcard:
        if getattr(ball_instance.specialcard, "layer_under", None):
            layers.append(ball_instance.specialcard.layer_under)
        return layers

    # No special — use regime layers
    if ball.cached_regime and getattr(ball.cached_regime, "layer_under", None):
        layers.append(ball.cached_regime.layer_under)

    return layers


def _collect_over_layers(ball_instance: "BallInstance") -> list[str]:
    """Collect layer_over paths. Special replaces regime — don't stack regime layers on special cards."""
    ball = ball_instance.countryball
    layers: list[str] = []

    # If special is active, it REPLACES the regime — only use special layers
    if ball_instance.specialcard:
        if getattr(ball_instance.specialcard, "layer_over", None):
            layers.append(ball_instance.specialcard.layer_over)
        return layers

    # No special — use regime layers
    if ball.cached_regime and getattr(ball.cached_regime, "layer_over", None):
        layers.append(ball.cached_regime.layer_over)

    return layers
    
# ---------------------------------------------------------------------------
# Credit colour
# ---------------------------------------------------------------------------
credits_color_cache: dict[str, tuple] = {}


def get_credit_color(image: Image.Image, region: tuple) -> tuple:
    """Return black or white depending on the brightness of *region* in *image*."""
    cropped = image.crop(region)
    stat = ImageStat.Stat(cropped.convert("L"))
    brightness = stat.mean[0]
    return (0, 0, 0, 255) if brightness > 128 else (255, 255, 255, 255)

# ---------------------------------------------------------------------------
# Text helper
# ---------------------------------------------------------------------------
def draw_text_with_boundaries(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    position: tuple[int, int],
    boundary_width: int,
    boundary_height: int,
    resize_text: bool = True,
    wrap_text: bool = False,
    centered_boundary: bool = False,
    fill: tuple = (255, 255, 255, 255),
    stroke_width: int = 1,
    stroke_fill: tuple = (0, 0, 0, 255),
    text_align: str = "center",
    vertical_align: str = "center",
    show_boundaries: bool = False,
) -> None:
    x, y = position

    if centered_boundary:
        x = (CARD_WIDTH - boundary_width) // 2

    lines = (
        textwrap.wrap(text, width=int(boundary_width / (font.size / 2.5)))
        if wrap_text
        else [text]
    )

    if show_boundaries:
        draw.rectangle([(x, y), (x + boundary_width, y + boundary_height)], outline="black", width=3)

    h = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        while resize_text and (w > boundary_width or h * len(lines) > boundary_height):
            font = get_font(font.path, font.size - 1)
            bbox = draw.textbbox((0, 0), line, font=font)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    if vertical_align == "center":
        y += (boundary_height - h * len(lines)) / 2
    elif vertical_align == "bottom":
        y += boundary_height - h * len(lines)

    for i, line in enumerate(lines):
        line_bbox = draw.textbbox((0, 0), line, font=font)
        line_width = line_bbox[2] - line_bbox[0]

        if text_align == "center":
            line_x = x + (boundary_width - line_width) / 2
        elif text_align == "right":
            line_x = x + (boundary_width - line_width)
        else:
            line_x = x

        draw.text(
            (line_x, y + i * h),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )


# ---------------------------------------------------------------------------
# Main card renderer
# ---------------------------------------------------------------------------
def draw_card(
    ball_instance: "BallInstance",
    media_path: str = "./admin_panel/media/",
) -> tuple[Image.Image, dict[str, Any]]:
    ball = ball_instance.countryball
    special_credits = ""
    card_name = ball.cached_regime.name

    if special_image := ball_instance.special_card:
        card_name = getattr(ball_instance.specialcard, "name", card_name)
        image_path = Path(media_path) / special_image.lstrip("/")
        if ball_instance.specialcard and ball_instance.specialcard.credits:
            special_credits = ball_instance.specialcard.credits
    else:
        image_path = Path(media_path) / ball.cached_regime.background.lstrip("/")

    icon: Image.Image | None = None
    if ball.cached_economy:
        icon = Image.open(Path(media_path) / ball.cached_economy.icon.lstrip("/")).convert("RGBA")

    collection_path = Path(media_path) / ball.collection_card.lstrip("/")

    frames: list[Image.Image] = []
    output_meta: dict[str, Any] = {"format": "PNG"}

    with (
        Image.open(image_path) as base_image,
        Image.open(collection_path).convert("RGBA") as artwork,
    ):
        is_animated = getattr(base_image, "is_animated", False)
        duration = base_image.info.get("duration", 100)
        if is_animated:
            output_meta = {"format": "WEBP", "duration": duration}

        artwork_frame = ImageOps.fit(artwork, artwork_size)

        for bg_frame in ImageSequence.Iterator(base_image):
            bg_rgba = bg_frame.convert("RGBA")
            frame = Image.new("RGBA", bg_rgba.size)

            # ── LAYERS UNDER ARTWORK & BACKGROUND ──
            for layer_path in _collect_under_layers(ball_instance):
                frame = _apply_layer(frame, layer_path, media_path)

            frame.paste(artwork_frame, CARD_CORNERS[0], artwork_frame)
            frame.paste(bg_rgba, (0, 0), bg_rgba)

            draw = ImageDraw.Draw(frame)

            draw_text_with_boundaries(
                draw=draw,
                text=ball.short_name or ball.country,
                font=title_font,
                position=(160, 910),
                boundary_width=1150,
                boundary_height=140,
                resize_text=True,
                wrap_text=False,
                text_align="center",
                vertical_align="center",
                fill=(255, 255, 255, 255),
                stroke_width=3,
                stroke_fill=(228, 227, 237, 255),
            )

            draw_text_with_boundaries(
                draw=draw,
                text=ball.capacity_name,
                font=capacity_name_font,
                position=(185, 1100),
                boundary_width=1100,
                boundary_height=140,
                resize_text=True,
                wrap_text=True,
                text_align="center",
                vertical_align="center",
                fill=(230, 230, 230, 255),
                stroke_width=3,
                stroke_fill=(206, 201, 201, 255),
            )

            draw_text_with_boundaries(
                draw=draw,
                text=ball.capacity_description,
                font=capacity_description_font,
                position=(185, 1240),
                boundary_width=1100,
                boundary_height=300,
                resize_text=True,
                wrap_text=True,
                text_align="center",
                vertical_align="top",
                fill=(200, 200, 200, 255),
                stroke_width=3,
                stroke_fill=(178, 173, 173, 255),
            )

            draw_text_with_boundaries(
                draw=draw,
                text=str(ball_instance.health),
                font=stats_font,
                position=(183, 1660),
                boundary_width=400,
                boundary_height=80,
                resize_text=True,
                wrap_text=False,
                fill=(235, 92, 92, 255),
                text_align="center",
                vertical_align="center",
                stroke_width=3,
                stroke_fill=(120, 18, 28, 255),
            )

            draw_text_with_boundaries(
                draw=draw,
                text=str(ball_instance.attack),
                font=stats_font,
                position=(885, 1660),
                boundary_width=400,
                boundary_height=80,
                resize_text=True,
                wrap_text=False,
                text_align="center",
                vertical_align="center",
                fill=(255, 204, 74, 255),
                stroke_width=3,
                stroke_fill=(140, 90, 20, 255),
            )

            if settings.show_rarity:
                draw_text_with_boundaries(
                    draw=draw,
                    text=str(ball.rarity),
                    font=stats_font,
                    position=(1200, 50),
                    boundary_width=250,
                    boundary_height=80,
                    resize_text=True,
                    wrap_text=False,
                    text_align="center",
                    vertical_align="center",
                    stroke_width=2,
                    stroke_fill=(0, 0, 0, 255),
                )

            if card_name in credits_color_cache:
                credits_color = credits_color_cache[card_name]
            else:
                credits_color = get_credit_color(
                    frame, (0, int(frame.height * 0.8), frame.width, frame.height)
                )
                credits_color_cache[card_name] = credits_color

            artwork_text = f"Artwork: {ball.credits} | Card: {special_credits}" if special_credits else f"Artwork: {ball.credits}"

            draw_text_with_boundaries(
                draw=draw,
                text=artwork_text,
                font=credits_font,
                position=(160, 1815),
                boundary_width=1150,
                boundary_height=40,
                text_align="center",
                vertical_align="center",
                resize_text=True,
                wrap_text=False,
                fill=credits_color,
                stroke_width=0,
                stroke_fill=(0, 0, 0, 255),
            )

            draw_text_with_boundaries(
                draw=draw,
                # Do not remove this credit line – see BallsDex licence
                text=f"Created by El Laggron",
                font=bot_credits_font,
                position=(160, 1860),
                boundary_width=1150,
                boundary_height=50,
                text_align="center",
                vertical_align="center",
                resize_text=True,
                wrap_text=False,
                fill=credits_color,
                stroke_width=0,
                stroke_fill=(0, 0, 0, 255),
            )

            if icon:
                icon_resized = ImageOps.fit(icon, (70, 70))
                frame.paste(icon_resized, (700, 1650), mask=icon_resized)

            # ── LAYERS OVER TEXT ──
            for layer_path in _collect_over_layers(ball_instance):
                frame = _apply_layer(frame, layer_path, media_path)

            frames.append(frame)

    if icon:
        icon.close()

    if is_animated and len(frames) > 1:
        output_meta["frames"] = frames[1:]
        return frames[0], output_meta

    return frames[0], output_meta


# ---------------------------------------------------------------------------
# Event banner renderer
# ---------------------------------------------------------------------------

def draw_banner(event: Any, media_path: str = "./admin_panel/media/") -> Image.Image:
    banner_path = Path(media_path) / event.banner.lstrip("/")
    image = Image.open(banner_path).convert("RGBA")
    draw = ImageDraw.Draw(image)

    image_width, image_height = image.size

    # Scale font sizes relative to banner dimensions
    title_f   = ImageFont.truetype(title_font.path, int(image_width * 0.03))
    desc_f    = ImageFont.truetype(capacity_description_font.path, int(image_width * 0.025))
    status_f  = ImageFont.truetype(capacity_name_font.path, int(image_width * 0.02))
    credits_f = ImageFont.truetype(credits_font.path, int(image_width * 0.015))

    title_pos   = (int(image_width * 0.015), int(image_height * 0.01))
    desc_top    = int(image_height * 0.15)
    status_pos  = (int(image_width * 0.01), int(image_height * 0.95))
    credits_pos = (int(image_width * 0.99), int(image_height * 0.95))
    line_height = int(image_width * 0.025) * 2

    draw.text(title_pos, event.name, font=title_f,
              fill=(255, 255, 255, 255), stroke_fill=(0, 0, 0, 255), stroke_width=2)

    for i, line in enumerate(textwrap.wrap(event.description, width=100)):
        draw.text(
            (title_pos[0], desc_top + i * line_height),
            line,
            font=desc_f,
            fill=(255, 255, 255, 255),
            stroke_fill=(0, 0, 0, 255),
            stroke_width=2,
        )

    draw.text(status_pos, "Event Status:", font=status_f,
              fill=(255, 255, 255, 255), stroke_fill=(0, 0, 0, 255), stroke_width=1)

    is_live = event.end_date > datetime.now(timezone.utc)
    draw.text(
        (status_pos[0] + int(image_width * 0.12), status_pos[1]),
        "Live!" if is_live else "Ended!",
        font=status_f,
        fill=(0, 255, 0, 255) if is_live else (255, 0, 0, 255),
        stroke_fill=(0, 0, 0, 255),
        stroke_width=1,
    )

    draw.text(credits_pos, "Created by Array_YE", font=credits_f,
              fill=(255, 255, 255, 255), stroke_fill=(0, 0, 0, 255), stroke_width=2, anchor="ra")

    return image