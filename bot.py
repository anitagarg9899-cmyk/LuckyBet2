import discord
from discord.ext import commands
import random
import json
import os
import asyncio
import hashlib
import hmac
import secrets
import re
import math
from datetime import datetime, timezone, timedelta
from images import (
    balance_card, coinflip_card, dice_card, slots_card,
    roulette_card, blackjack_card, addbal_card
)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.invites = True

bot = commands.Bot(command_prefix='.', intents=intents, help_command=None)

DB_FILE      = 'bot/user_data.json'
active_mines = {}
active_bj    = {}
invite_cache = {}   # guild_id -> {code: uses}

POINTS_TO_USD = 0.0037

RANKS = [
    (0,         "🥉 Bronze",   0xCD7F32),
    (5_000,     "🥈 Silver",   0xC0C0C0),
    (25_000,    "🥇 Gold",     0xFFD700),
    (100_000,   "💎 Platinum", 0x64C8FF),
    (500_000,   "👑 Diamond",  0xB464FF),
    (2_000_000, "⚡ VIP",      0xFF5000),
]
RANK_KEYS = ["bronze", "silver", "gold", "platinum", "diamond", "vip"]

def get_rank_info(total_wagered):
    rank = RANKS[0]; rank_idx = 0
    for i, entry in enumerate(RANKS):
        if total_wagered >= entry[0]:
            rank = entry; rank_idx = i
    next_rank = RANKS[rank_idx + 1] if rank_idx + 1 < len(RANKS) else None
    return rank, next_rank

def rank_key(rank_name):
    return rank_name.split()[-1].lower()

def fmt(points):
    usd = points * POINTS_TO_USD
    return f"R${points:,} (≈ ${usd:.2f})"

# ── Data ──────────────────────────────────────────────────────────────────────

def load_data():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def get_user(user_id):
    data = load_data(); uid = str(user_id)
    if uid not in data:
        data[uid] = {
            'balance': 0,
            'stats': {'wins': 0, 'losses': 0, 'total_wagered': 0, 'total_lost': 0},
            'last_daily': None, 'last_monthly': None,
            'wager_at_last_monthly': 0, 'rakeback_available': 0.0, 'clan': None,
            'bonus_received': 0, 'tips_sent': 0, 'tips_received': 0, 'total_withdrawn': 0,
        }
        save_data(data)
    u = data[uid]; changed = False
    for key, default in [
        ('last_daily', None), ('last_monthly', None), ('wager_at_last_monthly', 0),
        ('rakeback_available', 0.0), ('clan', None), ('bonus_received', 0),
        ('tips_sent', 0), ('tips_received', 0), ('total_withdrawn', 0),
        ('daily_invites', 0), ('daily_invites_date', None), ('total_invites', 0),
    ]:
        if key not in u: u[key] = default; changed = True
    if 'total_lost' not in u.get('stats', {}):
        u.setdefault('stats', {})['total_lost'] = 0; changed = True
    if changed: save_data(data)
    return data, uid

def get_user_balance(user_id):
    data, uid = get_user(user_id); return data[uid]['balance']

def resolve_bet(amount_str, balance):
    """Convert 'all', 'half', or a number string to an integer bet amount."""
    s = str(amount_str).lower().strip()
    if s == 'all':
        return balance
    if s == 'half':
        return max(1, balance // 2)
    try:
        return int(s)
    except ValueError:
        return None

def set_user_balance(user_id, amount):
    data, uid = get_user(user_id)
    data[uid]['balance'] = max(0, amount); save_data(data)

def add_to_stats(user_id, result, wager):
    data, uid = get_user(user_id); s = data[uid]['stats']
    s['total_wagered'] += wager
    if result:
        s['wins'] += 1
    else:
        s['losses'] += 1
        s['total_lost'] = s.get('total_lost', 0) + wager
        data[uid]['rakeback_available'] = data[uid].get('rakeback_available', 0.0) + wager * 0.002
    save_data(data)

def get_config():
    return load_data().get('__config__', {})

def save_config(cfg):
    data = load_data(); data['__config__'] = cfg; save_data(data)

def get_clans():
    return load_data().get('__clans__', {})

def save_clans(clans):
    data = load_data(); data['__clans__'] = clans; save_data(data)

def send_image(buf, filename='result.png'):
    buf.seek(0); return discord.File(buf, filename=filename)

# ── Rank Role Helper ──────────────────────────────────────────────────────────

async def assign_rank_role(guild, user_id):
    if not guild: return
    cfg = get_config(); rank_roles = cfg.get('rank_roles', {})
    if not rank_roles: return
    data, uid = get_user(user_id)
    total_wagered = data[uid]['stats']['total_wagered']
    current_rank, _ = get_rank_info(total_wagered)
    rkey = rank_key(current_rank[1])
    role_id = rank_roles.get(rkey)
    member = guild.get_member(user_id)
    if not member: return
    all_rank_ids = set(int(rid) for rid in rank_roles.values())
    to_remove = [r for r in member.roles if r.id in all_rank_ids]
    if to_remove:
        try: await member.remove_roles(*to_remove)
        except: pass
    if role_id:
        role = guild.get_role(int(role_id))
        if role:
            try: await member.add_roles(role)
            except: pass

# ── Provably Fair ─────────────────────────────────────────────────────────────

def generate_seeds():
    server_seed = secrets.token_hex(32)
    client_seed = secrets.token_hex(8)
    public_hash = hashlib.sha256(server_seed.encode()).hexdigest()
    return server_seed, client_seed, public_hash

def pf_mine_positions(server_seed, client_seed, mines_count, total=20):
    h = hmac.new(server_seed.encode(), client_seed.encode(), hashlib.sha256)
    rng_bytes = bytes.fromhex(h.hexdigest()); positions = list(range(total))
    for i in range(total - 1, 0, -1):
        j = rng_bytes[i % len(rng_bytes)] % (i + 1)
        positions[i], positions[j] = positions[j], positions[i]
    return set(positions[:mines_count])

# ── Crash Game ────────────────────────────────────────────────────────────────

CRASH_LOBBY_SECS = 20
CRASH_TICK       = 1.0   # seconds between multiplier updates

crash_state = {
    'phase':    'idle',   # idle | lobby | running | crashed
    'bets':     {},       # uid -> {'amount': int, 'start_bal': int, 'username': str}
    'cashed':   {},       # uid -> {'mult': float, 'profit': int}
    'crash_at': 1.0,
    'mult':     1.0,
    'message':  None,
    'channel_id': None,
    'task':     None,
    'view':     None,
    'guild_id': None,
}

def gen_crash_point():
    r = random.random()
    if r < 0.01: return 1.0  # 1% instant crash
    return min(round(0.99 / (1 - r), 2), 200.0)

def crash_mult_at(elapsed):
    return round(1.0 + elapsed * 0.12 + (elapsed ** 1.6) * 0.015, 2)

def crash_embed_build(phase, bets, cashed, mult=1.00, crash_at=None, color=0x1E90FF):
    if phase == 'lobby':
        title = "🚀  Crash — Lobby Open"
        desc  = f"Game starts in a moment!\nUse `.crash <amount>` to bet now.\n\n"
        color = 0x9B59B6
    elif phase == 'running':
        title = f"🚀  Crash — {mult:.2f}×  FLYING"
        desc  = f"**Current Multiplier:** `{mult:.2f}×`\nClick **Cash Out** before it crashes!\n\n"
        color = 0x00FF88 if mult < 3 else (0xFFD700 if mult < 7 else 0xFF5000)
    elif phase == 'crashed':
        title = f"💥  Crashed at {crash_at:.2f}×"
        desc  = f"**Crash Point:** `{crash_at:.2f}×`\n\n"
        color = 0xFF4444
    else:
        title = "🚀  Crash"; desc = ""; color = 0x1E90FF

    if bets:
        lines = []
        for uid, b in bets.items():
            if uid in cashed:
                c = cashed[uid]; sign = "+" if c['profit'] >= 0 else ""
                lines.append(f"✅ **{b['username']}** — cashed {c['mult']:.2f}× ({sign}R${c['profit']:,})")
            elif phase == 'crashed':
                lines.append(f"💥 **{b['username']}** — lost R${b['amount']:,}")
            else:
                lines.append(f"🎲 **{b['username']}** — R${b['amount']:,}")
        desc += "\n".join(lines)

    embed = discord.Embed(title=title, description=desc, color=color)
    if phase == 'lobby':
        embed.set_footer(text=f"Game starts in ~{CRASH_LOBBY_SECS}s after first bet")
    return embed


class CrashView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💰 Cash Out", style=discord.ButtonStyle.success, custom_id="crash_co")
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if crash_state['phase'] != 'running':
            await interaction.response.send_message("No active crash game right now!", ephemeral=True); return
        if uid not in crash_state['bets']:
            await interaction.response.send_message("You didn't bet this round! Use `.crash <amount>` next time.", ephemeral=True); return
        if uid in crash_state['cashed']:
            await interaction.response.send_message("You already cashed out!", ephemeral=True); return
        mult    = crash_state['mult']
        bet     = crash_state['bets'][uid]['amount']
        sb      = crash_state['bets'][uid]['start_bal']
        profit  = round(bet * mult) - bet
        new_bal = sb + profit
        set_user_balance(uid, new_bal)
        add_to_stats(uid, True, bet)
        if crash_state['guild_id']:
            guild = bot.get_guild(crash_state['guild_id'])
            if guild:
                asyncio.create_task(assign_rank_role(guild, uid))
        crash_state['cashed'][uid] = {'mult': mult, 'profit': profit}
        await interaction.response.send_message(
            f"✅ Cashed out at **{mult:.2f}×** — profit: **+R${profit:,}**  |  New balance: {fmt(new_bal)}",
            ephemeral=True
        )


async def run_crash_game(channel, guild_id):
    crash_state['guild_id'] = guild_id
    view = CrashView()
    crash_state['view'] = view

    # Lobby phase
    embed = crash_embed_build('lobby', crash_state['bets'], crash_state['cashed'])
    crash_state['message'] = await channel.send(embed=embed, view=view)

    await asyncio.sleep(CRASH_LOBBY_SECS)

    if not crash_state['bets']:
        crash_state['phase'] = 'idle'
        await crash_state['message'].edit(
            embed=discord.Embed(title="🚀 Crash — Cancelled", description="No bets placed.", color=0x888888),
            view=None)
        return

    # Running phase
    crash_state['phase']    = 'running'
    crash_state['crash_at'] = gen_crash_point()
    crash_state['mult']     = 1.00
    start_time = asyncio.get_event_loop().time()

    while True:
        elapsed = asyncio.get_event_loop().time() - start_time
        crash_state['mult'] = crash_mult_at(elapsed)

        if crash_state['mult'] >= crash_state['crash_at']:
            crash_state['mult'] = crash_state['crash_at']
            break

        embed = crash_embed_build('running', crash_state['bets'], crash_state['cashed'], crash_state['mult'])
        try:
            await crash_state['message'].edit(embed=embed, view=view)
        except Exception:
            pass
        await asyncio.sleep(CRASH_TICK)

    # Crashed
    crash_state['phase'] = 'crashed'
    for uid, b in crash_state['bets'].items():
        if uid not in crash_state['cashed']:
            new_bal = b['start_bal'] - b['amount']
            set_user_balance(uid, max(0, new_bal))
            add_to_stats(uid, False, b['amount'])

    embed = crash_embed_build('crashed', crash_state['bets'], crash_state['cashed'],
                              crash_at=crash_state['crash_at'])
    for item in view.children: item.disabled = True
    try:
        await crash_state['message'].edit(embed=embed, view=view)
    except Exception:
        pass

    await asyncio.sleep(8)

    # Reset
    crash_state.update({'phase': 'idle', 'bets': {}, 'cashed': {}, 'crash_at': 1.0,
                        'mult': 1.0, 'message': None, 'channel_id': None, 'task': None,
                        'view': None, 'guild_id': None})


@bot.command(name='crash')
async def crash_cmd(ctx, amount: str):
    bal = get_user_balance(ctx.author.id)
    amount = resolve_bet(amount, bal)
    if amount is None: await ctx.send("❌ Invalid amount! Use a number, `all`, or `half`."); return
    if amount <= 0: await ctx.send("❌ Bet must be positive!"); return
    if amount > bal: await ctx.send(f"❌ Insufficient balance! You have {fmt(bal)}"); return

    uid = ctx.author.id

    if crash_state['phase'] == 'idle':
        # Start lobby
        crash_state['phase']      = 'lobby'
        crash_state['channel_id'] = ctx.channel.id
        crash_state['bets'][uid]  = {'amount': amount, 'start_bal': bal, 'username': ctx.author.name}
        crash_state['task']       = asyncio.create_task(run_crash_game(ctx.channel, ctx.guild.id if ctx.guild else None))
        await ctx.message.delete()

    elif crash_state['phase'] == 'lobby':
        if crash_state['channel_id'] != ctx.channel.id:
            await ctx.send("❌ A crash game is running in another channel!", delete_after=5); return
        if uid in crash_state['bets']:
            await ctx.send("❌ You already bet this round!", delete_after=5); return
        crash_state['bets'][uid] = {'amount': amount, 'start_bal': bal, 'username': ctx.author.name}
        await ctx.message.delete()
        embed = crash_embed_build('lobby', crash_state['bets'], crash_state['cashed'])
        try: await crash_state['message'].edit(embed=embed, view=crash_state['view'])
        except: pass

    elif crash_state['phase'] == 'running':
        await ctx.send("⏳ A game is already in progress! You can bet on the **next** round.", delete_after=6)
    else:
        await ctx.send("⏳ Please wait — wrapping up the last round.", delete_after=5)

# ── Blackjack ─────────────────────────────────────────────────────────────────

def cv(cards):
    t = sum(cards); a = cards.count(11)
    while t > 21 and a: t -= 10; a -= 1
    return t

def cs(cards):
    return "  ".join("A" if c == 11 else str(c) for c in cards)

def bj_embed(player_cards, dealer_cards, bet, show_dealer=False,
             title="🃏  Blackjack", color=0x1E90FF, extra=""):
    pv = cv(player_cards); dv = cv(dealer_cards)
    desc = (
        f"**Your hand:** {cs(player_cards)}  —  **{pv}**\n"
        f"**Dealer:** {cs(dealer_cards) + '  — **' + str(dv) + '**' if show_dealer else str(dealer_cards[0]) + '  🂠'}\n\n"
        f"**Bet:** R${bet:,}"
    )
    if extra: desc += f"\n\n{extra}"
    return discord.Embed(title=title, description=desc, color=color)


class BlackjackView(discord.ui.View):
    def __init__(self, user_id, bet, start_balance, player_cards, dealer_cards, deck):
        super().__init__(timeout=120)
        self.user_id       = user_id; self.bet = bet
        self.start_balance = start_balance
        self.player_cards  = player_cards; self.dealer_cards = dealer_cards
        self.deck          = deck; self.game_over = False; self.first_action = True
        hit = discord.ui.Button(label="👊 Hit",         style=discord.ButtonStyle.primary,  custom_id="bj_hit")
        std = discord.ui.Button(label="🛑 Stand",       style=discord.ButtonStyle.danger,    custom_id="bj_stand")
        dbl = discord.ui.Button(label="⬆️ Double Down", style=discord.ButtonStyle.secondary, custom_id="bj_double")
        hit.callback = self.hit_callback; std.callback = self.stand_callback; dbl.callback = self.double_callback
        self.add_item(hit); self.add_item(std); self.add_item(dbl)

    def _disable_all(self):
        for item in self.children: item.disabled = True

    def _disable_double(self):
        for item in self.children:
            if getattr(item, 'custom_id', None) == 'bj_double': item.disabled = True

    async def hit_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your game!", ephemeral=True); return
        if self.game_over: return
        self.first_action = False; self._disable_double()
        self.player_cards.append(self.deck.pop())
        if cv(self.player_cards) > 21: await self._finish(interaction, bust=True)
        else: await interaction.response.edit_message(embed=bj_embed(self.player_cards, self.dealer_cards, self.bet), view=self)

    async def stand_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your game!", ephemeral=True); return
        if self.game_over: return
        await self._finish(interaction)

    async def double_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your game!", ephemeral=True); return
        if self.game_over: return
        if not self.first_action:
            await interaction.response.send_message("Double Down only available before hitting!", ephemeral=True); return
        if self.bet > get_user_balance(self.user_id):
            await interaction.response.send_message("Not enough balance to double down!", ephemeral=True); return
        self.bet *= 2; self.player_cards.append(self.deck.pop()); self.first_action = False
        await self._finish(interaction)

    async def _finish(self, interaction, bust=False):
        self.game_over = True; self._disable_all(); self.stop(); active_bj.pop(self.user_id, None)
        if not bust:
            while cv(self.dealer_cards) < 17: self.dealer_cards.append(self.deck.pop())
        pv = cv(self.player_cards); dv = cv(self.dealer_cards)
        if bust or pv > 21:   won = False; result = "Bust! You went over 21."
        elif dv > 21:         won = True;  result = "Dealer busts! You win!"
        elif pv > dv:         won = True;  result = "Higher hand — You win!"
        elif pv < dv:         won = False; result = "Dealer wins."
        else:                 won = None;  result = "Push — it's a tie."
        if won is True:
            new_bal = self.start_balance + self.bet; add_to_stats(self.user_id, True, self.bet)
            set_user_balance(self.user_id, new_bal); color = 0x00FF88
            extra = f"🎉 **{result}**\n+R${self.bet:,}  |  New Balance: {fmt(new_bal)}"
            if interaction.guild: asyncio.create_task(assign_rank_role(interaction.guild, self.user_id))
        elif won is False:
            new_bal = max(0, self.start_balance - self.bet); add_to_stats(self.user_id, False, self.bet)
            set_user_balance(self.user_id, new_bal); color = 0xFF4444
            extra = f"😢 **{result}**\n-R${self.bet:,}  |  New Balance: {fmt(new_bal)}"
        else:
            new_bal = self.start_balance; color = 0xFFD700
            extra = f"🤝 **{result}**\nNo change  |  Balance: {fmt(new_bal)}"
        title = "🃏  Blackjack — " + ("WIN!" if won is True else ("LOSS" if won is False else "TIE"))
        embed = bj_embed(self.player_cards, self.dealer_cards, self.bet, show_dealer=True,
                         title=title, color=color, extra=extra)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self): active_bj.pop(self.user_id, None)

# ── Mines ─────────────────────────────────────────────────────────────────────

MINES_ROWS = 4; MINES_COLS = 5; MINES_TOTAL = MINES_ROWS * MINES_COLS

def mines_multiplier(mines_count, picks):
    if picks == 0: return 1.0
    mult = 1.0; safe = MINES_TOTAL - mines_count
    for i in range(picks): mult *= (MINES_TOTAL - i) / (safe - i)
    return round(mult * 0.97, 2)

def make_mines_embed(bet, mines_count, picks, client_seed, public_hash,
                     server_seed=None, status=None, color=0x00BFFF):
    mult = mines_multiplier(mines_count, picks)
    profit = round(bet * mult) - bet if picks > 0 else 0
    safe = MINES_TOTAL - mines_count
    desc = (f"**Bet:** {bet:.2f}\n**Multiplier:** {mult:.1f}×\n**Profits:** {profit:.2f} pts\n"
            f"{mines_count} 💣 | {safe} 💎\n\n🔐 **Provably Fair:**\n"
            f"**Public Hash:** `{public_hash}`\n**Client Seed:** `{client_seed}`\n")
    desc += f"**Server Seed:** `{server_seed}`\n" if server_seed else "**Server Seed:** `Hidden`\n"
    if status: desc += f"\n{status}"
    return discord.Embed(title="⛏️  Mines", description=desc, color=color)


class MinesView(discord.ui.View):
    def __init__(self, user_id, bet, mines_count, mine_positions, server_seed, client_seed, public_hash):
        super().__init__(timeout=120)
        self.user_id = user_id; self.bet = bet; self.mines_count = mines_count
        self.mine_positions = mine_positions; self.server_seed = server_seed
        self.client_seed = client_seed; self.public_hash = public_hash
        self.revealed = set(); self.game_over = False
        for row in range(MINES_ROWS):
            for col in range(MINES_COLS):
                idx = row * MINES_COLS + col
                btn = discord.ui.Button(label="?", style=discord.ButtonStyle.secondary, row=row, custom_id=f"mine_{idx}")
                btn.callback = self.make_callback(idx); self.add_item(btn)
        co = discord.ui.Button(label="💰 Cash Out", style=discord.ButtonStyle.success, row=4, custom_id="cashout")
        co.callback = self.cashout_callback; self.add_item(co)

    def make_callback(self, idx):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("Not your game!", ephemeral=True); return
            if self.game_over or idx in self.revealed:
                await interaction.response.send_message("Invalid move!", ephemeral=True); return
            if idx in self.mine_positions:
                self.game_over = True; self._reveal_all()
                bal = get_user_balance(self.user_id); new_bal = bal - self.bet
                set_user_balance(self.user_id, new_bal); add_to_stats(self.user_id, False, self.bet)
                active_mines.pop(self.user_id, None); self.stop()
                status = f"💥 Hit a mine! Lost **{self.bet:,}** pts  |  New Balance: **R${new_bal:,}**"
                embed = make_mines_embed(self.bet, self.mines_count, len(self.revealed), self.client_seed,
                                         self.public_hash, server_seed=self.server_seed, status=status, color=0xFF4444)
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                self.revealed.add(idx); picks = len(self.revealed)
                mult = mines_multiplier(self.mines_count, picks); potential = round(self.bet * mult)
                self._set_gem(idx)
                for item in self.children:
                    if getattr(item, 'custom_id', None) == "cashout":
                        item.label = f"💰 Cash Out  R${potential:,}"; break
                await interaction.response.edit_message(
                    embed=make_mines_embed(self.bet, self.mines_count, picks, self.client_seed, self.public_hash),
                    view=self)
        return callback

    async def cashout_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your game!", ephemeral=True); return
        if self.game_over: return
        picks = len(self.revealed)
        if picks == 0:
            await interaction.response.send_message("Pick at least one cell first!", ephemeral=True); return
        self.game_over = True
        mult = mines_multiplier(self.mines_count, picks); winnings = round(self.bet * mult)
        profit = winnings - self.bet; bal = get_user_balance(self.user_id); new_bal = bal + profit
        set_user_balance(self.user_id, new_bal); add_to_stats(self.user_id, True, self.bet)
        active_mines.pop(self.user_id, None); self._reveal_all(); self.stop()
        status = f"✅ Cashed out **{winnings:,}** pts  |  New Balance: **R${new_bal:,}**"
        embed = make_mines_embed(self.bet, self.mines_count, picks, self.client_seed, self.public_hash,
                                  server_seed=self.server_seed, status=status, color=0x00FF88)
        await interaction.response.edit_message(embed=embed, view=self)
        if interaction.guild: asyncio.create_task(assign_rank_role(interaction.guild, self.user_id))

    def _set_gem(self, idx):
        for item in self.children:
            if getattr(item, 'custom_id', None) == f"mine_{idx}":
                item.label = "💎"; item.style = discord.ButtonStyle.success; item.disabled = True

    def _reveal_all(self):
        for item in self.children:
            cid = getattr(item, 'custom_id', None)
            if not cid: continue
            if cid.startswith("mine_"):
                idx = int(cid.split("_")[1])
                if idx in self.mine_positions: item.label = "💣"; item.style = discord.ButtonStyle.danger
                elif idx in self.revealed:     item.label = "💎"; item.style = discord.ButtonStyle.success
                else:                          item.label = "·";  item.style = discord.ButtonStyle.secondary
                item.disabled = True
            elif cid == "cashout": item.disabled = True

    async def on_timeout(self):
        if not self.game_over and self.user_id in active_mines:
            picks = len(self.revealed)
            if picks > 0:
                mult = mines_multiplier(self.mines_count, picks)
                set_user_balance(self.user_id, get_user_balance(self.user_id) + round(self.bet * mult) - self.bet)
                add_to_stats(self.user_id, True, self.bet)
            else:
                set_user_balance(self.user_id, get_user_balance(self.user_id) - self.bet)
                add_to_stats(self.user_id, False, self.bet)
            active_mines.pop(self.user_id, None)

# ── Events ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    for guild in bot.guilds:
        try:
            invites = await guild.fetch_invites()
            invite_cache[guild.id] = {inv.code: inv.uses for inv in invites}
        except Exception:
            pass
    print(f'{bot.user} has connected to Discord!')
    print('------')

@bot.event
async def on_invite_create(invite):
    guild_id = invite.guild.id
    if guild_id not in invite_cache:
        invite_cache[guild_id] = {}
    invite_cache[guild_id][invite.code] = invite.uses

@bot.event
async def on_member_join(member):
    guild = member.guild
    try:
        new_invites = await guild.fetch_invites()
    except Exception:
        return
    old = invite_cache.get(guild.id, {})
    inviter_id = None
    for inv in new_invites:
        old_uses = old.get(inv.code, 0)
        if inv.uses > old_uses:
            inviter_id = inv.inviter.id if inv.inviter else None
            break
    invite_cache[guild.id] = {inv.code: inv.uses for inv in new_invites}
    if not inviter_id:
        return
    today = datetime.now(timezone.utc).date().isoformat()
    data, uid = get_user(inviter_id)
    if data[uid].get('daily_invites_date') != today:
        data[uid]['daily_invites'] = 0
        data[uid]['daily_invites_date'] = today
    data[uid]['daily_invites'] = data[uid].get('daily_invites', 0) + 1
    data[uid]['total_invites']  = data[uid].get('total_invites', 0) + 1
    save_data(data)

# ── Admin ─────────────────────────────────────────────────────────────────────

@bot.command(name='addbal')
@commands.has_permissions(administrator=True)
async def addbal(ctx, member: discord.Member, amount: int):
    if amount == 0: await ctx.send("❌ Amount cannot be zero!"); return
    old_bal = get_user_balance(member.id); new_bal = old_bal + amount
    if new_bal < 0: await ctx.send(f"❌ Cannot reduce {member.name}'s balance below R$0!"); return
    set_user_balance(member.id, new_bal)
    img_buf = addbal_card(ctx.author.name, member.name, amount, old_bal, new_bal)
    embed = discord.Embed(title="🔧  Admin — Balance Updated", color=0x00FF88 if amount > 0 else 0xFF4444)
    embed.set_image(url="attachment://addbal.png")
    await ctx.send(embed=embed, file=send_image(img_buf, 'addbal.png'))

@addbal.error
async def addbal_error(ctx, error):
    if isinstance(error, commands.MissingPermissions): await ctx.send("🚫 Administrator permission required!")
    elif isinstance(error, commands.MemberNotFound):   await ctx.send("❌ Member not found — mention them with @")
    else:                                              await ctx.send("❌ Usage: `.addbal @user <amount>`")


@bot.command(name='removebal')
@commands.has_permissions(administrator=True)
async def removebal(ctx, member: discord.Member, amount: int):
    if amount <= 0: await ctx.send("❌ Amount must be positive!"); return
    old_bal = get_user_balance(member.id)
    if amount > old_bal:
        await ctx.send(f"❌ **{member.name}** only has **R${old_bal:,}** — can't remove more than their balance!"); return
    new_bal = old_bal - amount
    set_user_balance(member.id, new_bal)
    img_buf = addbal_card(ctx.author.name, member.name, -amount, old_bal, new_bal)
    embed = discord.Embed(
        title="🔧  Admin — Balance Removed",
        description=f"Removed **R${amount:,}** from {member.mention}\n**New Balance:** {fmt(new_bal)}",
        color=0xFF4444
    )
    embed.set_image(url="attachment://addbal.png")
    await ctx.send(embed=embed, file=send_image(img_buf, 'addbal.png'))

@removebal.error
async def removebal_error(ctx, error):
    if isinstance(error, commands.MissingPermissions): await ctx.send("🚫 Administrator permission required!")
    elif isinstance(error, commands.MemberNotFound):   await ctx.send("❌ Member not found — mention them with @")
    else:                                              await ctx.send("❌ Usage: `.removebal @user <amount>`")


@bot.command(name='updwithdraw')
@commands.has_permissions(administrator=True)
async def updwithdraw(ctx, member: discord.Member, amount: int):
    if amount < 0: await ctx.send("❌ Amount cannot be negative!"); return
    data, uid = get_user(member.id)
    data[uid]['total_withdrawn'] = data[uid].get('total_withdrawn', 0) + amount; save_data(data)
    embed = discord.Embed(title="🏦 Withdraw Updated", description=(
        f"**User:** {member.name}\n**Added:** {amount:,} pts\n"
        f"**Total Withdrawn:** {data[uid]['total_withdrawn']:,} pts"), color=0x00FF88)
    await ctx.send(embed=embed)

@updwithdraw.error
async def updwithdraw_error(ctx, error):
    if isinstance(error, commands.MissingPermissions): await ctx.send("🚫 Administrator permission required!")
    elif isinstance(error, commands.MemberNotFound):   await ctx.send("❌ Member not found — mention them with @")
    else:                                              await ctx.send("❌ Usage: `.updwithdraw @user <amount>`")


@bot.command(name='resetstats')
@commands.has_permissions(administrator=True)
async def resetstats(ctx):
    data = load_data(); count = 0
    for uid, ud in data.items():
        if uid.startswith('__'): continue
        ud['stats'] = {'wins': 0, 'losses': 0, 'total_wagered': 0, 'total_lost': 0}
        ud['rakeback_available'] = 0.0; ud['wager_at_last_monthly'] = 0; count += 1
    save_data(data)
    embed = discord.Embed(title="🔄 Stats Reset", description=f"Reset stats for **{count}** players.", color=0xFF8800)
    await ctx.send(embed=embed)

@resetstats.error
async def resetstats_error(ctx, error):
    if isinstance(error, commands.MissingPermissions): await ctx.send("🚫 Administrator permission required!")


@bot.command(name='setrank')
@commands.has_permissions(administrator=True)
async def setrank(ctx, rank_name: str, role: discord.Role = None):
    rn = rank_name.lower().strip()
    if rn not in RANK_KEYS:
        await ctx.send(f"❌ Valid ranks: `{', '.join(RANK_KEYS)}`"); return
    cfg = get_config()
    if 'rank_roles' not in cfg: cfg['rank_roles'] = {}
    if role is None:
        cfg['rank_roles'].pop(rn, None); save_config(cfg)
        embed = discord.Embed(title="🏅 Rank Role Removed",
                              description=f"Cleared role for **{rank_name.title()}**.", color=0xFF8800)
    else:
        cfg['rank_roles'][rn] = str(role.id); save_config(cfg)
        embed = discord.Embed(title="🏅 Rank Role Set",
                              description=f"**{rank_name.title()}** rank → {role.mention}", color=0x00FF88)
    await ctx.send(embed=embed)

@setrank.error
async def setrank_error(ctx, error):
    if isinstance(error, commands.MissingPermissions): await ctx.send("🚫 Administrator permission required!")
    elif isinstance(error, commands.RoleNotFound):     await ctx.send("❌ Role not found — mention it with @")
    else:                                              await ctx.send(f"❌ Usage: `.setrank <rank> @role`\nRanks: `{', '.join(RANK_KEYS)}`")


@bot.command(name='rankroles')
@commands.has_permissions(administrator=True)
async def rankroles_cmd(ctx):
    cfg = get_config(); rr = cfg.get('rank_roles', {})
    lines = []
    for rk, (_, rname, rcolor) in zip(RANK_KEYS, RANKS):
        role_id = rr.get(rk)
        role_str = f"<@&{role_id}>" if role_id else "*(not set)*"
        lines.append(f"{rname}: {role_str}")
    embed = discord.Embed(title="🏅 Rank Role Configuration", description="\n".join(lines), color=0x9B59B6)
    embed.set_footer(text="Use .setrank <rank> @role to configure  |  .setrank <rank> to clear")
    await ctx.send(embed=embed)

# ── Core Commands ─────────────────────────────────────────────────────────────

@bot.command(name='balance', aliases=['bal', 'b'])
async def balance(ctx, member: discord.Member = None):
    target = member or ctx.author
    bal = get_user_balance(target.id)
    img_buf = balance_card(target.name, target.id, bal)
    embed = discord.Embed(title=f"ℹ️  {target.name}'s Balance",
                          description=f"{bal:,} points  |  R${bal:,}  |  ${bal * POINTS_TO_USD:.2f}",
                          color=0x4FC3F7)
    embed.set_image(url="attachment://balance.png")
    await ctx.send(embed=embed, file=send_image(img_buf, 'balance.png'))


@bot.command(name='coinflip', aliases=['cf'])
async def coinflip(ctx, amount: str, choice: str):
    bal = get_user_balance(ctx.author.id); choice = choice.lower()
    if choice not in ['heads','tails','h','t']: await ctx.send("❌ Choose **heads** or **tails** (or h/t)"); return
    amount = resolve_bet(amount, bal)
    if amount is None: await ctx.send("❌ Invalid amount! Use a number, `all`, or `half`."); return
    if amount <= 0: await ctx.send("❌ Bet must be positive!"); return
    if amount > bal: await ctx.send(f"❌ Insufficient balance! You have {fmt(bal)}"); return
    choice = 'heads' if choice == 'h' else ('tails' if choice == 't' else choice)
    frames = ["🌀 Flipping...","🪙 Spinning...","✨ Almost...","🎯 Result..."]
    embed = discord.Embed(title="🪙  Coin Flip", description=frames[0], color=0xFFD700)
    msg = await ctx.send(embed=embed)
    for frame in frames[1:]:
        await asyncio.sleep(0.45); embed.description = frame; await msg.edit(embed=embed)
    await asyncio.sleep(0.35)
    result = random.choice(['heads','tails']); won = choice == result
    new_bal = bal + amount if won else bal - amount
    add_to_stats(ctx.author.id, won, amount); set_user_balance(ctx.author.id, new_bal)
    if ctx.guild: asyncio.create_task(assign_rank_role(ctx.guild, ctx.author.id))
    embed = discord.Embed(title="🎉 Coin Flip — YOU WON!" if won else "😢 Coin Flip — YOU LOST", color=0x00FF88 if won else 0xFF4444)
    embed.add_field(name="You chose", value=choice.upper(), inline=True)
    embed.add_field(name="Result",    value=result.upper(), inline=True)
    embed.add_field(name="Change",    value=f"{'+'if won else '-'}R${amount:,}", inline=True)
    embed.add_field(name="New Balance", value=fmt(new_bal), inline=False)
    img_buf = coinflip_card(ctx.author.name, choice, result, won)
    embed.set_image(url="attachment://coinflip.png")
    await msg.edit(embed=embed, attachments=[send_image(img_buf, 'coinflip.png')])


@bot.command(name='dice')
async def dice(ctx, amount: str, guess: int):
    bal = get_user_balance(ctx.author.id)
    if guess < 1 or guess > 6: await ctx.send("❌ Guess a number 1–6!"); return
    amount = resolve_bet(amount, bal)
    if amount is None: await ctx.send("❌ Invalid amount! Use a number, `all`, or `half`."); return
    if amount <= 0: await ctx.send("❌ Bet must be positive!"); return
    if amount > bal: await ctx.send(f"❌ Insufficient balance! You have {fmt(bal)}"); return
    faces = ["⚀","⚁","⚂","⚃","⚄","⚅"]
    embed = discord.Embed(title="🎲  Dice Roll", description="🎲 Rolling...", color=0xFFD700)
    msg = await ctx.send(embed=embed)
    for _ in range(4):
        await asyncio.sleep(0.4); embed.description = f"🎲 {faces[random.randint(0,5)]}  Rolling..."; await msg.edit(embed=embed)
    await asyncio.sleep(0.3)
    roll = random.randint(1, 6); won = guess == roll
    new_bal = (bal + amount * 5) if won else (bal - amount)
    add_to_stats(ctx.author.id, won, amount); set_user_balance(ctx.author.id, new_bal)
    if ctx.guild: asyncio.create_task(assign_rank_role(ctx.guild, ctx.author.id))
    embed = discord.Embed(title=f"🎉 Dice — WIN! (×5)" if won else "😢 Dice — LOST", color=0x00FF88 if won else 0xFF4444)
    embed.add_field(name="Your guess", value=f"{guess} {faces[guess-1]}", inline=True)
    embed.add_field(name="Rolled",     value=f"{roll} {faces[roll-1]}",   inline=True)
    embed.add_field(name="Change",     value=f"{'+'if won else '-'}R${amount*(5 if won else 1):,}", inline=True)
    embed.add_field(name="New Balance", value=fmt(new_bal), inline=False)
    img_buf = dice_card(ctx.author.name, guess, roll, won)
    embed.set_image(url="attachment://dice.png")
    await msg.edit(embed=embed, attachments=[send_image(img_buf, 'dice.png')])


@bot.command(name='slots')
async def slots(ctx, amount: str):
    bal = get_user_balance(ctx.author.id)
    amount = resolve_bet(amount, bal)
    if amount is None: await ctx.send("❌ Invalid amount! Use a number, `all`, or `half`."); return
    if amount <= 0: await ctx.send("❌ Bet must be positive!"); return
    if amount > bal: await ctx.send(f"❌ Insufficient balance! You have {fmt(bal)}"); return
    SPIN = "🌀"; GEM = "💎"; symbols = ["🍎","🍊","🍋","🍌","⭐",GEM]
    final = [random.choice(symbols) for _ in range(3)]
    def disp(r1,r2,r3): return f"┌─────────────┐\n│  {r1}  {r2}  {r3}  │\n└─────────────┘"
    embed = discord.Embed(title="🎰  Slot Machine", color=0xFFD700)
    embed.description = f"```\n{disp(SPIN,SPIN,SPIN)}\n```\nSpinning..."
    msg = await ctx.send(embed=embed)
    for step in range(1, 4):
        await asyncio.sleep(0.6); rv = [final[i] for i in range(step)]; pv = [SPIN]*(3-step)
        embed.description = f"```\n{disp(*(rv+pv))}\n```"; await msg.edit(embed=embed)
    await asyncio.sleep(0.4)
    r1,r2,r3 = final
    if r1==r2==r3: winnings=amount*(100 if r1==GEM else 10); won=True; label="💎 JACKPOT ×100" if r1==GEM else "✨ Triple ×10"
    elif r1==r2 or r2==r3: winnings=amount*2; won=True; label="Double ×2"
    else: winnings=0; won=False; label="No match"
    new_bal = bal + winnings if won else bal - amount
    add_to_stats(ctx.author.id, won, amount); set_user_balance(ctx.author.id, new_bal)
    if ctx.guild: asyncio.create_task(assign_rank_role(ctx.guild, ctx.author.id))
    embed = discord.Embed(title=f"🎉 Slots — {label}" if won else "😢 Slots — No Match", color=0x00FF88 if won else 0xFF4444)
    embed.description = f"```\n{disp(r1,r2,r3)}\n```"
    embed.add_field(name="Won" if won else "Lost", value=fmt(winnings if won else amount), inline=True)
    embed.add_field(name="New Balance", value=fmt(new_bal), inline=True)
    img_buf = slots_card(ctx.author.name, final, won, label)
    embed.set_image(url="attachment://slots.png")
    await msg.edit(embed=embed, attachments=[send_image(img_buf, 'slots.png')])


@bot.command(name='roulette')
async def roulette(ctx, amount: str, choice: str):
    bal = get_user_balance(ctx.author.id); choice = choice.lower()
    if choice not in ['red','black','even','odd']: await ctx.send("❌ Choose: `red` `black` `even` `odd`"); return
    amount = resolve_bet(amount, bal)
    if amount is None: await ctx.send("❌ Invalid amount! Use a number, `all`, or `half`."); return
    if amount <= 0: await ctx.send("❌ Bet must be positive!"); return
    if amount > bal: await ctx.send(f"❌ Insufficient balance! You have {fmt(bal)}"); return
    frames = ["🔴 🔵 🟢 🔴 ⚪","⚪ 🔴 🔵 🟢 🔴","🔴 ⚪ 🔴 🔵 🟢","🟢 🔴 ⚪ 🔴 🔵"]
    embed = discord.Embed(title="🎡  Roulette", description=f"Spinning...\n{frames[0]}", color=0xFFD700)
    msg = await ctx.send(embed=embed)
    for frame in frames[1:]:
        await asyncio.sleep(0.45); embed.description = f"Spinning...\n{frame}"; await msg.edit(embed=embed)
    await asyncio.sleep(0.4)
    spin = random.randint(0, 36)
    if spin == 0: rc = "green"; parity = "—"; won = False
    else: rc = "red" if spin%2==1 else "black"; parity = "even" if spin%2==0 else "odd"; won = choice==rc or choice==parity
    new_bal = bal + amount if won else bal - amount
    add_to_stats(ctx.author.id, won, amount); set_user_balance(ctx.author.id, new_bal)
    if ctx.guild: asyncio.create_task(assign_rank_role(ctx.guild, ctx.author.id))
    ci = "🔴" if rc=="red" else ("⚫" if rc=="black" else "🟢")
    embed = discord.Embed(title="🎉 Roulette — WIN! (×2)" if won else "😢 Roulette — LOST", color=0x00FF88 if won else 0xFF4444)
    embed.add_field(name="Landed", value=f"{ci} {spin} ({rc}/{parity})", inline=True)
    embed.add_field(name="You bet", value=choice.upper(), inline=True)
    embed.add_field(name="Change",  value=f"{'+'if won else '-'}R${amount:,}", inline=True)
    embed.add_field(name="New Balance", value=fmt(new_bal), inline=False)
    img_buf = roulette_card(ctx.author.name, choice, spin, rc, won)
    embed.set_image(url="attachment://roulette.png")
    await msg.edit(embed=embed, attachments=[send_image(img_buf, 'roulette.png')])


@bot.command(name='blackjack', aliases=['bj'])
async def blackjack_cmd(ctx, amount: str):
    bal = get_user_balance(ctx.author.id)
    amount = resolve_bet(amount, bal)
    if amount is None: await ctx.send("❌ Invalid amount! Use a number, `all`, or `half`."); return
    if amount <= 0: await ctx.send("❌ Bet must be positive!"); return
    if amount > bal: await ctx.send(f"❌ Insufficient balance! You have {fmt(bal)}"); return
    if ctx.author.id in active_bj: await ctx.send("❌ You already have an active blackjack game!"); return
    deck = [2,3,4,5,6,7,8,9,10,10,10,10,11] * 4; random.shuffle(deck)
    pc = [deck.pop(), deck.pop()]; dc = [deck.pop(), deck.pop()]; pv = cv(pc)
    if pv == 21:
        winnings = round(amount * 2.5); new_bal = bal + winnings - amount
        add_to_stats(ctx.author.id, True, amount); set_user_balance(ctx.author.id, new_bal)
        if ctx.guild: asyncio.create_task(assign_rank_role(ctx.guild, ctx.author.id))
        embed = discord.Embed(title="🃏  Blackjack — BLACKJACK! (×2.5)", color=0x00FF88)
        embed.add_field(name="Your hand", value=f"{cs(pc)} ({pv})", inline=False)
        embed.add_field(name="Won", value=fmt(winnings), inline=True)
        embed.add_field(name="New Balance", value=fmt(new_bal), inline=True)
        await ctx.send(embed=embed); return
    active_bj[ctx.author.id] = True
    view = BlackjackView(ctx.author.id, amount, bal, pc, dc, deck)
    embed = bj_embed(pc, dc, amount)
    embed.set_footer(text="👊 Hit  |  🛑 Stand  |  ⬆️ Double Down (first action only)")
    await ctx.send(embed=embed, view=view)


@bot.command(name='mines')
async def mines_cmd(ctx, amount: str, mine_count: int = 3):
    bal = get_user_balance(ctx.author.id)
    amount = resolve_bet(amount, bal)
    if amount is None: await ctx.send("❌ Invalid amount! Use a number, `all`, or `half`."); return
    if amount <= 0: await ctx.send("❌ Bet must be positive!"); return
    if amount > bal: await ctx.send(f"❌ Insufficient balance! You have {fmt(bal)}"); return
    if mine_count < 1 or mine_count > 15: await ctx.send("❌ Mine count must be 1–15!"); return
    if ctx.author.id in active_mines: await ctx.send("❌ You already have an active mines game!"); return
    server_seed, client_seed, public_hash = generate_seeds()
    mine_positions = pf_mine_positions(server_seed, client_seed, mine_count)
    active_mines[ctx.author.id] = True
    view = MinesView(ctx.author.id, amount, mine_count, mine_positions, server_seed, client_seed, public_hash)
    embed = make_mines_embed(amount, mine_count, 0, client_seed, public_hash)
    await ctx.send(embed=embed, view=view)

# ── Rewards ───────────────────────────────────────────────────────────────────

@bot.command(name='daily')
async def daily(ctx):
    data, uid = get_user(ctx.author.id); now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    # Reset daily invite count if it's a new day
    if data[uid].get('daily_invites_date') != today:
        data[uid]['daily_invites'] = 0
        data[uid]['daily_invites_date'] = today

    daily_invs = data[uid].get('daily_invites', 0)

    # Check cooldown first
    last = data[uid].get('last_daily')
    if last:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None: last_dt = last_dt.replace(tzinfo=timezone.utc)
        diff = now - last_dt
        if diff.total_seconds() < 86400:
            rem = timedelta(seconds=86400) - diff
            h = int(rem.total_seconds()//3600); m = int((rem.total_seconds()%3600)//60)
            embed = discord.Embed(title="🎁 Daily Reward",
                                  description=f"⏳ Come back in **{h}h {m}m**!", color=0xFF4444)
            await ctx.send(embed=embed); return

    # Check invite requirement
    REQUIRED_INVITES = 2
    if daily_invs < REQUIRED_INVITES:
        needed = REQUIRED_INVITES - daily_invs
        embed = discord.Embed(
            title="🎁 Daily Reward — Invite Required",
            description=(
                f"You need **{needed} more invite{'s' if needed > 1 else ''}** today to claim your daily reward!\n\n"
                f"**Today's invites:** {daily_invs} / {REQUIRED_INVITES}\n\n"
                f"Invite friends to the server and come back to claim!"
            ),
            color=0xFF8800
        )
        embed.set_footer(text="Invites reset every day at midnight UTC.")
        save_data(data)
        await ctx.send(embed=embed); return

    DAILY = 5
    data[uid]['last_daily']     = now.isoformat()
    data[uid]['balance']        = data[uid].get('balance', 0) + DAILY
    data[uid]['bonus_received'] = data[uid].get('bonus_received', 0) + DAILY
    save_data(data)
    embed = discord.Embed(title="🎁 Daily Reward Claimed!",
                          description=(
                              f"Received **R${DAILY}**!\n"
                              f"**New Balance:** {fmt(data[uid]['balance'])}\n\n"
                              f"✅ Today's invites: {daily_invs} / {REQUIRED_INVITES}"
                          ),
                          color=0x00FF88)
    embed.set_footer(text="Come back in 24 hours!")
    await ctx.send(embed=embed)


@bot.command(name='invites')
async def invites_cmd(ctx, member: discord.Member = None):
    target = member or ctx.author
    data, uid = get_user(target.id)
    today = datetime.now(timezone.utc).date().isoformat()
    if data[uid].get('daily_invites_date') != today:
        daily_invs = 0
    else:
        daily_invs = data[uid].get('daily_invites', 0)
    total_invs = data[uid].get('total_invites', 0)
    embed = discord.Embed(title=f"📨 {target.name}'s Invites", color=0x00BFFF)
    embed.add_field(name="Today's Invites", value=f"**{daily_invs} / 2**", inline=True)
    embed.add_field(name="Total Invites",   value=f"**{total_invs}**",     inline=True)
    status = "✅ Can claim daily!" if daily_invs >= 2 else f"❌ Need {2 - daily_invs} more invite(s) today"
    embed.add_field(name="Daily Status", value=status, inline=False)
    embed.set_footer(text="Invite 2 people per day to unlock your .daily reward.")
    await ctx.send(embed=embed)


@bot.command(name='monthly')
async def monthly(ctx):
    data, uid = get_user(ctx.author.id); now = datetime.now(timezone.utc)
    current_month = now.strftime('%Y-%m')
    if data[uid].get('last_monthly') == current_month:
        next_m = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
        embed = discord.Embed(title="📅 Monthly Reward",
                              description=f"⏳ Already claimed!\nNext in **{(next_m-now).days} days**.", color=0xFF4444)
        await ctx.send(embed=embed); return
    wager_since = data[uid]['stats']['total_wagered'] - data[uid].get('wager_at_last_monthly', 0)
    reward = int(wager_since // 1000)
    if reward == 0:
        embed = discord.Embed(title="📅 Monthly Reward", description=(
            f"Need at least **R$1,000** wagered since last claim.\n"
            f"**Wagered since:** R${wager_since:,}\n**Still need:** R${max(0, 1000-wager_since):,}"), color=0xFF8800)
        await ctx.send(embed=embed); return
    data[uid]['last_monthly']          = current_month
    data[uid]['wager_at_last_monthly'] = data[uid]['stats']['total_wagered']
    data[uid]['balance']               = data[uid].get('balance', 0) + reward
    data[uid]['bonus_received']        = data[uid].get('bonus_received', 0) + reward
    save_data(data)
    embed = discord.Embed(title="📅 Monthly Reward Claimed!", description=(
        f"**Wagered this period:** R${wager_since:,}\n**Reward:** {reward:,} pts\n"
        f"**New Balance:** {fmt(data[uid]['balance'])}"), color=0x00FF88)
    await ctx.send(embed=embed)


@bot.command(name='rakeback')
async def rakeback(ctx):
    data, uid = get_user(ctx.author.id); available = data[uid].get('rakeback_available', 0.0); amount = int(available)
    if amount < 1:
        embed = discord.Embed(title="💸 Rakeback", description=(
            f"**Available:** {available:.4f} pts *(need ≥1 to claim)*\n"
            f"**Rate:** 0.2% of all losses\n**Total Lost:** R${data[uid]['stats'].get('total_lost',0):,}"),
            color=0xFF8800)
        await ctx.send(embed=embed); return
    data[uid]['rakeback_available'] = available - amount
    data[uid]['balance']            = data[uid].get('balance', 0) + amount
    data[uid]['bonus_received']     = data[uid].get('bonus_received', 0) + amount
    save_data(data)
    embed = discord.Embed(title="💸 Rakeback Claimed!", description=(
        f"**Claimed:** {amount:,} pts\n**Remaining:** {(available-amount):.4f}\n"
        f"**New Balance:** {fmt(data[uid]['balance'])}"), color=0x00FF88)
    embed.set_footer(text="Rakeback = 0.2% of all losses, accumulated automatically.")
    await ctx.send(embed=embed)

# ── Social ────────────────────────────────────────────────────────────────────

@bot.command(name='send')
async def send_points(ctx, member: discord.Member, amount: int):
    if member.id == ctx.author.id: await ctx.send("❌ You can't send points to yourself!"); return
    if amount <= 0: await ctx.send("❌ Amount must be positive!"); return
    sender_bal = get_user_balance(ctx.author.id)
    if amount > sender_bal: await ctx.send(f"❌ Insufficient balance! You have {fmt(sender_bal)}"); return
    set_user_balance(ctx.author.id, sender_bal - amount)
    recv_bal = get_user_balance(member.id); set_user_balance(member.id, recv_bal + amount)
    sd, suid = get_user(ctx.author.id); sd[suid]['tips_sent'] = sd[suid].get('tips_sent',0) + amount; save_data(sd)
    rd, ruid = get_user(member.id);     rd[ruid]['tips_received'] = rd[ruid].get('tips_received',0) + amount; save_data(rd)
    embed = discord.Embed(title="🤝 Transfer Complete", description=(
        f"**{ctx.author.name}** → **{member.name}**\n**Amount:** R${amount:,}\n\n"
        f"**{ctx.author.name}'s balance:** {fmt(sender_bal-amount)}\n"
        f"**{member.name}'s balance:** {fmt(recv_bal+amount)}"), color=0x00FF88)
    await ctx.send(embed=embed)

@send_points.error
async def send_error(ctx, error):
    if isinstance(error, commands.MemberNotFound): await ctx.send("❌ Member not found — mention them with @")
    else: await ctx.send("❌ Usage: `.send @user <amount>`")


RAIN_DURATION = 120  # seconds

class RainView(discord.ui.View):
    def __init__(self, host_id, amount):
        super().__init__(timeout=RAIN_DURATION)
        self.host_id  = host_id
        self.amount   = amount
        self.joiners  = set()  # user_ids who joined

    @discord.ui.button(label="🌧️ Join Rain", style=discord.ButtonStyle.primary, custom_id="rain_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid == self.host_id:
            await interaction.response.send_message("❌ You started the rain — you can't join it!", ephemeral=True); return
        if uid in self.joiners:
            await interaction.response.send_message("✅ You're already in the rain!", ephemeral=True); return
        self.joiners.add(uid)
        count = len(self.joiners)
        share = self.amount // count if count else self.amount
        await interaction.response.send_message(
            f"🌧️ You joined the rain! **{count}** player{'s' if count != 1 else ''} in so far — "
            f"current share: **R${share:,}** each.", ephemeral=True)

    async def on_timeout(self):
        pass  # handled in the command task


@bot.command(name='rain')
async def rain(ctx, amount: str):
    bal = get_user_balance(ctx.author.id)
    amount = resolve_bet(amount, bal)
    if amount is None: await ctx.send("❌ Invalid amount! Use a number, `all`, or `half`."); return
    if amount <= 0: await ctx.send("❌ Amount must be positive!"); return
    if amount > bal: await ctx.send(f"❌ Insufficient balance! You have {fmt(bal)}"); return

    # Deduct immediately so the host can't spend it elsewhere
    set_user_balance(ctx.author.id, bal - amount)

    view = RainView(ctx.author.id, amount)

    embed = discord.Embed(
        title="🌧️  It's Raining Points!",
        description=(
            f"**{ctx.author.name}** is raining **R${amount:,}**!\n\n"
            f"Click **Join Rain** to get your share.\n"
            f"The pot splits equally among everyone who joins.\n\n"
            f"⏳ Rain ends in **{RAIN_DURATION // 60} minutes**."
        ),
        color=0x00BFFF
    )
    embed.set_footer(text=f"Pot: R${amount:,}  |  Splits equally among all joiners")
    msg = await ctx.send(embed=embed, view=view)

    # Countdown update at 1 min remaining
    await asyncio.sleep(RAIN_DURATION - 60)
    if not view.is_finished():
        count = len(view.joiners)
        share = amount // count if count else amount
        embed.description = (
            f"**{ctx.author.name}** is raining **R${amount:,}**!\n\n"
            f"Click **Join Rain** to get your share.\n\n"
            f"⏳ **1 minute left!**  "
            f"{'**' + str(count) + ' joined** — share: R$' + f'{share:,}' if count else 'No one joined yet!'}"
        )
        try: await msg.edit(embed=embed, view=view)
        except: pass

    await asyncio.sleep(60)

    # Disable button
    for item in view.children: item.disabled = True

    joiners = list(view.joiners)
    if not joiners:
        # Nobody joined — refund host
        set_user_balance(ctx.author.id, get_user_balance(ctx.author.id) + amount)
        embed = discord.Embed(
            title="🌧️  Rain Ended — No Takers",
            description=f"Nobody joined the rain. **R${amount:,}** refunded to {ctx.author.mention}.",
            color=0xFF8800
        )
        try: await msg.edit(embed=embed, view=view)
        except: pass
        return

    share = amount // len(joiners)
    remainder = amount - share * len(joiners)

    names = []
    for i, uid in enumerate(joiners):
        payout = share + (remainder if i == 0 else 0)  # first joiner gets any leftover cent
        prev = get_user_balance(uid)
        set_user_balance(uid, prev + payout)
        rd, ruid = get_user(uid)
        rd[ruid]['tips_received'] = rd[ruid].get('tips_received', 0) + payout
        save_data(rd)
        try: user = await bot.fetch_user(uid); names.append(f"**{user.name}** +R${payout:,}")
        except: names.append(f"+R${payout:,}")

    sd, suid = get_user(ctx.author.id)
    sd[suid]['tips_sent'] = sd[suid].get('tips_sent', 0) + amount
    save_data(sd)

    embed = discord.Embed(
        title="🌧️  Rain Complete!",
        description=(
            f"**{ctx.author.name}** rained **R${amount:,}** on **{len(joiners)}** player{'s' if len(joiners)!=1 else ''}!\n\n"
            + "\n".join(names)
        ),
        color=0x00FF88
    )
    embed.set_footer(text=f"Each player received R${share:,}")
    try: await msg.edit(embed=embed, view=view)
    except: pass


@bot.command(name='rank')
async def rank(ctx):
    data, uid = get_user(ctx.author.id); tw = data[uid]['stats']['total_wagered']
    rank_info, next_r = get_rank_info(tw); _, rname, rcolor = rank_info
    desc = f"**Current Rank:** {rname}\n**Total Wagered:** R${tw:,}\n\n"
    if next_r:
        nt, nn, _ = next_r; rt = rank_info[0]; span = nt - rt; prog = tw - rt
        pct = min(prog/span, 1.0) if span > 0 else 1.0
        bf = int(pct * 20); bar = "█"*bf + "░"*(20-bf)
        desc += (f"**Next Rank:** {nn}\n**Progress:** `[{bar}]` {int(pct*100)}%\n"
                 f"**Still need:** R${nt-tw:,} wagered\n\n")
    else:
        desc += "🎉 **MAX RANK ACHIEVED!**\n\n"
    desc += "**All Ranks:**\n"
    for thresh, name, _ in RANKS:
        marker = "→ " if name == rname else "   "; desc += f"{marker}{name}: R${thresh:,}+\n"
    embed = discord.Embed(title="🏆 Your Rank", description=desc, color=rcolor)
    await ctx.send(embed=embed)


@bot.command(name='clan')
async def clan(ctx, action: str = "help", *, arg: str = ""):
    action = action.lower().strip()
    if action == "create":
        name = arg.strip()
        if not name or len(name) > 20: await ctx.send("❌ Usage: `.clan create <name>` (max 20 chars)"); return
        data, uid = get_user(ctx.author.id)
        if data[uid].get('clan'): await ctx.send(f"❌ You're already in **{data[uid]['clan']}**!"); return
        clans = get_clans()
        if any(k.lower() == name.lower() for k in clans): await ctx.send(f"❌ **{name}** already exists!"); return
        clans[name] = {'owner_id': str(ctx.author.id), 'members': [str(ctx.author.id)], 'created_at': datetime.now(timezone.utc).isoformat()[:10]}
        save_clans(clans); data[uid]['clan'] = name; save_data(data)
        await ctx.send(embed=discord.Embed(title="🛡️ Clan Created!", description=f"You created **{name}**!\nShare `.clan join {name}` with friends.", color=0x00FF88))
    elif action == "join":
        name = arg.strip()
        if not name: await ctx.send("❌ Usage: `.clan join <name>`"); return
        clans = get_clans(); real = next((k for k in clans if k.lower()==name.lower()), None)
        if not real: await ctx.send(f"❌ Clan **{name}** not found!"); return
        data, uid = get_user(ctx.author.id)
        if data[uid].get('clan'): await ctx.send(f"❌ You're already in **{data[uid]['clan']}**!"); return
        clans[real]['members'].append(str(ctx.author.id)); save_clans(clans)
        data[uid]['clan'] = real; save_data(data)
        await ctx.send(embed=discord.Embed(title="🛡️ Joined Clan!", description=f"You joined **{real}**!", color=0x00FF88))
    elif action == "leave":
        data, uid = get_user(ctx.author.id); cn = data[uid].get('clan')
        if not cn: await ctx.send("❌ You're not in a clan!"); return
        clans = get_clans()
        if cn in clans:
            c = clans[cn]
            if c['owner_id']==str(ctx.author.id) and len(c['members'])>1:
                await ctx.send("❌ You're the owner! Kick all members first or use `.clan disband`."); return
            c['members'] = [m for m in c['members'] if m!=str(ctx.author.id)]
            if not c['members']: del clans[cn]
            save_clans(clans)
        data[uid]['clan'] = None; save_data(data)
        await ctx.send(embed=discord.Embed(title="🛡️ Left Clan", description=f"You left **{cn}**.", color=0xFF8800))
    elif action == "disband":
        data, uid = get_user(ctx.author.id); cn = data[uid].get('clan')
        if not cn: await ctx.send("❌ You're not in a clan!"); return
        clans = get_clans()
        if cn not in clans or clans[cn]['owner_id'] != str(ctx.author.id):
            await ctx.send("❌ You're not the owner!"); return
        members = clans[cn]['members']; del clans[cn]; save_clans(clans)
        all_data = load_data()
        for mid in members:
            if mid in all_data: all_data[mid]['clan'] = None
        save_data(all_data)
        await ctx.send(embed=discord.Embed(title="🛡️ Clan Disbanded", description=f"**{cn}** has been disbanded.", color=0xFF4444))
    elif action == "kick":
        match = re.search(r'<@!?(\d+)>', arg)
        if not match: await ctx.send("❌ Usage: `.clan kick @member`"); return
        target_id = match.group(1); data, uid = get_user(ctx.author.id); cn = data[uid].get('clan')
        if not cn: await ctx.send("❌ You're not in a clan!"); return
        clans = get_clans()
        if cn not in clans or clans[cn]['owner_id'] != str(ctx.author.id):
            await ctx.send("❌ Only the clan owner can kick!"); return
        if target_id == str(ctx.author.id): await ctx.send("❌ You can't kick yourself!"); return
        if target_id not in clans[cn]['members']: await ctx.send("❌ Not in your clan!"); return
        clans[cn]['members'].remove(target_id); save_clans(clans)
        td, tuid = get_user(int(target_id)); td[tuid]['clan'] = None; save_data(td)
        try: user = await bot.fetch_user(int(target_id)); uname = user.name
        except: uname = target_id
        await ctx.send(embed=discord.Embed(title="🛡️ Member Kicked", description=f"**{uname}** removed from **{cn}**.", color=0xFF8800))
    elif action == "info":
        name = arg.strip() if arg else None
        if not name:
            data, uid = get_user(ctx.author.id); name = data[uid].get('clan')
            if not name: await ctx.send("❌ You're not in a clan! Use `.clan info <name>`."); return
        clans = get_clans(); real = next((k for k in clans if k.lower()==name.lower()), None)
        if not real: await ctx.send(f"❌ Clan **{name}** not found!"); return
        c = clans[real]
        try: owner = await bot.fetch_user(int(c['owner_id'])); on = owner.name
        except: on = "Unknown"
        all_data = load_data()
        tw = sum(all_data.get(m,{}).get('stats',{}).get('total_wagered',0) for m in c['members'])
        embed = discord.Embed(title=f"🛡️ {real}", color=0x9B59B6)
        embed.add_field(name="Owner",   value=on,                  inline=True)
        embed.add_field(name="Members", value=str(len(c['members'])),inline=True)
        embed.add_field(name="Founded", value=c.get('created_at','?')[:10], inline=True)
        embed.add_field(name="Total Wagered", value=f"R${tw:,}", inline=True)
        await ctx.send(embed=embed)
    elif action == "top":
        clans = get_clans()
        if not clans: await ctx.send("❌ No clans yet!"); return
        all_data = load_data()
        stats = sorted([(n, sum(all_data.get(m,{}).get('stats',{}).get('total_wagered',0) for m in c['members']), len(c['members'])) for n,c in clans.items()], key=lambda x: x[1], reverse=True)
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
        lines = [f"{medals[i]} **{n}**  —  R${t:,}  ({m} members)" for i,(n,t,m) in enumerate(stats[:5])]
        await ctx.send(embed=discord.Embed(title="🛡️ Clan Leaderboard", description="\n".join(lines), color=0xFFD700))
    else:
        embed = discord.Embed(title="🛡️ Clan Commands", color=0x9B59B6)
        for n, v in [(".clan create <name>","Create a new clan"),(".clan join <name>","Join a clan"),(".clan leave","Leave your clan"),(".clan disband","Disband your clan (owner)"),(".clan kick @member","Kick a member (owner)"),(".clan info [name]","View clan details"),(".clan top","Top 5 clans")]:
            embed.add_field(name=n, value=v, inline=False)
        await ctx.send(embed=embed)


@bot.command(name='price')
async def price(ctx, amount: int = None):
    if amount is not None:
        if amount <= 0: await ctx.send("❌ Amount must be positive!"); return
        usd = amount * POINTS_TO_USD
        description = (
            f"Points: **{amount:,.2f}**\n"
            f"ROBUX: **{amount:,}**\n"
            f"USD: **${usd:.2f}**\n\n"
            f"Rate: **{amount:,} POINT = {amount:,} Robux Or ${usd:.2f}**"
        )
        embed = discord.Embed(title="💱 Price Conversion", description=description, color=0x00BFFF)
        await ctx.send(embed=embed)
    else:
        rows = [("1","R$1.00","$0.0037"),("100","R$100.00","$0.37"),("1,000","R$1,000","$3.70"),
                ("10,000","R$10,000","$37.00"),("100,000","R$100,000","$370.00"),("1,000,000","R$1,000,000","$3,700.00")]
        lines = ["```", f"{'Points':<12}  {'R$':>12}  {'USD':>10}", "-"*38]
        for pts, brl, usd in rows: lines.append(f"{pts:<12}  {brl:>12}  {usd:>10}")
        lines.append("```")
        embed = discord.Embed(title="💹 LuckyBet Points Price", description="\n".join(lines), color=0x00BFFF)
        embed.set_footer(text="Tip: .price <amount> to convert a specific value  |  Rate: 1pt = R$1 = $0.0037")
        await ctx.send(embed=embed)


@bot.group(name='thread', invoke_without_command=True)
async def thread_cmd(ctx):
    embed = discord.Embed(title="💬 Thread Commands", color=0x00BFFF, description=(
        "`.thread create` — Create a private thread\n"
        "`.thread close` — Close (archive) the current thread\n"
        "`.thread add @user` — Add a user to the current thread\n"
        "`.thread remove @user` — Remove a user from the current thread"
    ))
    await ctx.send(embed=embed)

@thread_cmd.command(name='create')
async def thread_create(ctx):
    try:
        thread = await ctx.channel.create_thread(
            name=f"{ctx.author.name}'s Thread",
            type=discord.ChannelType.private_thread,
            auto_archive_duration=1440
        )
        await thread.add_user(ctx.author)
        await thread.send(f"Welcome {ctx.author.mention}! 👋 This is your private thread.")
        embed = discord.Embed(title="💬 Thread Created", description=f"Your thread: {thread.mention}", color=0x00FF99)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to create private threads!")
    except Exception as e:
        await ctx.send(f"❌ Could not create thread: {e}")

@thread_cmd.command(name='close')
async def thread_close(ctx):
    if not isinstance(ctx.channel, discord.Thread):
        await ctx.send("❌ This command can only be used inside a thread!")
        return
    try:
        await ctx.send("🗑️ Deleting thread...")
        await ctx.channel.delete()
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to delete this thread!")
    except Exception as e:
        await ctx.send(f"❌ Could not delete thread: {e}")

@thread_cmd.command(name='add')
async def thread_add(ctx, member: discord.Member = None):
    if not isinstance(ctx.channel, discord.Thread):
        await ctx.send("❌ This command can only be used inside a thread!")
        return
    if member is None:
        await ctx.send("❌ Please mention a user. Usage: `.thread add @user`")
        return
    try:
        await ctx.channel.add_user(member)
        embed = discord.Embed(title="💬 User Added", description=f"{member.mention} has been added to the thread.", color=0x00FF99)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to add users to this thread!")
    except Exception as e:
        await ctx.send(f"❌ Could not add user: {e}")

@thread_cmd.command(name='remove')
async def thread_remove(ctx, member: discord.Member = None):
    if not isinstance(ctx.channel, discord.Thread):
        await ctx.send("❌ This command can only be used inside a thread!")
        return
    if member is None:
        await ctx.send("❌ Please mention a user. Usage: `.thread remove @user`")
        return
    try:
        await ctx.channel.remove_user(member)
        embed = discord.Embed(title="💬 User Removed", description=f"{member.mention} has been removed from the thread.", color=0xFF4444)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to remove users from this thread!")
    except Exception as e:
        await ctx.send(f"❌ Could not remove user: {e}")

# ── General ───────────────────────────────────────────────────────────────────

@bot.command(name='leaderboard', aliases=['lb'])
async def leaderboard(ctx):
    data = load_data(); users = {k:v for k,v in data.items() if not k.startswith('__') and 'balance' in v}
    if not users: await ctx.send("❌ No players yet!"); return
    top = sorted(users.items(), key=lambda x: x[1]['balance'], reverse=True)[:10]
    medals = ["🥇","🥈","🥉"] + ["🏅"]*7
    lines = []
    for idx, (uid, ud) in enumerate(top):
        try: user = await bot.fetch_user(int(uid)); name = user.name
        except: name = "Unknown"
        lines.append(f"{medals[idx]} **{name}**  —  {fmt(ud['balance'])}")
    embed = discord.Embed(title="🏆  LuckyBet Leaderboard", description="\n".join(lines), color=0xFFD700)
    await ctx.send(embed=embed)


@bot.command(name='stats')
async def stats(ctx, member: discord.Member = None):
    target = member or ctx.author; data, uid = get_user(target.id); ud = data[uid]; s = ud['stats']
    total = s['wins'] + s['losses']; rank_info, _ = get_rank_info(s['total_wagered'])
    now = datetime.now(timezone.utc); last_daily = ud.get('last_daily')
    if last_daily:
        ld = datetime.fromisoformat(last_daily)
        if ld.tzinfo is None: ld = ld.replace(tzinfo=timezone.utc)
        diff = now - ld
        if diff.total_seconds() < 86400:
            rem = timedelta(seconds=86400) - diff
            h = int(rem.total_seconds()//3600); m = int((rem.total_seconds()%3600)//60)
            daily_str = f"⏳ Ready in {h}h {m}m"
        else: daily_str = "✅ Ready to claim!"
    else: daily_str = "✅ Ready to claim!"
    bal = ud['balance']; div = "─"*8
    desc = (
        f"💰 **Main Balance:** {bal:,.2f} points\n"
        f"🎁 **Daily Reward:** {daily_str}\n"
        f"🏆 **Rank:** {rank_info[1]}\n\n"
        f"`{div} LIFETIME STATISTICS {div}`\n"
        f"🎲 **Games Played**\n{total:,}\n"
        f"🏆 **Games Won**\n{s['wins']:,}\n"
        f"💀 **Games Lost**\n{s['losses']:,}\n"
        f"💸 **Total Wagered**\n{s['total_wagered']:,.2f} points\n"
        f"🎁 **Bonus Received**\n{ud.get('bonus_received',0):,.2f} points\n"
        f"📤 **Tips Sent**\n{ud.get('tips_sent',0):,.2f} points\n"
        f"📥 **Tips Received**\n{ud.get('tips_received',0):,.2f} points\n"
        f"🏦 **Total Withdrawn**\n{ud.get('total_withdrawn',0):,.2f} points"
    )
    embed = discord.Embed(title=f"📊 {target.name}'s Profile", description=desc, color=0x1E90FF)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text=f"R${bal:,.2f}  ≈  ${bal*POINTS_TO_USD:.2f} USD")
    await ctx.send(embed=embed)

@stats.error
async def stats_error(ctx, error):
    if isinstance(error, commands.MemberNotFound): await ctx.send("❌ Member not found — mention them with @")
    else: await ctx.send("❌ Usage: `.stats` or `.stats @user`")


@bot.command(name='help')
async def help_command(ctx):
    embed = discord.Embed(title="🎰  LuckyBet — Commands", color=0x9B59B6)
    embed.add_field(name="🎮 Games", value=(
        "`.coinflip` / `.cf <amt> <h/t>` — Coin flip 1:1\n"
        "`.dice <amt> <1-6>` — Dice guess ×5\n"
        "`.slots <amt>` — Slots up to ×100\n"
        "`.roulette <amt> <r/b/e/o>` — Roulette ×2\n"
        "`.blackjack` / `.bj <amt>` — Hit, Stand, Double\n"
        "`.mines <amt> [mines]` — Provably fair mines\n"
        "`.crash <amt>` — Multiplayer crash game"
    ), inline=False)
    embed.add_field(name="🎁 Rewards", value=(
        "`.daily` — 5 pts free (24h cooldown)\n"
        "`.monthly` — 1pt per R$1,000 wagered\n"
        "`.rakeback` — Claim 0.2% of your losses"
    ), inline=False)
    embed.add_field(name="🤝 Social", value=(
        "`.send @user <amt>` — Send points\n"
        "`.rain <amt>` — Rain points on joiners (2 min)\n"
        "`.clan <create/join/leave/info/top>` — Clan system\n"
        "`.thread` — Create a private thread"
    ), inline=False)
    embed.add_field(name="📊 Info", value=(
        "`.balance` / `.bal` — Your balance\n"
        "`.stats [@user]` — Full profile & lifetime stats\n"
        "`.rank` — Full rank progress\n"
        "`.leaderboard` / `.lb` — Top 10 players\n"
        "`.price` — Points price table"
    ), inline=False)
    embed.add_field(name="🛡️ Admin", value=(
        "`.addbal @user <amt>` — Add balance\n"
        "`.removebal @user <amt>` — Remove balance\n"
        "`.updwithdraw @user <amt>` — Add to withdraw total\n"
        "`.resetstats` — Reset all players' stats\n"
        "`.setrank <rank> @role` — Link rank to a role\n"
        "`.rankroles` — View current rank→role config"
    ), inline=False)
    embed.add_field(name="💱 Currency", value="R$1 = 1 point  |  R$1,000 = $3.70 USD", inline=False)
    await ctx.send(embed=embed)


if __name__ == "__main__":
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN: print("❌ DISCORD_TOKEN not set!")
    else: bot.run(TOKEN)
