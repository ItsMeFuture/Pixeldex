from ballsdex.packages.shop.cog import Shop
from ballsdex.packages.shop.admin_commands import ShopAdmin

async def setup(bot):
    await bot.add_cog(Shop(bot))
    await bot.add_cog(ShopAdmin(bot))