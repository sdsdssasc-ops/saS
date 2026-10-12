import os
import discord
from discord import app_commands
from discord.ext import commands
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("MTU1OTE2NzkxNDg0OTA3NTI0MA.GEN0Tm.I8AzsM9Tk5yKylMIN7eCrOc-JZyb8p6hDa2T-Q")

intents = discord.Intents.default()
intents.members = True  # لازم تفعّله من Developer Portal



async def start_web_server():
    # سيرفر صغير عشان Render (Web Service) يشوف بورت مفتوح، ونقدر نسوي له ping
    app = web.Application()
    app.router.add_get("/", lambda request: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000))).start()


class MyBot(commands.Bot):
    async def setup_hook(self):
        await start_web_server()


bot = MyBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"البوت شغال: {bot.user}")


# ترحيب بالأعضاء الجدد (يرسل في قناة اسمها welcome أو القناة الافتراضية)
@bot.event
async def on_member_join(member: discord.Member):
    channel = discord.utils.get(member.guild.text_channels, name="welcome") or member.guild.system_channel
    if channel:
        await channel.send(f"أهلاً وسهلاً {member.mention} في **{member.guild.name}** 👋")


@bot.tree.command(name="ping", description="يفحص سرعة البوت")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 {round(bot.latency * 1000)}ms")


@bot.tree.command(name="clear", description="يحذف عدد من الرسائل")
@app_commands.describe(amount="عدد الرسائل (1-100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"تم حذف {len(deleted)} رسالة", ephemeral=True)


@bot.tree.command(name="kick", description="يطرد عضو")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "بدون سبب"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"تم طرد {member.mention} | السبب: {reason}")


@bot.tree.error
async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("ما عندك صلاحية لهذا الأمر.", ephemeral=True)
    else:
        raise error


bot.run(TOKUN)
