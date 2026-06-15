import asyncio
from TikTokApi import TikTokApi

async def main():
    async with TikTokApi() as api:
        await api.create_sessions(
            num_sessions=1,
            headless=False,
        )

        user = api.user(username="khoailangthang")
        info = await user.info()
        print(info)

asyncio.run(main())