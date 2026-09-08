import asyncio
import traceback
from mcp_client import client


async def main():
    try:
        tools = await client.get_tools(server_name="aviationstack")
        print("SUCCESS:", [t.name for t in tools])
    except Exception as exc:
        print("FULL NESTED TRACEBACK:")
        traceback.print_exception(type(exc), exc, exc.__traceback__)


asyncio.run(main())