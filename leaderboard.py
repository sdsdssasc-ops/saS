"""أوامر الليدربورد: تعديل اللاعبين وإرسال الصورة لروم تحدده."""
import asyncio
import io
import json
import traceback
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

import leaderboard_image

DATA_FILE = Path(__file__).parent / "data" / "players.json"


def load_players() -> dict:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_players(data: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class Leaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="lb_send", description="يسوي صورة الليدربورد ويرسلها في الروم اللي تحدده")
    @app_commands.describe(channel="الروم اللي يرسل فيه الليدربورد")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def lb_send(self, interaction: discord.Interaction, channel: discord.TextChannel):
        players = list(load_players().values())
        if not players:
            await interaction.response.send_message("ما فيه لاعبين بعد. استخدم /lb_set عشان تضيف.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            png = await asyncio.to_thread(leaderboard_image.render, players)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            await interaction.followup.send(
                f"صار خطأ وأنا أرسم الصورة: `{type(e).__name__}: {e}`\nغالباً ملف ناقص (logo.png أو الخطوط) بجانب bot.py.",
                ephemeral=True,
            )
            return
        try:
            await channel.send(file=discord.File(fp=io.BytesIO(png), filename="leaderboard.png"))
        except discord.Forbidden:
            await interaction.followup.send(f"ما عندي صلاحية الإرسال أو رفع الصور في {channel.mention}.", ephemeral=True)
            return
        await interaction.followup.send(f"تم إرسال الليدربورد في {channel.mention} ✅", ephemeral=True)

    @app_commands.command(name="lb_set", description="يضيف لاعب أو يعدل نقاطه وسلسلة فوزه")
    @app_commands.describe(name="اسم اللاعب", points="النقاط", streak="سلسلة الفوز الحالية", best="أفضل سلسلة (اختياري)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def lb_set(
        self,
        interaction: discord.Interaction,
        name: str,
        points: app_commands.Range[int, 0, 999999],
        streak: app_commands.Range[int, 0, 9999] = 0,
        best: app_commands.Range[int, 0, 9999] | None = None,
    ):
        data = load_players()
        key = name.strip().lower()
        old_best = data.get(key, {}).get("best", 0)
        data[key] = {"name": name.strip(), "points": points, "streak": streak, "best": max(best if best is not None else old_best, streak)}
        save_players(data)
        p = data[key]
        await interaction.response.send_message(
            f"تم: **{p['name']}** | {p['points']} نقطة | سلسلة {p['streak']} / {p['best']}", ephemeral=True
        )

    @app_commands.command(name="lb_remove", description="يحذف لاعب من الليدربورد")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def lb_remove(self, interaction: discord.Interaction, name: str):
        data = load_players()
        if data.pop(name.strip().lower(), None) is None:
            await interaction.response.send_message("ما لقيت اللاعب هذا.", ephemeral=True)
            return
        save_players(data)
        await interaction.response.send_message(f"تم حذف **{name.strip()}**", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))
