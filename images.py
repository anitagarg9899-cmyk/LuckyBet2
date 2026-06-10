from PIL import Image, ImageDraw, ImageFont
import io
from datetime import datetime

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

# Palette

BG       = (12,  20,  32)
CARD     = (26,  40,  64)
CARD2    = (20,  32,  52)
HEADER   = (36,  53,  85)
ACCENT   = (79, 195, 247)
GREEN    = (0,  230, 118)
RED      = (255, 71,  71)
GOLD     = (255, 215,  0)
DGOLD    = (184, 134,  11)
WHITE    = (255, 255, 255)
GRAY     = (136, 153, 170)
DARKGRAY = (74, 106, 138)
BLACK    = (0,   0,   0)

def fnt(size, bold=False, mono=False):
    try:
        path = FONT_MONO if mono else (FONT_BOLD if bold else FONT_REG)
        return ImageFont.truetype(path, size)
    except:
        return ImageFont.load_default()

def rr(draw, xy, r, fill=None, outline=None, width=1):
    """Draw a rounded rectangle."""
    draw.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=width)

def center_text(draw, text, font, cx, cy, color):
    bb = draw.textbbox((0, 0), text, font=font)
    w = bb[2] - bb[0]; h = bb[3] - bb[1]
    draw.text((cx - w // 2, cy - h // 2), text, fill=color, font=font)

def footer(draw, W, H, label="LuckyBet Casino"):
    draw.line([(20, H - 34), (W - 20, H - 34)], fill=(42, 63, 95), width=1)
    now = datetime.now().strftime("%m/%d/%Y, %I:%M:%S %p").lstrip("0")
    f = fnt(11)
    draw.text((22, H - 24), label, fill=DARKGRAY, font=f)
    bb = draw.textbbox((0, 0), now, font=f)
    draw.text((W - (bb[2] - bb[0]) - 22, H - 24), now, fill=DARKGRAY, font=f)

def to_buf(img):
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return buf

# Balance card

def balance_card(username, user_id, balance):
    W, H = 560, 210
    img = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(img)

    # outer card
    rr(d, [8, 8, W - 8, H - 8], 18, fill=CARD)
    # left accent stripe
    rr(d, [8, 8, 14, H - 8], 6, fill=ACCENT)

    # avatar circle
    cx, cy, cr = 72, H // 2, 42
    d.ellipse([cx - cr - 2, cy - cr - 2, cx + cr + 2, cy + cr + 2], fill=(18, 28, 46))
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(36, 53, 85))
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], outline=ACCENT, width=2)
    initial = (username[0].upper()) if username else "?"
    center_text(d, initial, fnt(34, bold=True), cx, cy - 2, ACCENT)

    # name + id
    d.text((128, 28), username, fill=WHITE, font=fnt(20, bold=True))
    d.text((128, 56), f"ID: {user_id}", fill=GRAY, font=fnt(13))

    # divider
    d.line([(128, 78), (W - 22, 78)], fill=(42, 63, 95), width=1)

    # label
    d.text((128, 86), "POINTS BALANCE", fill=ACCENT, font=fnt(12, bold=True))

    # big balance number
    bal_str = f"{balance:,}"
    d.text((128, 104), bal_str, fill=WHITE, font=fnt(40, bold=True))

    # sub-line
    usd = balance * 0.0037
    d.text((128, 158), f"R${balance:,}  \u2248  ${usd:.2f} USD", fill=GRAY, font=fnt(13))

    footer(d, W, H)
    return to_buf(img)

# Helpers

def make_card(W, H, title, subtitle=""):
    img = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(img)
    rr(d, [8, 8, W - 8, H - 8], 18, fill=CARD)
    # header band
    rr(d, [8, 8, W - 8, 54], 14, fill=HEADER)
    d.rectangle([8, 36, W - 8, 54], fill=HEADER)
    d.text((22, 14), title, fill=ACCENT, font=fnt(18, bold=True))
    if subtitle:
        bb = d.textbbox((0, 0), subtitle, font=fnt(13))
        d.text((W - (bb[2] - bb[0]) - 22, 20), subtitle, fill=GRAY, font=fnt(13))
    footer(d, W, H)
    return img, d

def result_banner(d, W, y, won, text):
    color = GREEN if won else RED
    rr(d, [20, y, W - 20, y + 46], 10, fill=(0, 50, 20) if won else (60, 10, 10))
    center_text(d, text, fnt(22, bold=True), W // 2, y + 23, color)

# Coin flip card

def coinflip_card(username, choice, result, won):
    W, H = 500, 340
    img, d = make_card(W, H, "\U0001fa99  Coin Flip", f"bet by {username}")

    # coin
    cx, cy, cr = W // 2, 180, 85
    d.ellipse([cx - cr + 4, cy - cr + 4, cx + cr + 4, cy + cr + 4], fill=(6, 12, 24))
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=DGOLD)
    d.ellipse([cx - cr + 8, cy - cr + 8, cx + cr - 8, cy + cr - 8], fill=GOLD)
    d.ellipse([cx - cr + 14, cy - cr + 14, cx - cr + 38, cy - cr + 38], fill=(255, 236, 110))
    lbl = "H" if result == "heads" else "T"
    center_text(d, lbl, fnt(56, bold=True), cx, cy - 2, DGOLD)

    result_banner(d, W, 280, won, f"Landed on {result.upper()}")
    return to_buf(img)

# Dice card

DICE_DOTS = {
    1: [(0, 0)],
    2: [(-1, -1), (1, 1)],
    3: [(-1, -1), (0, 0), (1, 1)],
    4: [(-1, -1), (1, -1), (-1, 1), (1, 1)],
    5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)],
    6: [(-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1)],
}

def draw_die(d, cx, cy, size, value, face=(230, 230, 235), dot=(30, 30, 30)):
    half = size // 2
    rr(d, [cx - half, cy - half, cx + half, cy + half], 14, fill=face, outline=(180,180,180), width=2)
    step = size // 3
    dr = max(6, size // 10)
    for (dx, dy) in DICE_DOTS[value]:
        px = cx + dx * step
        py = cy + dy * step
        d.ellipse([px - dr, py - dr, px + dr, py + dr], fill=dot)

def dice_card(username, guess, roll, won):
    W, H = 500, 340
    img, d = make_card(W, H, "\U0001f3b2  Dice Roll", f"bet by {username}")

    # two dice — guess on left, roll on right
    draw_die(d, W // 2 - 80, 180, 110, guess, face=(200, 210, 220), dot=(60, 80, 100))
    draw_die(d, W // 2 + 80, 180, 110, roll,  face=(230, 230, 235), dot=(30, 30, 30))

    d.text((W // 2 - 80 - 22, 238), "Guess", fill=GRAY, font=fnt(13))
    d.text((W // 2 + 80 - 16, 238), "Roll",  fill=GRAY, font=fnt(13))

    match_str = "= Match! x5" if won else "!= Your guess"
    label = f"Rolled {roll}  {match_str}"
    result_banner(d, W, 272, won, label)
    return to_buf(img)

# Slots card

SLOT_COLORS = {
    '\U0001f34e': ((180, 40,  40), (255, 120, 120)),   # apple  red
    '\U0001f34a': ((200, 100, 10), (255, 165,  50)),   # orange
    '\U0001f34b': ((180, 160, 10), (240, 230,  60)),   # lemon
    '\U0001f34c': ((180, 140, 20), (240, 200,  60)),   # banana
    '\u2b50':     ((180, 140,  0), (255, 215,   0)),   # star   gold
    '\U0001f48e': ((20,  60, 180), ( 80, 150, 255)),   # gem    blue
}
SLOT_LABELS = {
    '\U0001f34e': "AP",
    '\U0001f34a': "OR",
    '\U0001f34b': "LE",
    '\U0001f34c': "BA",
    '\u2b50': '\u2605',
    '\U0001f48e': '\u25c6',
}

def draw_slot_reel(d, cx, cy, size, symbol):
    half = size // 2
    bg, fg = SLOT_COLORS.get(symbol, ((60, 60, 60), (200, 200, 200)))
    rr(d, [cx - half, cy - half, cx + half, cy + half], 12, fill=bg, outline=fg, width=3)
    lbl = SLOT_LABELS.get(symbol, "?")
    center_text(d, lbl, fnt(38, bold=True), cx, cy - 2, fg)

def slots_card(username, symbols, won, label):
    W, H = 500, 340
    img, d = make_card(W, H, "\U0001f3b0  Slots", f"bet by {username}")

    positions = [W // 2 - 120, W // 2, W // 2 + 120]
    for i, (cx, sym) in enumerate(zip(positions, symbols)):
        draw_slot_reel(d, cx, 175, 100, sym)

    result_banner(d, W, 272, won, label)
    return to_buf(img)

# Roulette card

def roulette_card(username, choice, spin, result_color, won):
    W, H = 500, 340
    img, d = make_card(W, H, "\U0001f3a1  Roulette", f"bet by {username}")

    cx, cy, cr = W // 2, 175, 80
    wheel_color = (200, 40, 40) if result_color == "red" else \
                  (20, 20, 20)  if result_color == "black" else \
                  (10, 130, 60)
    d.ellipse([cx - cr + 4, cy - cr + 4, cx + cr + 4, cy + cr + 4], fill=(6, 12, 24))
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=wheel_color)
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], outline=(180, 180, 180), width=3)
    d.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=CARD)
    center_text(d, str(spin), fnt(28, bold=True), cx, cy - 2, WHITE)

    parity = ""
    if spin > 0:
        parity = "even" if spin % 2 == 0 else "odd"
    sub = f"{result_color.upper()}  {parity.upper()}  #{spin}"
    center_text(d, sub, fnt(14), W // 2, 258, GRAY)

    result_banner(d, W, 278, won, f"You bet {choice.upper()} \u2014 {'WIN!' if won else 'LOSS'}")
    return to_buf(img)

# Blackjack card

def draw_card_tile(d, cx, cy, value):
    w, h = 52, 72
    rr(d, [cx - w//2, cy - h//2, cx + w//2, cy + h//2], 8, fill=WHITE, outline=(180,180,180), width=1)
    lbl = "A" if value == 11 else str(value)
    center_text(d, lbl, fnt(20, bold=True), cx, cy - 2, (30, 30, 30))

def blackjack_card(username, player_cards, player_val, dealer_cards, dealer_val, won):
    W, H = 500, 360
    img, d = make_card(W, H, "\U0001f0cf  Blackjack", f"bet by {username}")

    # Player hand
    d.text((22, 68), "Your hand", fill=GRAY, font=fnt(13))
    px_start = 60
    for i, c in enumerate(player_cards[:6]):
        draw_card_tile(d, px_start + i * 62, 115, c)
    d.text((22, 148), f"Total: {player_val}", fill=ACCENT, font=fnt(15, bold=True))

    # Dealer hand
    d.text((22, 170), "Dealer hand", fill=GRAY, font=fnt(13))
    for i, c in enumerate(dealer_cards[:6]):
        draw_card_tile(d, px_start + i * 62, 218, c)
    d.text((22, 250), f"Total: {dealer_val}", fill=RED if dealer_val > 21 else ACCENT, font=fnt(15, bold=True))

    label = "YOU WIN!" if won else "YOU LOST"
    result_banner(d, W, 280, won, label)
    return to_buf(img)

# Mines result card

def mines_result_card(username, picks, multiplier, won):
    W, H = 500, 300
    img, d = make_card(W, H, "\u26cf\ufe0f  Mines", f"bet by {username}")

    # Stats
    stats = [
        ("Safe picks", str(picks)),
        ("Multiplier", f"\u00d7{multiplier}"),
    ]
    for i, (label, val) in enumerate(stats):
        x = 60 + i * 200
        rr(d, [x - 50, 80, x + 130, 180], 12, fill=CARD2)
        d.text((x - 30, 95), label, fill=GRAY, font=fnt(13))
        center_text(d, val, fnt(30, bold=True), x + 40, 148, ACCENT if not won else GREEN)

    label = f"Cashed out \u00d7{multiplier}" if won else "BOOM \u2014 Mine hit!"
    result_banner(d, W, 210, won, label)
    return to_buf(img)

# Ladder result card

def ladder_result_card(username, rung, multiplier, won, fell):
    W, H = 500, 320
    RUNGS_TOTAL = 8
    img, d = make_card(W, H, "\U0001fa9c  Ladder", f"bet by {username}")

    # Visual ladder
    rung_w = (W - 80) // RUNGS_TOTAL
    bar_y = 160
    for i in range(RUNGS_TOTAL):
        x = 40 + i * rung_w + rung_w // 2
        if i < rung:
            color = GREEN
        elif i == rung and fell:
            color = RED
        elif i == rung and won:
            color = GOLD
        else:
            color = (50, 70, 100)
        d.ellipse([x - 14, bar_y - 14, x + 14, bar_y + 14], fill=color)
        d.text((x - 5, bar_y - 9), str(i + 1), fill=BLACK if color != (50,70,100) else GRAY, font=fnt(14, bold=True))
        if i < RUNGS_TOTAL - 1:
            nx = 40 + (i + 1) * rung_w + rung_w // 2
            line_color = GREEN if i < rung - 1 else (50, 70, 100)
            d.line([(x + 14, bar_y), (nx - 14, bar_y)], fill=line_color, width=3)

    # Stats
    center_text(d, f"Rung {rung + 1} / {RUNGS_TOTAL}  \u2014  \u00d7{multiplier}", fnt(16, bold=True), W // 2, 210, ACCENT)

    if fell:
        label = f"Fell on rung {rung + 1} \u2014 LOST"
    elif rung >= RUNGS_TOTAL - 1:
        label = f"MAX! \u00d7{multiplier} \u2014 YOU WIN!"
    else:
        label = f"Cashed out \u00d7{multiplier} \u2014 WIN!"
    result_banner(d, W, 238, won, label)
    return to_buf(img)

# Admin addbal card

def addbal_card(admin_name, target_name, amount, old_bal, new_bal):
    W, H = 480, 200
    img = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(img)
    rr(d, [8, 8, W - 8, H - 8], 18, fill=CARD)
    rr(d, [8, 8, W - 8, 54], 14, fill=HEADER)
    d.rectangle([8, 36, W - 8, 54], fill=HEADER)
    d.text((22, 14), "\U0001f527  Admin — Balance Updated", fill=(255, 200, 0), font=fnt(16, bold=True))

    rows = [
        ("Target",   target_name),
        ("Change",   f"{'+ ' if amount > 0 else ''}{amount:,} R$"),
        ("Previous", f"R${old_bal:,}"),
        ("New Bal",  f"R${new_bal:,}"),
    ]
    col1, col2 = 22, 180
    y = 66
    for label, val in rows:
        d.text((col1, y), label, fill=GRAY, font=fnt(13))
        color = GREEN if (label == "Change" and amount > 0) else (RED if (label == "Change") else WHITE)
        d.text((col2, y), val, fill=color, font=fnt(13, bold=True))
        y += 26

    f_foot = fnt(11)
    d.text((22, H - 24), f"Action by {admin_name}", fill=DARKGRAY, font=f_foot)
    return to_buf(img)
