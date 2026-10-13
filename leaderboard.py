"""ليدربورد PVP + تسجيل مباريات 1V1 / 2V2 / 3V3 / 4V4 مع تأكيد النتيجة من الطرفين."""
import asyncio
import io
import json
import os
import re
import traceback
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import leaderboard_image

# ====== إعدادات تقدر تغيرها ======
WIN_POINTS = 25          # نقاط الفائز
LOSS_POINTS = 15         # نقاط تنخصم من الخاسر (ما تنزل تحت 0)
LOBBY_TIMEOUT = 15 * 60  # بعد كم ثانية ينلغي اللوبي لو ما أحد انضم
MAX_MISMATCHES = 3       # كم مرة نسمح بنتيجتين مختلفتين قبل ما تنلغي المباراة
# =================================

DATA_DIR = Path(__file__).parent / "data"
DATA_FILE = DATA_DIR / "players.json"
STATE_FILE = DATA_DIR / "state.json"  # الروم والرسالة اللي فيها الليدربورد

ROBLOX_PREFIX = re.compile(r"^https?://(www\.)?(roblox\.com|ro\.blox\.com)/", re.I)
SCORE_RE = re.compile(r"^\s*(\d{1,2})\s*[-–—:]\s*(\d{1,2})\s*$")

# اسم حساب روبلوكس لكل لاعب (يتعبى من Bloxlink لو حطيت BLOXLINK_API_KEY)
ROBLOX_NAMES: dict[int, str] = {}


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_players() -> dict:
    return _load(DATA_FILE)


def save_players(data: dict) -> None:
    _save(DATA_FILE, data)


def find_key(data: dict, name: str) -> str:
    """يدور على اللاعب بالاسم (يشمل اللي تسجلوا من المباريات)."""
    for key, rec in data.items():
        if rec.get("name", "").lower() == name.strip().lower():
            return key
    return name.strip().lower()


def names(users) -> str:
    return "، ".join(ROBLOX_NAMES.get(u.id, u.display_name) for u in users) or "—"


class Lobby:
    def __init__(self, guild_id, size, host, link, channel):
        self.guild_id = guild_id
        self.size = size
        self.link = link
        self.channel = channel
        self.players = [host]  # الأول هو المضيف (فريق A)، وبعدها نوزع بالتناوب A/B
        self.message: Optional[discord.Message] = None
        self.task: Optional[asyncio.Task] = None

    @property
    def host(self):
        return self.players[0]

    @property
    def full(self):
        return len(self.players) >= self.size * 2

    def teams(self):
        return self.players[0::2], self.players[1::2]


class Match:
    def __init__(self, size, link, team_a, team_b, channel):
        self.size = size
        self.link = link
        self.team_a = team_a
        self.team_b = team_b
        self.channel = channel
        self.submissions: dict[int, tuple] = {}  # captain_id -> (won, a, b)
        self.mismatches = 0
        self.done = False

    @property
    def captain_a(self):
        return self.team_a[0]

    @property
    def captain_b(self):
        return self.team_b[0]

    @property
    def players(self):
        return self.team_a + self.team_b


# ---------------------------------------------------------------- واجهات (أزرار ونوافذ)

class PanelView(discord.ui.View):
    """الأزرار اللي تحت صورة الليدربورد."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="تسجيل 1V1", style=discord.ButtonStyle.primary, custom_id="pvp:1")
    async def b1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.on_mode(interaction, 1)

    @discord.ui.button(label="تسجيل 2V2", style=discord.ButtonStyle.primary, custom_id="pvp:2")
    async def b2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.on_mode(interaction, 2)

    @discord.ui.button(label="تسجيل 3V3", style=discord.ButtonStyle.primary, custom_id="pvp:3")
    async def b3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.on_mode(interaction, 3)

    @discord.ui.button(label="تسجيل 4V4", style=discord.ButtonStyle.primary, custom_id="pvp:4")
    async def b4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.on_mode(interaction, 4)


class LinkModal(discord.ui.Modal):
    link = discord.ui.TextInput(
        label="رابط السيرفر الخاص (روبلوكس)", placeholder="https://www.roblox.com/...", max_length=300
    )

    def __init__(self, cog, size: int):
        super().__init__(title=f"تسجيل {size}V{size}")
        self.cog = cog
        self.size = size

    async def on_submit(self, interaction: discord.Interaction):
        parts = self.link.value.strip().split()
        token = parts[0] if parts else ""
        if not ROBLOX_PREFIX.match(token):
            await interaction.response.send_message(
                "الرابط لازم يكون رابط روبلوكس (يبدأ بـ https://www.roblox.com/). اضغط الزر وجرب مرة ثانية.",
                ephemeral=True,
            )
            return
        await self.cog.create_lobby(interaction, self.size, token)


class LobbyView(discord.ui.View):
    def __init__(self, cog, lobby: Lobby):
        super().__init__(timeout=None)
        self.cog = cog
        self.lobby = lobby

    @discord.ui.button(label="إلغاء اللوبي", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_host = interaction.user.id == self.lobby.host.id
        is_admin = getattr(interaction.user, "guild_permissions", None) and interaction.user.guild_permissions.manage_guild
        if not (is_host or is_admin):
            await interaction.response.send_message("بس صاحب اللوبي أو الإدارة يقدرون يلغونه.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.cog.cancel_lobby(self.lobby, "ألغاه صاحب اللوبي")
        await interaction.followup.send("تم إلغاء اللوبي.", ephemeral=True)


class ScoreModal(discord.ui.Modal):
    score = discord.ui.TextInput(label="النتيجة (رقم الفائز أول) مثل 5-2", placeholder="5-2", max_length=7)

    def __init__(self, cog, match: Match, won: bool):
        super().__init__(title="كتابة النتيجة")
        self.cog = cog
        self.match = match
        self.won = won

    async def on_submit(self, interaction: discord.Interaction):
        m = SCORE_RE.match(self.score.value)
        if not m or int(m[1]) <= int(m[2]):
            await interaction.response.send_message(
                "اكتب النتيجة مثل `5-2` (رقم الفائز أول وأكبر من رقم الخاسر). اضغط الزر وحاول مرة ثانية.",
                ephemeral=True,
            )
            return
        await self.cog.submit_result(interaction, self.match, self.won, int(m[1]), int(m[2]))


class ResultView(discord.ui.View):
    def __init__(self, cog, match: Match, captain_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.match = match
        self.captain_id = captain_id

    async def _ok(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.captain_id:
            await interaction.response.send_message("هذا الزر لكابتن الفريق بس.", ephemeral=True)
            return False
        if self.match.done:
            await interaction.response.send_message("المباراة هذي انتهت أو انلغت.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="فزنا ✅", style=discord.ButtonStyle.success)
    async def won(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._ok(interaction):
            await interaction.response.send_modal(ScoreModal(self.cog, self.match, True))

    @discord.ui.button(label="خسرنا ❌", style=discord.ButtonStyle.danger)
    async def lost(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._ok(interaction):
            await interaction.response.send_modal(ScoreModal(self.cog, self.match, False))


# ---------------------------------------------------------------- الكوج

class Leaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.lobbies: dict[tuple, Lobby] = {}
        self.busy: dict[int, object] = {}  # user_id -> Lobby أو Match

    async def cog_load(self):
        self.bot.add_view(PanelView(self))  # عشان الأزرار تظل تشتغل بعد إعادة التشغيل

    # ---------- صورة الليدربورد ----------
    async def _render_file(self) -> discord.File:
        players = list(load_players().values())
        png = await asyncio.to_thread(leaderboard_image.render, players)
        return discord.File(fp=io.BytesIO(png), filename="leaderboard.png")

    async def refresh(self) -> Optional[str]:
        """يحدّث صورة الليدربورد اللي في الروم المحفوظ. يرجع نص الخطأ لو صار خطأ."""
        state = _load(STATE_FILE)
        if not state:
            return None
        async with self.lock:
            try:
                channel = self.bot.get_channel(state["channel_id"]) or await self.bot.fetch_channel(state["channel_id"])
                file = await self._render_file()
                message_id = state.get("message_id")
                if message_id:
                    try:
                        message = await channel.fetch_message(message_id)
                        await message.edit(attachments=[file])
                        return None
                    except discord.NotFound:
                        file = await self._render_file()  # انحذفت الرسالة، نرسل وحدة جديدة
                message = await channel.send(
                    content="⚔️ **TIME BOMB PVP** — اضغط على نمط اللعب عشان تسجل",
                    file=file,
                    view=PanelView(self),
                )
                state["message_id"] = message.id
                _save(STATE_FILE, state)
                return None
            except discord.Forbidden:
                return "ما عندي صلاحية الإرسال أو إرفاق الملفات في الروم."
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return f"{type(e).__name__}: {e}"

    def _status(self, error: Optional[str]) -> str:
        if not _load(STATE_FILE):
            return "\n(ما أرسلت اللوحة لروم بعد، استخدم /pvp_panel)"
        return f"\n⚠️ ما قدرت أحدّث الصورة: {error}" if error else "\nتحدثت الصورة في الروم ✅"

    # ---------- مساعدات ----------
    async def _dm(self, user, content: str, view: Optional[discord.ui.View] = None) -> bool:
        try:
            await user.send(content, view=view) if view else await user.send(content)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    def _lobby_text(self, lobby: Lobby) -> str:
        n = lobby.size
        a, b = lobby.teams()
        return (
            f"🟢 **{n}V{n}** — لوبي مفتوح ({len(lobby.players)}/{n * 2})\n"
            f"فريق A: {names(a)}\nفريق B: {names(b)}\n"
            f"اضغط زر **تسجيل {n}V{n}** في اللوحة فوق عشان تنضم."
        )

    # ---------- ربط حساب روبلوكس (Bloxlink) ----------
    async def _bloxlink_lookup(self, guild_id: int, user_id: int, key: str) -> Optional[str]:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = f"https://api.blox.link/v4/public/guilds/{guild_id}/discord-to-roblox/{user_id}"
            async with session.get(url, headers={"Authorization": key}) as r:
                if r.status == 404:
                    return None
                if r.status != 200:
                    raise RuntimeError(f"Bloxlink status {r.status}")
                data = await r.json()
            roblox_id = data.get("robloxID") or data.get("robloxId")
            if not roblox_id:
                return None
            resolved = (data.get("resolved") or {}).get("roblox") or {}
            if resolved.get("name"):
                return resolved["name"]
            async with session.get(f"https://users.roblox.com/v1/users/{roblox_id}") as r:
                if r.status == 200:
                    return (await r.json()).get("name") or f"Roblox {roblox_id}"
            return f"Roblox {roblox_id}"

    async def _check_link(self, interaction: discord.Interaction) -> bool:
        """لو BLOXLINK_API_KEY موجود، نطلب من اللاعب يكون رابط حسابه في Bloxlink."""
        key = os.getenv("BLOXLINK_API_KEY")
        if not key or interaction.user.id in ROBLOX_NAMES:
            return True
        await interaction.response.defer(ephemeral=True)  # عشان ما نتقيد بـ 3 ثواني
        try:
            name = await self._bloxlink_lookup(interaction.guild_id, interaction.user.id, key)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            await interaction.followup.send(
                f"ما قدرت أتحقق من حسابك في Bloxlink الحين.\nالسبب: `{type(e).__name__}: {e}`\n"
                "لو تكرر، تأكد من المفتاح `BLOXLINK_API_KEY` في رندر.",
                ephemeral=True,
            )
            return False
        if not name:
            await interaction.followup.send(
                "لازم تربط حسابك في روبلوكس أول. اكتب `/verify` (بوت Bloxlink) وبعدها اضغط زر التسجيل من جديد.",
                ephemeral=True,
            )
            return False
        ROBLOX_NAMES[interaction.user.id] = name
        await interaction.followup.send(
            f"تم التحقق من حسابك: **{name}** ✅\nاضغط زر التسجيل مرة ثانية عشان تكمل.", ephemeral=True
        )
        return False  # أول مرة فقط، المرة الجاية يمشي مباشرة

    # ---------- التسجيل ----------
    async def on_mode(self, interaction: discord.Interaction, size: int):
        if interaction.user.id in self.busy:
            await interaction.response.send_message(
                "أنت مسجل بلوبي أو مباراة الحين. خلّصها أو اطلب من الإدارة `/pvp_reset`.", ephemeral=True
            )
            return
        if not await self._check_link(interaction):
            return
        lobby = self.lobbies.get((interaction.guild_id, size))
        if lobby is None:
            await interaction.response.send_modal(LinkModal(self, size))  # أول لاعب: يحط رابطه
        else:
            await self.join_lobby(interaction, lobby)

    async def create_lobby(self, interaction: discord.Interaction, size: int, link: str):
        key = (interaction.guild_id, size)
        if interaction.user.id in self.busy or key in self.lobbies:
            await interaction.response.send_message(
                "سبقك لاعب وفتح لوبي. اضغط زر التسجيل مرة ثانية وتنضم له.", ephemeral=True
            )
            return
        lobby = Lobby(interaction.guild_id, size, interaction.user, link, interaction.channel)
        self.lobbies[key] = lobby
        self.busy[interaction.user.id] = lobby
        await interaction.response.send_message(
            "تمام ✅ انتظر لاعب ينضم، وراح أرسل لك تنبيه بالخاص أول ما أحد ينضم.", ephemeral=True
        )
        try:
            lobby.message = await interaction.channel.send(self._lobby_text(lobby), view=LobbyView(self, lobby))
        except discord.HTTPException:
            self._drop_lobby(lobby)
            await interaction.followup.send("ما قدرت أرسل رسالة اللوبي في الروم. تأكد من صلاحيات البوت.", ephemeral=True)
            return
        lobby.task = asyncio.create_task(self._expire(lobby))

    async def join_lobby(self, interaction: discord.Interaction, lobby: Lobby):
        user = interaction.user
        lobby.players.append(user)
        self.busy[user.id] = lobby
        team = "A" if (len(lobby.players) - 1) % 2 == 0 else "B"
        n = lobby.size
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="افتح الرابط", url=lobby.link))
        await interaction.response.send_message(
            f"✅ انضميت لمباراة **{n}V{n}** (فريق {team}).\n"
            f"📋 انسخ الرابط (اضغط عليه) وحطه في المتصفح وادخل:\n```{lobby.link}```",
            view=view,
            ephemeral=True,
        )
        await self._dm(lobby.host, f"🔔 {user.display_name} انضم لمباراتك {n}V{n} ({len(lobby.players)}/{n * 2}).")
        if lobby.full:
            await self.start_match(lobby)
        elif lobby.message:
            try:
                await lobby.message.edit(content=self._lobby_text(lobby))
            except discord.HTTPException:
                pass

    def _drop_lobby(self, lobby: Lobby):
        if self.lobbies.get((lobby.guild_id, lobby.size)) is lobby:
            del self.lobbies[(lobby.guild_id, lobby.size)]
        for p in lobby.players:
            if self.busy.get(p.id) is lobby:
                del self.busy[p.id]
        if lobby.task and lobby.task is not asyncio.current_task():
            lobby.task.cancel()

    async def cancel_lobby(self, lobby: Lobby, reason: str):
        self._drop_lobby(lobby)
        if lobby.message:
            try:
                await lobby.message.edit(content=f"🔴 انلغى اللوبي ({reason})", view=None)
            except discord.HTTPException:
                pass

    async def _expire(self, lobby: Lobby):
        await asyncio.sleep(LOBBY_TIMEOUT)
        if self.lobbies.get((lobby.guild_id, lobby.size)) is lobby:
            await self.cancel_lobby(lobby, "انتهى وقت الانتظار")

    # ---------- المباراة والنتيجة ----------
    async def start_match(self, lobby: Lobby):
        self._drop_lobby(lobby)
        team_a, team_b = lobby.teams()
        match = Match(lobby.size, lobby.link, team_a, team_b, lobby.channel)
        for p in match.players:
            self.busy[p.id] = match
        n = lobby.size
        if lobby.message:
            try:
                await lobby.message.edit(
                    content=(
                        f"⚔️ **{n}V{n}** بدأت!\nفريق A: {names(team_a)}\nفريق B: {names(team_b)}\n"
                        f"الكباتن ({match.captain_a.display_name} و {match.captain_b.display_name}) "
                        f"يكتبون النتيجة في الخاص بعد ما تخلصون."
                    ),
                    view=None,
                )
            except discord.HTTPException:
                pass
        await self.prompt_result(match, "🏁 بعد ما تخلص المباراة اضغط الزر المناسب واكتب النتيجة مثل `5-2` (رقم الفائز أول).\nلازم الطرفين يكتبون **نفس النتيجة** عشان تنحسب.")

    async def prompt_result(self, match: Match, text: str):
        for captain in (match.captain_a, match.captain_b):
            view = ResultView(self, match, captain.id)
            if not await self._dm(captain, text, view):
                await match.channel.send(f"{captain.mention} {text}", view=view)  # لو الخاص مقفل

    async def submit_result(self, interaction: discord.Interaction, match: Match, won: bool, a: int, b: int):
        match.submissions[interaction.user.id] = (won, a, b)
        if len(match.submissions) < 2:
            await interaction.response.send_message(
                f"تم تسجيل نتيجتك ({'فزتم' if won else 'خسرتم'} {a}-{b}) ✅ ننتظر الطرف الثاني.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        sa = match.submissions[match.captain_a.id]
        sb = match.submissions[match.captain_b.id]
        if sa[0] != sb[0] and sa[1:] == sb[1:]:
            await self.finish(match, a_won=sa[0], winner_score=sa[1], loser_score=sa[2])
            await interaction.followup.send("النتيجة تطابقت وتم تأكيدها ✅", ephemeral=True)
            return
        match.mismatches += 1
        match.submissions.clear()
        if match.mismatches >= MAX_MISMATCHES:
            match.done = True
            for p in match.players:
                self.busy.pop(p.id, None)
            await match.channel.send(
                f"⚠️ مباراة {match.captain_a.display_name} ضد {match.captain_b.display_name}: النتيجتين ما تطابقت "
                f"{MAX_MISMATCHES} مرات وانلغت. تواصلوا مع الإدارة."
            )
            await interaction.followup.send("النتيجة ما تطابقت وانلغت المباراة، تواصل مع الإدارة.", ephemeral=True)
            return
        await interaction.followup.send("النتيجتين ما تطابقوا، راح أرسل لكم من جديد.", ephemeral=True)
        await self.prompt_result(
            match,
            "⚠️ النتيجتين ما تطابقوا (لازم الفائز والنتيجة يتطابقون، مثل واحد فزنا `5-2` والثاني خسرنا `5-2`).\nاكتبوا من جديد:",
        )

    async def finish(self, match: Match, a_won: bool, winner_score: int, loser_score: int):
        match.done = True
        winners = match.team_a if a_won else match.team_b
        losers = match.team_b if a_won else match.team_a
        data = load_players()
        for u in winners + losers:
            key = f"id:{u.id}"
            rec = data.get(key, {"points": 0, "streak": 0, "best": 0})
            rec["name"] = ROBLOX_NAMES.get(u.id, u.display_name)
            if u in winners:
                rec["points"] += WIN_POINTS
                rec["streak"] += 1
                rec["best"] = max(rec["best"], rec["streak"])
            else:
                rec["points"] = max(0, rec["points"] - LOSS_POINTS)
                rec["streak"] = 0
            data[key] = rec
        save_players(data)
        for p in match.players:
            self.busy.pop(p.id, None)
        await self.refresh()
        n = match.size
        state = _load(STATE_FILE)
        out = self.bot.get_channel(state.get("results_channel_id", 0)) or match.channel
        try:
            await out.send(
                f"🏆 **{n}V{n}** | {names(winners)} فازوا على {names(losers)} بنتيجة **{winner_score}-{loser_score}** "
                f"(+{WIN_POINTS} / -{LOSS_POINTS})"
            )
        except discord.HTTPException:
            traceback.print_exc()

    # ---------- الأوامر ----------
    @app_commands.command(name="pvp_panel", description="يرسل لوحة الليدربورد وأزرار التسجيل في الروم، وتتحدث تلقائياً")
    @app_commands.describe(channel="الروم اللي تظهر فيه الليدربورد وأزرار التسجيل", results_channel="روم النتائج (اختياري، الافتراضي نفس الروم)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def pvp_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        results_channel: Optional[discord.TextChannel] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        _save(STATE_FILE, {
            "channel_id": channel.id,
            "message_id": None,
            "results_channel_id": (results_channel or channel).id,
        })
        error = await self.refresh()
        if error:
            await interaction.followup.send(f"ما قدرت أرسل اللوحة: {error}", ephemeral=True)
            return
        await interaction.followup.send(
            f"تم إرسال اللوحة في {channel.mention} ✅\nالنتائج تنرسل في {(results_channel or channel).mention}.", ephemeral=True
        )

    @app_commands.command(name="pvp_reset", description="يلغي كل اللوبيات والمباريات العالقة")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def pvp_reset(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        for lobby in list(self.lobbies.values()):
            await self.cancel_lobby(lobby, "ألغتها الإدارة")
        for obj in list(self.busy.values()):
            if isinstance(obj, Match):
                obj.done = True
        self.busy.clear()
        await interaction.followup.send("تم تصفير كل اللوبيات والمباريات ✅", ephemeral=True)

    @app_commands.command(name="lb_set", description="يضيف لاعب أو يعدل نقاطه وسلسلة فوزه يدوياً")
    @app_commands.describe(name="اسم اللاعب", points="النقاط", streak="سلسلة الفوز الحالية", best="أفضل سلسلة (اختياري)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def lb_set(
        self,
        interaction: discord.Interaction,
        name: str,
        points: app_commands.Range[int, 0, 999999],
        streak: app_commands.Range[int, 0, 9999] = 0,
        best: Optional[app_commands.Range[int, 0, 9999]] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        data = load_players()
        key = find_key(data, name)
        old_best = data.get(key, {}).get("best", 0)
        data[key] = {
            "name": name.strip(),
            "points": points,
            "streak": streak,
            "best": max(best if best is not None else old_best, streak),
        }
        save_players(data)
        p = data[key]
        error = await self.refresh()
        await interaction.followup.send(
            f"تم: **{p['name']}** | {p['points']} نقطة | سلسلة {p['streak']} / {p['best']}" + self._status(error),
            ephemeral=True,
        )

    @app_commands.command(name="lb_remove", description="يحذف لاعب من الليدربورد")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def lb_remove(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        data = load_players()
        key = find_key(data, name)
        if data.pop(key, None) is None:
            await interaction.followup.send("ما لقيت اللاعب هذا.", ephemeral=True)
            return
        save_players(data)
        error = await self.refresh()
        await interaction.followup.send(f"تم حذف **{name.strip()}**" + self._status(error), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))
