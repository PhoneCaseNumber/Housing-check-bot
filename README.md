# Housing Lookup Bot

A small Discord bot that looks up a Minecraft player's public Hypixel Housing information when a command is run.

> **Disclaimer:** This project is not affiliated with, endorsed by, or approved by Hypixel Inc. or Mojang.

## What it does

- `/하우징확인 <player>` shows the player's houses: name, current player count, and current cookie count.
- `/핑` shows the bot's response time.

Data is fetched only when a command is used. The bot does not poll in the background and does not store any lookup history.

## Access

- Commands can only be used by the Discord user IDs listed in `ALLOWED_USER_IDS`. The bot does not start if this list is empty.
- The bot can be limited to specific servers with `ALLOWED_GUILD_IDS`. It leaves any other server it is added to.
- This is a personal, non-monetized project intended for a private server.

## API usage

| Service | Endpoint | When |
|---|---|---|
| Hypixel Public API | `GET /v2/housing/houses?player=<uuid>` | Once per command, unless a cached result exists |
| Mojang API | `GET /users/profiles/minecraft/<name>` | Once per name, cached for 24 hours |

- Hypixel results are cached for 1 hour per player (`PLAYER_CACHE_TTL`).
- Each user has a 10-second command cooldown (`COMMAND_COOLDOWN`).
- On an HTTP 429 response the bot stops sending requests until the rate limit resets.
- The API key is sent in the `API-Key` header and is read from the environment. It is never stored in the code.

## Setup

Requires Python 3.10+.

```bash
pip install discord.py aiohttp python-dotenv
```

Create a `.env` file next to the script:

```env
BOT_TOKEN=your_discord_bot_token
HYPIXEL_API_KEY=your_hypixel_api_key
ALLOWED_USER_IDS=123456789012345678
# optional
ALLOWED_GUILD_IDS=123456789012345678
COMMAND_COOLDOWN=10
PLAYER_CACHE_TTL=3600
```

Run:

```bash
python housing_lookup_bot.py
```

Do not commit `.env`. Add it to `.gitignore`.

## 한국어 요약

명령어를 입력할 때 플레이어의 Hypixel 하우징 정보(이름, 현재 인원, 쿠키 수)를 한 번 조회하는 개인용 디스코드 봇입니다. 백그라운드 조회와 기록 저장은 없고, 허용된 유저만 사용할 수 있습니다. Hypixel과 제휴 관계가 아니며 승인을 받지 않았습니다.
