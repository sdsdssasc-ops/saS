"""أوامر الليدربورد: تعديل اللاعبين، وتحديث صورة الروم تلقائياً."""
import asyncio
import io
import json
import traceback
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import leaderboard_image

DATA_DIR = Path(__file__).parent / "data"
DATA_FILE = DATA_DIR / "players.json"
STATE_FILE = DATA_DIR / "state.json"  # يحفظ الروم والرسالة اللي فيها الصورة


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


class Leaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lock = asyncio.Lock()

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
                        # انحذفت الرسالة، نرسل وحدة جديدة
                        file = await self._render_file()
                message = await channel.send(file=file)
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
            return "\n(ما أرسلت الليدربورد لروم بعد، استخدم /lb_send)"
        return f"\n⚠️ ما قدرت أحدّث الصورة: {error}" if error else "\nتحدثت الصورة في الروم ✅"

    @app_commands.command(name="lb_send", description="يسوي صورة الليدربورد ويرسلها في الروم، وبعدها تتحدث تلقائياً")
    @app_commands.describe(channel="الروم اللي يرسل فيه الليدربورد")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def lb_send(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not load_players():
            await interaction.response.send_message("ما فيه لاعبين بعد. استخدم /lb_set عشان تضيف.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        _save(STATE_FILE, {"channel_id": channel.id, "message_id": None})  # رسالة جديدة في الروم
        error = await self.refresh()
        if error:
            await interaction.followup.send(f"ما قدرت أرسل الليدربورد: {error}", ephemeral=True)
            return
        await interaction.followup.send(f"تم إرسال الليدربورد في {channel.mention} ✅\nأي إضافة أو حذف يحدّث الصورة تلقائياً.", ephemeral=True)

    @app_commands.command(name="lb_set", description="يضيف لاعب أو يعدل نقاطه وسلسلة فوزه")
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
        key = name.strip().lower()
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
        if data.pop(name.strip().lower(), None) is None:
            await interaction.followup.send("ما لقيت اللاعب هذا.", ephemeral=True)
            return
        save_players(data)
        error = await self.refresh()
        await interaction.followup.send(f"تم حذف **{name.strip()}**" + self._status(error), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))
