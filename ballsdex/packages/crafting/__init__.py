from ballsdex.packages.crafting.cog import Crafting

async def setup(bot):
    await bot.add_cog(Crafting(bot))