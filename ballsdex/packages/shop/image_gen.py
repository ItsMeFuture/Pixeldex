"""
Shop grid image generator.
Canvas: 2990x2200 (exported at 1000% from 299x220).
Supports animated pixel-art backgrounds/overlays, per-section custom fonts,
bundles, and custom item images.
"""
from __future__ import annotations

import logging
import textwrap
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence

from ballsdex.core.image_generator.image_gen import credits_font, stats_font, title_font

if TYPE_CHECKING:
    from ballsdex.packages.shop.tortoise_models import ShopItem, ShopSection

log = logging.getLogger("ballsdex.packages.shop.image_gen")

MEDIA_PATH = "./admin_panel/media/"

GRID_SLOTS = 3

# ═══════════════════════════════════════════════════════════════════════════
# EXACT MEASUREMENTS FROM 299x220 CANVAS (multiplied by 10)
# ═══════════════════════════════════════════════════════════════════════════
SCALE = 10

CANVAS_WIDTH = 299 * SCALE      # 2990
CANVAS_HEIGHT = 220 * SCALE     # 2200

CONTENT_WIDTH = 287 * SCALE     # 2870
CONTENT_HEIGHT = 122 * SCALE    # 1220
CONTENT_X = 6 * SCALE           # 60
CONTENT_Y = 98 * SCALE          # 980

CELL_WIDTH = 79 * SCALE         # 790
CELL_HEIGHT = 109 * SCALE       # 1090
CELL_GAP_H = 8 * SCALE          # 80
CELL_PAD_H = 17 * SCALE         # 170
CELL_PAD_V = 11 * SCALE         # 110

STATUS_WIDTH = 73 * SCALE       # 730
STATUS_HEIGHT = 8 * SCALE       # 80
STATUS_X = 3 * SCALE            # 30
STATUS_Y = 97 * SCALE           # 970

PRICE_WIDTH = 55 * SCALE        # 550
PRICE_HEIGHT = 10 * SCALE       # 100
PRICE_X = 12 * SCALE            # 120
PRICE_Y = 85 * SCALE            # 850

DESC_WIDTH = 71 * SCALE         # 710
DESC_HEIGHT = 25 * SCALE        # 250
DESC_X = 4 * SCALE              # 40
DESC_Y = 57 * SCALE             # 570

NAME_WIDTH = 71 * SCALE         # 710
NAME_HEIGHT = 11 * SCALE        # 110
NAME_X = 4 * SCALE              # 40
NAME_Y = 45 * SCALE             # 450

ARTWORK_WIDTH = 38 * SCALE      # 380
ARTWORK_HEIGHT = 38 * SCALE     # 380
ARTWORK_X = 4 * SCALE           # 40
ARTWORK_Y = 4 * SCALE           # 40

STATS_WIDTH = 31 * SCALE        # 310
STATS_HEIGHT = 38 * SCALE       # 380
STATS_X = 44 * SCALE            # 440
STATS_Y = 4 * SCALE             # 40

QTY_SIZE = 10 * SCALE           # 100
QTY_GAP_BOTTOM = 2 * SCALE      # 20
QTY_GAP_RIGHT = 2 * SCALE       # 20

BAR_WIDTH = 27 * SCALE          # 270
BAR_HEIGHT = 7 * SCALE          # 70
BAR_X = 2 * SCALE               # 20
BAR_TOP_GAP = 3 * SCALE         # 30
BAR_GAP = 1 * SCALE             # 10

ARROW_SIZE = 18 * SCALE         # 180
ARROW_LEFT_X = 2 * SCALE        # 20
ARROW_RIGHT_X = 279 * SCALE     # 2790
ARROW_Y = 151 * SCALE           # 1510

# ── Badges ──
SALE_BADGE_HEIGHT = 8 * SCALE
SALE_BADGE_PAD = 2 * SCALE
SALE_BADGE_FILL = (200, 255, 120, 240)
SALE_BADGE_OUTLINE = (120, 200, 50, 255)
SALE_BADGE_TEXT = (30, 80, 20, 255)

BUNDLE_BADGE_FILL = (100, 200, 255, 240)
BUNDLE_BADGE_OUTLINE = (50, 120, 200, 255)
BUNDLE_BADGE_TEXT = (20, 40, 80, 255)

# ═══════════════════════════════════════════════════════════════════════════
# CACHE
# ═══════════════════════════════════════════════════════════════════════════
_grid_cache: dict[tuple, bytes] = {}


def _effective_price(item, section):
    base = item.current_price
    if section and section.is_on_sale:
        return int(base * (100 - section.discount_percent) / 100)
    return base


def _effective_discount(item, section):
    if not section or not section.is_on_sale:
        return item.discount_percent if item.is_on_sale else 0
    item_mult = (100 - item.discount_percent) / 100.0 if item.is_on_sale else 1.0
    section_mult = (100 - section.discount_percent) / 100.0
    return 100 - int(100 * item_mult * section_mult)


# ═══════════════════════════════════════════════════════════════════════════
# FONT HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _load_section_font(
    section: "ShopSection",
    attr_path: str,
    attr_size: str,
    default_font,
    default_size: int,
) -> ImageFont.FreeTypeFont:
    path = getattr(section, attr_path, None)
    size = getattr(section, attr_size, default_size) or default_size
    if path:
        try:
            full = Path(MEDIA_PATH) / path.lstrip("/")
            return ImageFont.truetype(str(full), size)
        except Exception as e:
            log.warning("Failed to load %s font '%s': %s", attr_path, path, e)
    return ImageFont.truetype(default_font.path, size)


def _get_section_fonts(section: "ShopSection") -> dict[str, ImageFont.FreeTypeFont]:
    return {
        "name": _load_section_font(section, "font_name", "font_name_size", title_font, 56),
        "price": _load_section_font(section, "font_price", "font_price_size", stats_font, 48),
        "desc": _load_section_font(section, "font_desc", "font_desc_size", credits_font, 38),
        "stats": _load_section_font(section, "font_stats", "font_stats_size", credits_font, 34),
        "empty": _load_section_font(section, "font_empty", "font_empty_size", stats_font, 80),
        "qty": _load_section_font(section, "font_qty", "font_qty_size", stats_font, 44),
    }


# ═══════════════════════════════════════════════════════════════════════════
# DRAW HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _draw_text(
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
    stroke_width: int = 2,
    stroke_fill: tuple = (0, 0, 0, 255),
    text_align: str = "center",
    vertical_align: str = "center",
    show_boundaries: bool = True,
) -> None:
    x, y = position

    if centered_boundary:
        x = (CANVAS_WIDTH - boundary_width) // 2 if boundary_width < CANVAS_WIDTH else 0

    lines = (
        textwrap.wrap(text, width=int(boundary_width / (font.size / 2.5)))
        if wrap_text
        else [text]
    )

    h = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        while resize_text and (w > boundary_width or h * len(lines) > boundary_height):
            font = ImageFont.truetype(font.path, font.size - 1)
            bbox = draw.textbbox((0, 0), line, font=font)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    if vertical_align == "center":
        y += (boundary_height - h * len(lines)) / 2
    elif vertical_align == "bottom":
        y += boundary_height - h * len(lines)

    if show_boundaries:
        draw.rectangle(
            [(x, position[1]), (x + boundary_width, position[1] + boundary_height)],
            outline=(255, 0, 0, 255),
            width=2,
        )

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


def _draw_stat_bar(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    x: int,
    y: int,
    width: int,
    height: int,
    fill: tuple = (255, 255, 255, 255),
    bar_color: tuple = (255, 80, 80, 255),
) -> None:
    draw.rectangle([x, y, x + width, y + height], outline=bar_color, width=2)
    text_x = x + 14
    text_y = y + (height - font.size) // 2 - 2
    draw.text(
        (text_x, text_y), text, font=font, fill=fill,
        stroke_width=1, stroke_fill=(0, 0, 0, 255),
    )


# ═══════════════════════════════════════════════════════════════════════════
# ANIMATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def load_gif_frames(path: str):
    img = Image.open(path)
    frames = []
    canvas = Image.new("RGBA", img.size, (0, 0, 0, 0))
    for frame in ImageSequence.Iterator(img):
        disposal = getattr(frame, "disposal_method", frame.info.get("disposal", 0))
        if disposal == 2:
            canvas = Image.new("RGBA", img.size, (0, 0, 0, 0))
        rgba_frame = frame.convert("RGBA")
        canvas.paste(rgba_frame, (0, 0), rgba_frame)
        frames.append(canvas.copy())
    return frames, frame.info.get("duration", 100)

# ═══════════════════════════════════════════════════════════════════════════
# IMAGE PATH HELPER
# ═══════════════════════════════════════════════════════════════════════════

def _get_item_image_path(item: "ShopItem") -> str:
    if item.custom_image:
        return item.custom_image
    if item.output_ball:
        return item.output_ball.wild_card
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# CORE UI RENDERER
# ═══════════════════════════════════════════════════════════════════════════

def _render_shop_ui(
    section: "ShopSection",
    items: list["ShopItem"],
    item_status: dict[int, str],
    page: int,
    total_pages: int,
    fonts: dict[str, ImageFont.FreeTypeFont],
    show_boundaries: bool = True,
    skip_arrows: bool = False,
) -> Image.Image:
    ui = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ui)

    item_name_font = fonts["name"]
    item_price_font = fonts["price"]
    item_desc_font = fonts["desc"]
    stat_font = fonts["stats"]
    empty_font = fonts["empty"]
    qty_font = fonts["qty"]

    # Content area outline
    if show_boundaries:
        draw.rectangle(
            [CONTENT_X, CONTENT_Y, CONTENT_X + CONTENT_WIDTH, CONTENT_Y + CONTENT_HEIGHT],
            outline=(0, 255, 0, 255), width=2,
        )

    if not items:
        _draw_text(
            draw=draw, text="No items available", font=empty_font,
            position=(0, CONTENT_Y + CONTENT_HEIGHT // 2 - 50),
            boundary_width=CANVAS_WIDTH, boundary_height=100,
            resize_text=True, centered_boundary=True,
            text_align="center", vertical_align="center",
            show_boundaries=show_boundaries,
        )
    else:
        for idx, item in enumerate(items):
            col = idx % 3
            cell_x = CONTENT_X + CELL_PAD_H + col * (CELL_WIDTH + CELL_GAP_H)
            cell_y = CONTENT_Y + CELL_PAD_V

            is_bundle = getattr(item, 'is_bundle', False)
            bundle_items = list(item.bundle_items) if is_bundle else []
            ball = item.output_ball

            # Cell outline
            if show_boundaries:
                draw.rectangle(
                    [cell_x, cell_y, cell_x + CELL_WIDTH, cell_y + CELL_HEIGHT],
                    outline=(255, 255, 255, 255), width=2,
                )

            # Sale badge
            discount = _effective_discount(item, section)
            if discount > 0:
                sale_text = f"-{discount}%"
                sale_bbox = draw.textbbox((0, 0), sale_text, font=qty_font)
                sale_text_w = sale_bbox[2] - sale_bbox[0]
                sale_text_h = sale_bbox[3] - sale_bbox[1]
                pad_x, pad_y = 12, 6
                badge_w = sale_text_w + pad_x * 2
                badge_h = max(sale_text_h + pad_y * 2, SALE_BADGE_HEIGHT)
                badge_x = cell_x + CELL_WIDTH - SALE_BADGE_PAD - badge_w
                badge_y = cell_y - badge_h
                draw.rectangle(
                    [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
                    fill=SALE_BADGE_FILL, outline=SALE_BADGE_OUTLINE, width=2,
                )
                draw.text(
                    (badge_x + (badge_w - sale_text_w) / 2, badge_y + (badge_h - sale_text_h) / 2),
                    sale_text, font=qty_font, fill=SALE_BADGE_TEXT,
                    stroke_width=2, stroke_fill=(255, 255, 255, 255),
                )

            # Bundle badge
            if is_bundle:
                bundle_text = "BUNDLE"
                b_bbox = draw.textbbox((0, 0), bundle_text, font=qty_font)
                b_text_w = b_bbox[2] - b_bbox[0]
                b_text_h = b_bbox[3] - b_bbox[1]
                b_pad_x, b_pad_y = 12, 6
                b_badge_w = b_text_w + b_pad_x * 2
                b_badge_h = max(b_text_h + b_pad_y * 2, SALE_BADGE_HEIGHT)
                b_badge_x = cell_x + SALE_BADGE_PAD
                b_badge_y = cell_y - b_badge_h
                draw.rectangle(
                    [b_badge_x, b_badge_y, b_badge_x + b_badge_w, b_badge_y + b_badge_h],
                    fill=BUNDLE_BADGE_FILL, outline=BUNDLE_BADGE_OUTLINE, width=2,
                )
                draw.text(
                    (b_badge_x + (b_badge_w - b_text_w) / 2, b_badge_y + (b_badge_h - b_text_h) / 2),
                    bundle_text, font=qty_font, fill=BUNDLE_BADGE_TEXT,
                    stroke_width=2, stroke_fill=(255, 255, 255, 255),
                )

            # Artwork
            art_x = cell_x + ARTWORK_X
            art_y = cell_y + ARTWORK_Y
            if show_boundaries:
                draw.rectangle(
                    [art_x, art_y, art_x + ARTWORK_WIDTH, art_y + ARTWORK_HEIGHT],
                    outline=(0, 0, 139, 255), width=3,
                )

            image_path = _get_item_image_path(item)
            try:
                art_path = Path(MEDIA_PATH) / image_path.lstrip("/")
                with Image.open(art_path).convert("RGBA") as art:
                    art.thumbnail((ARTWORK_WIDTH - 16, ARTWORK_HEIGHT - 16), Image.Resampling.NEAREST)
                    artwork_bg = Image.new("RGBA", (ARTWORK_WIDTH, ARTWORK_HEIGHT), (0, 0, 0, 0))
                    offset_x = (ARTWORK_WIDTH - art.width) // 2
                    offset_y = (ARTWORK_HEIGHT - art.height) // 2
                    artwork_bg.paste(art, (offset_x, offset_y), art)
                    ui.paste(artwork_bg, (art_x, art_y), artwork_bg)
            except Exception:
                log.warning("Failed to load artwork for item %s (path: %s)", item.pk, image_path)
                _draw_text(
                    draw=draw, text="?", font=empty_font,
                    position=(art_x, art_y),
                    boundary_width=ARTWORK_WIDTH, boundary_height=ARTWORK_HEIGHT,
                    resize_text=True, text_align="center", vertical_align="center",
                    fill=(80, 100, 180, 255),
                    show_boundaries=show_boundaries,
                )

            # Quantity badge
            if item.output_quantity > 1:
                qty_text = f"x{item.output_quantity}"
                qty_box_x = art_x + ARTWORK_WIDTH - QTY_SIZE - QTY_GAP_RIGHT
                qty_box_y = art_y + ARTWORK_HEIGHT - QTY_SIZE - QTY_GAP_BOTTOM
                if show_boundaries:
                    draw.rectangle(
                        [qty_box_x, qty_box_y, qty_box_x + QTY_SIZE, qty_box_y + QTY_SIZE],
                        outline=(255, 105, 180, 255), width=2,
                    )
                _draw_text(
                    draw=draw, text=qty_text, font=qty_font,
                    position=(qty_box_x, qty_box_y),
                    boundary_width=QTY_SIZE, boundary_height=QTY_SIZE,
                    resize_text=True, text_align="center", vertical_align="center",
                    fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0, 255),
                    show_boundaries=show_boundaries,
                )

            # Stats box
            stats_x = cell_x + STATS_X
            stats_y = cell_y + STATS_Y
            if show_boundaries:
                draw.rectangle(
                    [stats_x, stats_y, stats_x + STATS_WIDTH, stats_y + STATS_HEIGHT],
                    outline=(200, 200, 200, 255), width=2,
                )

            bar_x = stats_x + BAR_X
            bar_y = stats_y + BAR_TOP_GAP

            if is_bundle:
                total_balls = sum(bi.quantity for bi in bundle_items)
                _draw_stat_bar(draw, "📦 Bundle", stat_font, bar_x, bar_y, BAR_WIDTH, BAR_HEIGHT,
                               fill=(255, 255, 255, 255), bar_color=(100, 150, 255, 255))
                bar_y += BAR_HEIGHT + BAR_GAP
                type_text = f"{len(bundle_items)} types" if bundle_items else "Empty"
                _draw_stat_bar(draw, type_text, stat_font, bar_x, bar_y, BAR_WIDTH, BAR_HEIGHT,
                               fill=(255, 255, 255, 255), bar_color=(100, 220, 100, 255))
                bar_y += BAR_HEIGHT + BAR_GAP
                count_text = f"{total_balls} balls" if bundle_items else "0 balls"
                _draw_stat_bar(draw, count_text, stat_font, bar_x, bar_y, BAR_WIDTH, BAR_HEIGHT,
                               fill=(255, 255, 255, 255), bar_color=(255, 200, 80, 255))
            else:
                if item.output_health_bonus is not None:
                    sign = "+" if item.output_health_bonus >= 0 else ""
                    hp_text = f"HP: {sign}{item.output_health_bonus}%"
                else:
                    hp_text = "HP: Random"
                _draw_stat_bar(draw, hp_text, stat_font, bar_x, bar_y, BAR_WIDTH, BAR_HEIGHT,
                               fill=(255, 255, 255, 255), bar_color=(255, 60, 60, 255))
                bar_y += BAR_HEIGHT + BAR_GAP

                if item.output_attack_bonus is not None:
                    sign = "+" if item.output_attack_bonus >= 0 else ""
                    atk_text = f"ATK: {sign}{item.output_attack_bonus}%"
                else:
                    atk_text = "ATK: Random"
                _draw_stat_bar(draw, atk_text, stat_font, bar_x, bar_y, BAR_WIDTH, BAR_HEIGHT,
                               fill=(255, 255, 255, 255), bar_color=(255, 180, 0, 255))
                bar_y += BAR_HEIGHT + BAR_GAP

                if item.output_special:
                    special_text = f"◆ {item.output_special.name}"
                    _draw_stat_bar(draw, special_text, stat_font, bar_x, bar_y, BAR_WIDTH, BAR_HEIGHT,
                                   fill=(255, 255, 255, 255), bar_color=(180, 80, 220, 255))
                else:
                    _draw_stat_bar(draw, "No Special", stat_font, bar_x, bar_y, BAR_WIDTH, BAR_HEIGHT,
                                   fill=(180, 180, 180, 255), bar_color=(100, 100, 100, 255))

            # Name
            name_x = cell_x + NAME_X
            name_y = cell_y + NAME_Y
            if show_boundaries:
                draw.rectangle(
                    [name_x, name_y, name_x + NAME_WIDTH, name_y + NAME_HEIGHT],
                    outline=(0, 255, 255, 255), width=2,
                )
            name_text = item.name or (ball.country if ball else "Unknown")
            _draw_text(
                draw=draw, text=name_text, font=item_name_font,
                position=(name_x, name_y),
                boundary_width=NAME_WIDTH, boundary_height=NAME_HEIGHT,
                resize_text=True, wrap_text=False,
                text_align="center", vertical_align="center",
                fill=(0, 255, 255, 255), stroke_width=3, stroke_fill=(0, 60, 80, 255),
                show_boundaries=show_boundaries,
            )

            # Description
            desc_x = cell_x + DESC_X
            desc_y = cell_y + DESC_Y
            if item.description:
                if show_boundaries:
                    draw.rectangle(
                        [desc_x, desc_y, desc_x + DESC_WIDTH, desc_y + DESC_HEIGHT],
                        outline=(80, 150, 255, 255), width=2,
                    )
                _draw_text(
                    draw=draw, text=item.description, font=item_desc_font,
                    position=(desc_x + 10, desc_y + 4),
                    boundary_width=DESC_WIDTH - 20, boundary_height=DESC_HEIGHT - 8,
                    resize_text=True, wrap_text=True,
                    text_align="center", vertical_align="center",
                    fill=(150, 200, 255, 255), stroke_width=1,
                    show_boundaries=show_boundaries,
                )
            else:
                if show_boundaries:
                    draw.rectangle(
                        [desc_x, desc_y, desc_x + DESC_WIDTH, desc_y + DESC_HEIGHT],
                        outline=(80, 150, 255, 150), width=1,
                    )

            # Price
            price_x = cell_x + PRICE_X
            price_y = cell_y + PRICE_Y
            if show_boundaries:
                draw.rectangle(
                    [price_x, price_y, price_x + PRICE_WIDTH, price_y + PRICE_HEIGHT],
                    outline=(100, 255, 100, 255), width=2,
                )

            icon_y = price_y + 10
            price_area_x = price_x + 16
            price_area_width = PRICE_WIDTH - 32
            price_boundary_height = PRICE_HEIGHT - 20

            try:
                icon_path = Path(MEDIA_PATH) / item.currency.icon.lstrip("/")
                with Image.open(icon_path).convert("RGBA") as icon:
                    # ── Currency icon size ──
                    ICON_SIZE = 64          # change this (was 48)
                    ICON_CANVAS = 68        # ICON_SIZE + 4
                    ICON_RESERVE = 80       # space reserved for icon + padding
                    TEXT_OFFSET = 74        # ICON_CANVAS + 6

                    icon.thumbnail((ICON_SIZE, ICON_SIZE), Image.Resampling.NEAREST)
                    icon_bg = Image.new("RGBA", (ICON_CANVAS, ICON_CANVAS), (0, 0, 0, 0))
                    offset_x = (ICON_CANVAS - icon.width) // 2
                    offset_y = (ICON_CANVAS - icon.height) // 2
                    icon_bg.paste(icon, (offset_x, offset_y), icon)

                    icon_reserved_width = ICON_RESERVE
                    text_max_width = price_area_width - icon_reserved_width

                    price_font = ImageFont.truetype(item_price_font.path, item_price_font.size)
                    price_text = f"{_effective_price(item, section):,}"
                    price_bbox = draw.textbbox((0, 0), price_text, font=price_font)
                    price_w = price_bbox[2] - price_bbox[0]

                    while price_w > text_max_width and price_font.size > 10:
                        price_font = ImageFont.truetype(price_font.path, price_font.size - 1)
                        price_bbox = draw.textbbox((0, 0), price_text, font=price_font)
                        price_w = price_bbox[2] - price_bbox[0]

                    total_w = icon_reserved_width + price_w
                    start_x = price_area_x + (price_area_width - total_w) // 2

                    ui.paste(icon_bg, (start_x, icon_y), icon_bg)
                    draw.text(
                        (start_x + TEXT_OFFSET, icon_y - 4),
                        price_text, font=price_font, fill=(100, 255, 150, 255),
                        stroke_width=3, stroke_fill=(0, 50, 15, 255),
                    )
            except Exception:
                _draw_text(
                    draw=draw, text=f"{_effective_price(item, section):,}", font=item_price_font,
                    position=(price_area_x, icon_y - 6),
                    boundary_width=price_area_width, boundary_height=price_boundary_height,
                    resize_text=True, text_align="center", vertical_align="center",
                    fill=(100, 255, 150, 255), stroke_width=3, stroke_fill=(0, 50, 15, 255),
                    show_boundaries=show_boundaries,
                )

            # Status
            status_x = cell_x + STATUS_X
            status_y = cell_y + STATUS_Y
            status = item_status.get(item.pk, "")
            if status:
                if show_boundaries:
                    draw.rectangle(
                        [status_x, status_y, status_x + STATUS_WIDTH, status_y + STATUS_HEIGHT],
                        outline=(180, 100, 220, 255), width=2,
                    )
                _draw_text(
                    draw=draw, text=status, font=item_desc_font,
                    position=(status_x, status_y),
                    boundary_width=STATUS_WIDTH, boundary_height=STATUS_HEIGHT,
                    resize_text=True, text_align="center", vertical_align="center",
                    fill=(255, 100, 100, 255) if "Sold out" in status else (220, 180, 255, 255),
                    stroke_width=1,
                    show_boundaries=show_boundaries,
                )

    # Navigation arrows
    if not skip_arrows:
        if page > 0 and section.arrow_left:
            try:
                arrow_path = Path(MEDIA_PATH) / section.arrow_left.lstrip("/")
                with Image.open(arrow_path).convert("RGBA") as arrow_img:
                    arrow = ImageOps.fit(arrow_img, (ARROW_SIZE, ARROW_SIZE), method=Image.Resampling.NEAREST)
                    ui.paste(arrow, (ARROW_LEFT_X, ARROW_Y), arrow)
            except Exception:
                log.warning("Failed to load left arrow %s", section.arrow_left)
        else:
            if show_boundaries:
                draw.rectangle(
                    [ARROW_LEFT_X, ARROW_Y, ARROW_LEFT_X + ARROW_SIZE, ARROW_Y + ARROW_SIZE],
                    outline=(255, 0, 255, 255), width=2,
                )

        if page < total_pages - 1 and section.arrow_right:
            try:
                arrow_path = Path(MEDIA_PATH) / section.arrow_right.lstrip("/")
                with Image.open(arrow_path).convert("RGBA") as arrow_img:
                    arrow = ImageOps.fit(arrow_img, (ARROW_SIZE, ARROW_SIZE), method=Image.Resampling.NEAREST)
                    ui.paste(arrow, (ARROW_RIGHT_X, ARROW_Y), arrow)
            except Exception:
                log.warning("Failed to load right arrow %s", section.arrow_right)
        else:
            if show_boundaries:
                draw.rectangle(
                    [ARROW_RIGHT_X, ARROW_Y, ARROW_RIGHT_X + ARROW_SIZE, ARROW_Y + ARROW_SIZE],
                    outline=(255, 0, 255, 255), width=2,
                )

    return ui

# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC RENDERERS
# ═══════════════════════════════════════════════════════════════════════════

async def render_section_grid(
    section: "ShopSection",
    items: list["ShopItem"],
    item_status: dict[int, str],
    page: int = 0,
    total_pages: int = 1,
    media_path: str = MEDIA_PATH,
    show_boundaries: bool = True,
) -> BytesIO:
    items = items[:3]

    def _dt_key(dt):
        if not dt:
            return ""
        return dt.isoformat()

    section_sale_token = (
        section.discount_percent if section.is_on_sale else 0,
        _dt_key(section.sale_start_date),
        _dt_key(section.sale_end_date),
    )
    item_sale_tokens = tuple(
        (i.pk, i.current_price, i.is_bundle, i.custom_image or "",
         tuple(bi.pk for bi in i.bundle_items) if i.is_bundle else ())
        for i in items
    )
    font_token = (
        section.font_name or "", section.font_name_size,
        section.font_price or "", section.font_price_size,
        section.font_desc or "", section.font_desc_size,
        section.font_stats or "", section.font_stats_size,
        section.font_empty or "", section.font_empty_size,
        section.font_qty or "", section.font_qty_size,
    )
    cache_key = (
        section.pk,
        item_sale_tokens,
        section_sale_token,
        font_token,
        page,
        total_pages,
    )

    if cache_key in _grid_cache:
        return BytesIO(_grid_cache[cache_key])

    # Background
    bg_path = Path(media_path) / section.background.lstrip("/")
    try:
        with Image.open(bg_path) as bg:
            canvas = ImageOps.fit(bg.convert("RGBA"), (CANVAS_WIDTH, CANVAS_HEIGHT), method=Image.Resampling.NEAREST)
    except Exception:
        log.warning("Failed to load background %s", bg_path)
        canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (30, 30, 40, 255))

    # Fonts + UI
    fonts = _get_section_fonts(section)
    ui = _render_shop_ui(section, items, item_status, page, total_pages, fonts, show_boundaries=show_boundaries)

    canvas = Image.alpha_composite(canvas, ui)

    # Overlay
    if section.overlay:
        overlay_path = Path(media_path) / section.overlay.lstrip("/")
        try:
            with Image.open(overlay_path).convert("RGBA") as overlay_img:
                overlay = ImageOps.fit(overlay_img, (CANVAS_WIDTH, CANVAS_HEIGHT), method=Image.Resampling.NEAREST)
                canvas = Image.alpha_composite(canvas, overlay)
        except Exception:
            log.warning("Failed to load overlay %s", overlay_path)

    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    buffer.seek(0)
    _grid_cache[cache_key] = buffer.getvalue()
    return BytesIO(_grid_cache[cache_key])


async def render_animated_grid(
    section: "ShopSection",
    items: list["ShopItem"],
    item_status: dict[int, str],
    page: int = 0,
    total_pages: int = 1,
    media_path: str = MEDIA_PATH,
) -> BytesIO:
    items = items[:3]

    bg_frames, bg_dur = load_gif_frames(Path(media_path) / section.background.lstrip("/"))

    ov_frames = []
    ov_dur = bg_dur
    if section.overlay:
        ov_frames, ov_dur = load_gif_frames(Path(media_path) / section.overlay.lstrip("/"))

        if len(ov_frames) != len(bg_frames):
            if len(ov_frames) == 1:
                ov_frames = ov_frames * len(bg_frames)
            elif len(bg_frames) == 1:
                bg_frames = bg_frames * len(ov_frames)
            else:
                raise ValueError(
                    f"Background ({len(bg_frames)} frames) and overlay ({len(ov_frames)} frames) "
                    "must have the same frame count for index-synced animation, "
                    "unless one of them is a single static frame."
                )

    # Load arrow frames (animated or static)
    left_arrow_frames = []
    if section.arrow_left:
        try:
            left_arrow_frames, _ = load_gif_frames(Path(media_path) / section.arrow_left.lstrip("/"))
        except Exception:
            log.warning("Failed to load left arrow %s", section.arrow_left)

    right_arrow_frames = []
    if section.arrow_right:
        try:
            right_arrow_frames, _ = load_gif_frames(Path(media_path) / section.arrow_right.lstrip("/"))
        except Exception:
            log.warning("Failed to load right arrow %s", section.arrow_right)

    frame_count = len(bg_frames)

    fonts = _get_section_fonts(section)
    ui = _render_shop_ui(section, items, item_status, page, total_pages, fonts, show_boundaries=True, skip_arrows=True)

    output_frames = []
    for i in range(frame_count):
        bg = ImageOps.fit(
            bg_frames[i].convert("RGBA"), (CANVAS_WIDTH, CANVAS_HEIGHT),
            method=Image.Resampling.NEAREST,
        )
        frame = Image.alpha_composite(bg, ui)

        if ov_frames:
            ov = ImageOps.fit(
                ov_frames[i].convert("RGBA"), (CANVAS_WIDTH, CANVAS_HEIGHT),
                method=Image.Resampling.NEAREST,
            )
            frame = Image.alpha_composite(frame, ov)

        # Paste animated arrows per frame
        if left_arrow_frames and page > 0:
            la = ImageOps.fit(
                left_arrow_frames[i % len(left_arrow_frames)].convert("RGBA"),
                (ARROW_SIZE, ARROW_SIZE),
                method=Image.Resampling.NEAREST,
            )
            frame.paste(la, (ARROW_LEFT_X, ARROW_Y), la)

        if right_arrow_frames and page < total_pages - 1:
            ra = ImageOps.fit(
                right_arrow_frames[i % len(right_arrow_frames)].convert("RGBA"),
                (ARROW_SIZE, ARROW_SIZE),
                method=Image.Resampling.NEAREST,
            )
            frame.paste(ra, (ARROW_RIGHT_X, ARROW_Y), ra)

        output_frames.append(frame)

    output_dur = min(bg_dur, ov_dur) if ov_frames else bg_dur

    buffer = BytesIO()
    output_frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=output_frames[1:],
        duration=output_dur,
        loop=0,
        optimize=True,
    )
    buffer.seek(0)
    return buffer

def invalidate_cache(section_id: int | None = None):
    if section_id is None:
        _grid_cache.clear()
    else:
        for key in list(_grid_cache.keys()):
            if key[0] == section_id:
                del _grid_cache[key]