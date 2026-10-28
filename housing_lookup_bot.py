"""
Hypixel 하우징 정보 조회 Discord 봇

명령어를 입력하면 해당 플레이어의 하우징 정보를 한 번 조회해서 보여줍니다.
허용된 유저(ALLOWED_USER_IDS)만 사용할 수 있습니다.
이 봇은 Hypixel과 제휴 관계가 아니며 Hypixel의 승인을 받지 않았습니다.

환경변수 (.env):
    BOT_TOKEN          Discord 봇 토큰
    HYPIXEL_API_KEY    Hypixel API 키
    ALLOWED_USER_IDS   사용 가능한 디스코드 유저 ID (쉼표로 구분)
    ALLOWED_GUILD_IDS  (선택) 봇이 머물 수 있는 서버 ID
    COMMAND_COOLDOWN   (선택) 명령어 쿨다운(초), 기본 10
    PLAYER_CACHE_TTL   (선택) 조회 결과 캐시 시간(초), 기본 3600
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ───────────────────────── 설정 ─────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
API_KEY = os.environ["HYPIXEL_API_KEY"]


def _ids(name: str) -> set[int]:
    return {int(x) for x in os.environ.get(name, "").replace(" ", "").split(",") if x}


ALLOWED_USER_IDS = _ids("ALLOWED_USER_IDS")
ALLOWED_GUILD_IDS = _ids("ALLOWED_GUILD_IDS")
if not ALLOWED_USER_IDS:
    raise SystemExit("ALLOWED_USER_IDS 가 비어 있습니다. .env 에 본인 디스코드 유저 ID를 넣어주세요.")

COMMAND_COOLDOWN = int(os.environ.get("COMMAND_COOLDOWN", "10"))
PLAYER_CACHE_TTL = int(os.environ.get("PLAYER_CACHE_TTL", "3600"))
UUID_CACHE_TTL = 86400

KST = timezone(timedelta(hours=9))
MOJANG_URL = "https://api.mojang.com/users/profiles/minecraft/{}"
HOUSING_URL = "https://api.hypixel.net/v2/housing/houses"
NAME_RE = re.compile(r"[A-Za-z0-9_]{1,16}")
DISCLAIMER = "Hypixel과 제휴/승인 관계 없음"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("housing")

_uuid_cache: dict[str, tuple[float, tuple[str, str]]] = {}
_house_cache: dict[str, tuple[float, list]] = {}
_user_last_used: dict[int, float] = {}
_rate_limited_until = 0.0


class HypixelError(Exception):
    pass


def strip_color(text: str) -> str:
    return re.sub(r"§.", "", text or "")


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ───────────────────────── 봇 ─────────────────────────
class OwnerTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in ALLOWED_USER_IDS:
            return True
        await interaction.response.send_message("이 봇은 허용된 사용자만 쓸 수 있어요.", ephemeral=True)
        return False


class HousingBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default(), tree_cls=OwnerTree)
        self.session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        await self.tree.sync()

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()


bot = HousingBot()


async def leave_unallowed_guilds() -> None:
    if not ALLOWED_GUILD_IDS:
        return
    for guild in bot.guilds:
        if guild.id not in ALLOWED_GUILD_IDS:
            log.warning("허용되지 않은 서버에서 나갑니다: %s (%s)", guild.name, guild.id)
            await guild.leave()


@bot.event
async def on_ready():
    log.info("%s 로그인 완료", bot.user)
    await leave_unallowed_guilds()


@bot.event
async def on_guild_join(guild: discord.Guild):
    await leave_unallowed_guilds()


# ───────────────────────── API ─────────────────────────
async def resolve_player(name: str) -> tuple[str, str] | None:
    key = name.lower()
    cached = _uuid_cache.get(key)
    if cached and time.time() - cached[0] < UUID_CACHE_TTL:
        return cached[1]
    async with bot.session.get(MOJANG_URL.format(name)) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
    profile = (data["name"], data["id"])
    _uuid_cache[key] = (time.time(), profile)
    return profile


async def fetch_houses(player_uuid: str) -> tuple[float, list]:
    global _rate_limited_until
    now = time.time()
    cached = _house_cache.get(player_uuid)
    if cached and now - cached[0] < PLAYER_CACHE_TTL:
        return cached

    if time.monotonic() < _rate_limited_until:
        raise HypixelError("요청 한도에 걸려 있어요. 잠시 후 다시 시도해주세요.")

    async with bot.session.get(
        HOUSING_URL, params={"player": player_uuid}, headers={"API-Key": API_KEY}
    ) as resp:
        if resp.status == 429:
            wait = min(_int(resp.headers.get("RateLimit-Reset"), 60), 300)
            _rate_limited_until = time.monotonic() + wait
            raise HypixelError(f"요청 한도에 걸렸어요. {wait}초 후 다시 시도해주세요.")
        if resp.status in (401, 403):
            raise HypixelError("API 키가 잘못되었거나 만료됐어요.")
        if resp.status != 200:
            raise HypixelError(f"Hypixel 응답 오류 (HTTP {resp.status})")
        data = await resp.json()

    if isinstance(data, list):
        houses = data
    elif not data.get("success", True):
        raise HypixelError(data.get("cause", "알 수 없는 오류"))
    else:
        houses = data.get("houses", [])

    _house_cache[player_uuid] = (now, houses)
    return now, houses


# ───────────────────────── 명령어 ─────────────────────────
def check_cooldown(user_id: int) -> float | None:
    now = time.monotonic()
    last = _user_last_used.get(user_id)
    if last is not None and now - last < COMMAND_COOLDOWN:
        return COMMAND_COOLDOWN - (now - last)
    _user_last_used[user_id] = now
    return None


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {message}", color=discord.Color.red())


def created_text(created_at_ms) -> str:
    sec = _int(created_at_ms, 0) // 1000
    if sec <= 0:
        return ""
    return datetime.fromtimestamp(sec, KST).strftime("%Y.%m.%d")


@bot.tree.command(name="하우징확인", description="플레이어의 하우징 정보를 한 번 조회합니다")
@app_commands.describe(player="하우징을 확인할 마인크래프트 닉네임")
async def housing_check(interaction: discord.Interaction, player: str):
    remaining = check_cooldown(interaction.user.id)
    if remaining is not None:
        await interaction.response.send_message(
            embed=error_embed(f"**{remaining:.0f}초** 후에 다시 시도해주세요."), ephemeral=True
        )
        return
    await interaction.response.defer()

    player = player.strip()
    if not NAME_RE.fullmatch(player):
        await interaction.followup.send(embed=error_embed("닉네임 형식이 올바르지 않아요."))
        return

    try:
        profile = await resolve_player(player)
        if not profile:
            await interaction.followup.send(embed=error_embed(f"**`{player}`** 플레이어를 찾을 수 없어요."))
            return
        name, uuid = profile
        fetched_at, houses = await fetch_houses(uuid)
    except HypixelError as e:
        await interaction.followup.send(embed=error_embed(str(e)))
        return
    except (aiohttp.ClientError, asyncio.TimeoutError):
        await interaction.followup.send(embed=error_embed("네트워크 오류가 났어요. 잠시 후 다시 시도해주세요."))
        return

    if not houses:
        await interaction.followup.send(embed=error_embed(f"**`{name}`**님의 하우징을 찾을 수 없어요."))
        return

    embeds = []
    for h in houses[:10]:
        players = h.get("players", 0)
        cookies = h.get("cookies", {}).get("current", 0)
        embed = discord.Embed(
            title=f"🏠 {strip_color(h.get('name', '')) or '이름없음'}",
            description=f"`{name}`\n\n`{'🟢' if players else '🔴'} {players}명`  `🍪 {cookies:,}개`",
            color=discord.Color.green() if players else discord.Color.red(),
            timestamp=datetime.fromtimestamp(fetched_at, timezone.utc),
        )
        parts = []
        created = created_text(h.get("createdAt"))
        if created:
            parts.append(f"개설일 {created}")
        parts.append(DISCLAIMER)
        embed.set_footer(text=" · ".join(parts))
        embeds.append(embed)

    await interaction.followup.send(embeds=embeds)


@bot.tree.command(name="핑", description="봇 상태 확인")
async def ping(interaction: discord.Interaction):
    embed = discord.Embed(title="🏓 Pong!", color=discord.Color.green())
    embed.add_field(name="응답 속도", value=f"{bot.latency * 1000:.0f}ms")
    embed.set_footer(text="이 봇은 Hypixel과 제휴 관계가 아니며 Hypixel의 승인을 받지 않았습니다.")
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    bot.run(BOT_TOKEN)
