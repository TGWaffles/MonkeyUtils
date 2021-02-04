import discord
import numpy as np
import chess.svg
import random

from src.helpers.storage_helper import DataHelper
from src.storage import config
from discord.ext import commands
from main import UtilsBot
from scipy.signal import convolve2d


class Games(commands.Cog):
    def __init__(self, bot: UtilsBot):
        self.bot: UtilsBot = bot
        self.data = DataHelper()

    async def connect4_send_to_player(self, player, board: np.array, their_turn):
        board_embed = discord.Embed(title="Connect Four!", colour=discord.Colour.light_grey())
        for array in board:
            for character in array:
                board_embed.description += [config.c4_none, config.c4_red, config.c4_yellow][character]
            board_embed.description += "\n"
        board_embed.add_field(name="Turn", value="It is " + ("NOT ", "")[their_turn] +"your turn!")

    @commands.command(description="Play connect four!", aliases=["connectfour", "connect_four", "c4"],
                      enabled=False)
    async def connect4(self, ctx, player2: discord.Member):
        connect_four_games = self.data.get("ongoing_games", {}).get("connect_four", {})
        player1 = ctx.author
        sorted_ids = sorted([player1.id, player2.id])
        combined_id = "{}-{}".format(*sorted_ids)
        if combined_id in connect_four_games:
            await ctx.reply(embed=self.bot.create_error_embed("You already have a connect four game with that person!"))
            return
        game_board = np.array([[None] * 6] * 7)

    @staticmethod
    def get_kernels():
        horizontal_kernel = np.array([[ 1, 1, 1, 1]])
        vertical_kernel = np.transpose(horizontal_kernel)
        diag1_kernel = np.eye(4, dtype=np.uint8)
        diag2_kernel = np.fliplr(diag1_kernel)
        return [horizontal_kernel, vertical_kernel, diag1_kernel, diag2_kernel]

    async def connect4_check_win(self, board, player_id):
        detection_kernels = self.get_kernels()
        for kernel in detection_kernels:
            if (convolve2d(board == player_id, kernel, mode="valid") == 4).any():
                return True
        return False

    @commands.command()
    async def chess(self, ctx, player2: discord.Member):
        chess_games = self.data.get("ongoing_games", {}).get("chess_games", {})
        player1 = ctx.author
        possible_id_1 = "{}-{}".format(player1.id, player2.id)
        possible_id_2 = "{}-{}".format(player2.id, player1.id)
        if possible_id_1 in chess_games or possible_id_2 in chess_games:
            await ctx.reply(embed=self.bot.create_error_embed("You already have a chess game with that person!"))
            return
        new_game = chess.Board()
        white, black = random.choice([player1.id, player2.id])
        game_id = "{}-{}".format(white, black)
        chess_games[game_id] = new_game.fen()

    async def send_current_board_state(self, game_id):
        





def setup(bot):
    cog = Games(bot)
    bot.add_cog(cog)

