import os
import io
import requests
from PIL import Image, ImageDraw, ImageFont

def get_font(size: int, bold: bool = False):
    """Load cross-platform TrueType font (Windows, Linux Docker, macOS)."""
    font_names = [
        # Linux / Docker paths (DejaVu, Liberation, FreeSans)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        
        # Windows paths
        "C:\\Windows\\Fonts\\segoeuib.ttf" if bold else "C:\\Windows\\Fonts\\segoeui.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\calibrib.ttf" if bold else "C:\\Windows\\Fonts\\calibri.ttf",
        
        # macOS paths
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
    ]
    
    for name in font_names:
        if os.path.exists(name):
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                pass
                
    # Auto-fallback: Ensure clean TTF is downloaded into local fonts directory if system TTF missing
    local_font_dir = os.path.join(os.path.dirname(__file__), "fonts")
    os.makedirs(local_font_dir, exist_ok=True)
    local_font_path = os.path.join(local_font_dir, "DejaVuSans.ttf")
    
    if not os.path.exists(local_font_path):
        try:
            url = "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans.ttf"
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                with open(local_font_path, "wb") as f:
                    f.write(r.content)
        except Exception as e:
            print(f"[Warning] Font download fallback failed: {e}")
            
    if os.path.exists(local_font_path):
        try:
            return ImageFont.truetype(local_font_path, size)
        except Exception:
            pass

    return ImageFont.load_default()

def wrap_text(text: str, font: ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> list:
    """Wrap text to fit within max_width in pixels."""
    words = text.split()
    lines = []
    current_line = []
    
    for word in words:
        current_line.append(word)
        test_str = ' '.join(current_line)
        bbox = draw.textbbox((0, 0), test_str, font=font)
        w = bbox[2] - bbox[0]
        if w > max_width:
            current_line.pop()
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]
            
    if current_line:
        lines.append(' '.join(current_line))
    return lines

def download_image(url: str, size: tuple) -> Image.Image:
    """Download image thumbnail and resize/crop to size."""
    try:
        if url.startswith("http"):
            resp = requests.get(url, timeout=4, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                # Center crop to aspect ratio
                target_w, target_h = size
                img_w, img_h = img.size
                
                target_ratio = target_w / target_h
                img_ratio = img_w / img_h
                
                if img_ratio > target_ratio:
                    new_w = int(img_h * target_ratio)
                    left = (img_w - new_w) // 2
                    img = img.crop((left, 0, left + new_w, img_h))
                else:
                    new_h = int(img_w / target_ratio)
                    top = (img_h - new_h) // 2
                    img = img.crop((0, top, img_w, top + new_h))
                    
                return img.resize(size, Image.Resampling.LANCZOS)
    except Exception as e:
        print(f"[Info] Thumbnail download fallback for {url[:30]}: {e}")
    
    # Return abstract gradient fallback thumbnail
    fallback = Image.new("RGB", size, (18, 24, 38))
    f_draw = ImageDraw.Draw(fallback)
    for y in range(size[1]):
        r = int(18 + (y / size[1]) * 20)
        g = int(24 + (y / size[1]) * 30)
        b = int(38 + (y / size[1]) * 50)
        f_draw.line([(0, y), (size[0], y)], fill=(r, g, b))
    return fallback

def create_visual_card(article: dict, output_path: str) -> str:
    """
    Renders a vertical 9:16 mobile phone portrait card.
    Card Dimensions: 720 x 1280 px (Exact 9:16 aspect ratio)
    Style: Minimalist, clean obsidian dark theme.
    """
    WIDTH, HEIGHT = 720, 1280
    category = article.get("category", "TECH").upper()

    # Base Background with Dark Obsidian Vertical Gradient
    card = Image.new("RGBA", (WIDTH, HEIGHT), (9, 12, 18, 255))
    draw = ImageDraw.Draw(card)

    for y in range(HEIGHT):
        factor = y / HEIGHT
        r = int(9 + factor * 10)
        g = int(12 + factor * 14)
        b = int(18 + factor * 22)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b, 255))

    # Outer Minimalist Glassmorphism Frame
    padding = 24
    card_rect = [padding, padding, WIDTH - padding, HEIGHT - padding]
    draw.rounded_rectangle(
        card_rect,
        radius=28,
        fill=(16, 21, 31, 255),
        outline=(34, 43, 60, 255),
        width=2
    )

    # Color Palette per Category
    if category == "TECH":
        badge_bg = (99, 102, 241)        # Indigo / Violet
        accent_color = (129, 140, 248)    # Light Indigo Glow
        category_label = "TECH INDUSTRY"
    else:
        badge_bg = (16, 185, 129)        # Emerald Green
        accent_color = (52, 211, 153)     # Mint Glow
        category_label = "FINANCE & MARKETS"

    # Typography sizes tuned for 720x1280 vertical layout
    font_badge = get_font(17, bold=True)
    font_meta = get_font(16, bold=False)
    font_title = get_font(32, bold=True)
    font_desc = get_font(21, bold=False)
    font_footer = get_font(15, bold=False)

    # 1. Header Area (Top Padding)
    top_y = 52
    left_x = 52
    content_w = WIDTH - (left_x * 2)

    # Category Pill Badge (Top Left)
    badge_w, badge_h = 190, 40
    draw.rounded_rectangle(
        [left_x, top_y, left_x + badge_w, top_y + badge_h],
        radius=20,
        fill=badge_bg
    )
    draw.text((left_x + 18, top_y + 9), category_label, font=font_badge, fill=(255, 255, 255))

    # Source & Date Meta (Top Right)
    source_str = f"{article.get('source', 'News')}"
    bbox = draw.textbbox((0, 0), source_str, font=font_meta)
    meta_w = bbox[2] - bbox[0]
    draw.text((WIDTH - left_x - meta_w, top_y + 10), source_str, font=font_meta, fill=(148, 163, 184))

    # 2. Hero Featured Thumbnail (Full width of inner container, 616x460)
    thumb_x = left_x
    thumb_y = top_y + 60
    thumb_w = content_w
    thumb_h = 460

    thumb_img = download_image(article.get("image_url", ""), (thumb_w, thumb_h))
    
    # Rounded Mask for Hero Thumbnail
    mask = Image.new("L", (thumb_w, thumb_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, thumb_w, thumb_h], radius=20, fill=255)
    
    card.paste(thumb_img, (thumb_x, thumb_y), mask)
    draw.rounded_rectangle(
        [thumb_x, thumb_y, thumb_x + thumb_w, thumb_y + thumb_h],
        radius=20,
        outline=(44, 54, 74),
        width=2
    )

    # Date Tag bar over bottom of image
    pub_date_str = article.get('published_at', 'Today')
    draw.text((thumb_x + 15, thumb_y + thumb_h + 15), pub_date_str, font=font_meta, fill=(100, 116, 139))

    # 3. Article Title (Bold, readable headline)
    curr_y = thumb_y + thumb_h + 46
    raw_title = article.get("title", "No Title")
    title_lines = wrap_text(raw_title, font_title, content_w, draw)[:4]
    
    for line in title_lines:
        draw.text((left_x, curr_y), line, font=font_title, fill=(248, 250, 252))
        curr_y += 44

    curr_y += 20

    # 4. Article Description Excerpt
    raw_desc = article.get("description", "")
    if raw_desc:
        desc_lines = wrap_text(raw_desc, font_desc, content_w, draw)[:6]
        for line in desc_lines:
            draw.text((left_x, curr_y), line, font=font_desc, fill=(148, 163, 184))
            curr_y += 32

    # 5. Bottom Phone Footer Action Bar
    footer_y = HEIGHT - 90
    draw.line([(left_x, footer_y), (WIDTH - left_x, footer_y)], fill=(32, 40, 56), width=1)

    # Accent Bar on left
    draw.rounded_rectangle([left_x, footer_y - 1, left_x + 140, footer_y + 2], radius=2, fill=accent_color)

    # Footer Branding
    draw.text((left_x, footer_y + 24), "MOBILE CARD  •  9:16 PORTRAIT", font=font_footer, fill=(100, 116, 139))

    # Read More link indicator
    read_more = "READ FULL STORY →"
    bbox_rm = draw.textbbox((0, 0), read_more, font=font_badge)
    rm_w = bbox_rm[2] - bbox_rm[0]
    draw.text((WIDTH - left_x - rm_w, footer_y + 23), read_more, font=font_badge, fill=accent_color)

    # Save PNG Card
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    card.convert("RGB").save(output_path, "PNG", quality=95)
    return output_path

def generate_all_cards(articles: list, output_dir: str = "output/cards") -> list:
    """Generate 9:16 vertical portrait visual PNG cards for all 20 articles."""
    os.makedirs(output_dir, exist_ok=True)
    generated_files = []
    
    for article in articles:
        idx = article.get("index", 1)
        filename = f"card_{idx:02d}_{article.get('category').lower()}.png"
        filepath = os.path.join(output_dir, filename)
        try:
            path = create_visual_card(article, filepath)
            article["card_image_path"] = path
            article["card_filename"] = filename
            generated_files.append(filepath)
            print(f"[Generated 9:16 Portrait Card {idx}/20] {filename}")
        except Exception as e:
            print(f"[Error] Failed to generate card {idx}: {e}")
            
    return generated_files

if __name__ == "__main__":
    from news_fetcher import get_latest_20_news
    news = get_latest_20_news()
    cards = generate_all_cards(news, "output/cards")
    print(f"Done! Generated {len(cards)} vertical 9:16 portrait visual PNG cards.")
