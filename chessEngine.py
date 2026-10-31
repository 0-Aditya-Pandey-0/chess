"""
bot.py - the chess engine behind the "With engine" mode.

app.py only needs these names from this file:
    choose_moves(board)                      -> the engine's reply (a chess.Move)
    read_positions / write_positions         -> JSON helpers (bottom of this file)
    read_legal_moves / write_legal_moves

How the engine thinks (top to bottom in this file):
    1. evaluate()   looks at ONE position and gives it a score
    2. Searcher     looks AHEAD through many moves and picks the best one
"""
import json
import threading
import time
from pathlib import Path

import chess

POSITIONS_PATH = Path(__file__).with_name("frontend") / "positions.json"
LEGAL_MOVES_PATH = Path(__file__).with_name("frontend") / "legalmoves.json"


# =============================================================================
# SETTINGS - change these and see how the engine's play changes
# =============================================================================
SEARCH_TIME = 1.5         # most seconds the engine may think per move
MAX_DEPTH = 64            # most half-moves it may look ahead (try 3 for a weak engine)
VERBOSE = True            # print a one-line summary of each search in the terminal
USE_NULL_MOVE = True      # speed-up: "what if I skipped my turn?" (prunes hopeless lines)
USE_LMR = True            # speed-up: look less deeply at moves that look unpromising
TT_MAX_ENTRIES = 300_000  # memory cap for remembered positions

MATE = 100_000            # score of "checkmate right now"
MATE_THRESHOLD = MATE - 1_000   # any score beyond this means "a forced mate was found"
INF = 10 ** 9
MAX_PLY = 64              # safety limit on search depth (including extensions)


# =============================================================================
# 1. EVALUATION - "how good is this position?"
# =============================================================================
# Scores are in centipawns: 100 = one pawn. Positive = good for White.

PIECE_VALUE = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

# Piece-square tables: a bonus/penalty for a piece standing on a square.
# Each is written the way you see the board from White's side:
# first row = rank 8, last row = rank 1, left to right = files a..h.
PAWN_TABLE = [
     0,  0,  0,  0,  0,  0,  0,  0,
    50, 50, 50, 50, 50, 50, 50, 50,
    10, 10, 20, 30, 30, 20, 10, 10,
     5,  5, 10, 25, 25, 10,  5,  5,
     0,  0,  0, 20, 20,  0,  0,  0,
     5, -5,-10,  0,  0,-10, -5,  5,
     5, 10, 10,-20,-20, 10, 10,  5,
     0,  0,  0,  0,  0,  0,  0,  0,
]
KNIGHT_TABLE = [
    -50,-40,-30,-30,-30,-30,-40,-50,
    -40,-20,  0,  0,  0,  0,-20,-40,
    -30,  0, 10, 15, 15, 10,  0,-30,
    -30,  5, 15, 20, 20, 15,  5,-30,
    -30,  0, 15, 20, 20, 15,  0,-30,
    -30,  5, 10, 15, 15, 10,  5,-30,
    -40,-20,  0,  5,  5,  0,-20,-40,
    -50,-40,-30,-30,-30,-30,-40,-50,
]
BISHOP_TABLE = [
    -20,-10,-10,-10,-10,-10,-10,-20,
    -10,  0,  0,  0,  0,  0,  0,-10,
    -10,  0,  5, 10, 10,  5,  0,-10,
    -10,  5,  5, 10, 10,  5,  5,-10,
    -10,  0, 10, 10, 10, 10,  0,-10,
    -10, 10, 10, 10, 10, 10, 10,-10,
    -10,  5,  0,  0,  0,  0,  5,-10,
    -20,-10,-10,-10,-10,-10,-10,-20,
]
ROOK_TABLE = [
      0,  0,  0,  0,  0,  0,  0,  0,
      5, 10, 10, 10, 10, 10, 10,  5,
     -5,  0,  0,  0,  0,  0,  0, -5,
     -5,  0,  0,  0,  0,  0,  0, -5,
     -5,  0,  0,  0,  0,  0,  0, -5,
     -5,  0,  0,  0,  0,  0,  0, -5,
     -5,  0,  0,  0,  0,  0,  0, -5,
      0,  0,  0,  5,  5,  0,  0,  0,
]
QUEEN_TABLE = [
    -20,-10,-10, -5, -5,-10,-10,-20,
    -10,  0,  0,  0,  0,  0,  0,-10,
    -10,  0,  5,  5,  5,  5,  0,-10,
     -5,  0,  5,  5,  5,  5,  0, -5,
      0,  0,  5,  5,  5,  5,  0, -5,
    -10,  5,  5,  5,  5,  5,  0,-10,
    -10,  0,  5,  0,  0,  0,  0,-10,
    -20,-10,-10, -5, -5,-10,-10,-20,
]
# The king wants to hide in the middlegame but walk to the centre in the endgame.
KING_MIDDLEGAME_TABLE = [
    -30,-40,-40,-50,-50,-40,-40,-30,
    -30,-40,-40,-50,-50,-40,-40,-30,
    -30,-40,-40,-50,-50,-40,-40,-30,
    -30,-40,-40,-50,-50,-40,-40,-30,
    -20,-30,-30,-40,-40,-30,-30,-20,
    -10,-20,-20,-20,-20,-20,-20,-10,
     20, 20,  0,  0,  0,  0, 20, 20,
     20, 30, 10,  0,  0, 10, 30, 20,
]
KING_ENDGAME_TABLE = [
    -50,-40,-30,-20,-20,-30,-40,-50,
    -30,-20,-10,  0,  0,-10,-20,-30,
    -30,-10, 20, 30, 30, 20,-10,-30,
    -30,-10, 30, 40, 40, 30,-10,-30,
    -30,-10, 30, 40, 40, 30,-10,-30,
    -30,-10, 20, 30, 30, 20,-10,-30,
    -30,-30,  0,  0,  0,  0,-30,-30,
    -50,-30,-30,-30,-30,-30,-30,-50,
]

PIECE_SQUARE_TABLES = {
    chess.PAWN: PAWN_TABLE,
    chess.KNIGHT: KNIGHT_TABLE,
    chess.BISHOP: BISHOP_TABLE,
    chess.ROOK: ROOK_TABLE,
    chess.QUEEN: QUEEN_TABLE,
}


def _build_tables():
    """Turn the pictures above into lookups: TABLE[piece_type][square] = value + bonus.

    python-chess numbers squares a1=0 ... h8=63, but the pictures start at rank 8,
    so for White we flip the rank with `square ^ 56`. Black is the mirror image,
    so Black reads the picture directly.
    """
    white, black = {}, {}
    for piece_type, table in PIECE_SQUARE_TABLES.items():
        value = PIECE_VALUE[piece_type]
        white[piece_type] = [value + table[square ^ 56] for square in range(64)]
        black[piece_type] = [value + table[square] for square in range(64)]
    return white, black


WHITE_TABLE, BLACK_TABLE = _build_tables()
KING_MG_WHITE = [KING_MIDDLEGAME_TABLE[square ^ 56] for square in range(64)]
KING_MG_BLACK = KING_MIDDLEGAME_TABLE
KING_EG_WHITE = [KING_ENDGAME_TABLE[square ^ 56] for square in range(64)]
KING_EG_BLACK = KING_ENDGAME_TABLE

# How much each piece counts towards "we are still in the middlegame" (max total = 24).
PHASE_WEIGHT = {chess.PAWN: 0, chess.KNIGHT: 1, chess.BISHOP: 1, chess.ROOK: 2, chess.QUEEN: 4}
BISHOP_PAIR = 30


def evaluate(board):
    """Score the position from the point of view of the side to move.

    Material + piece-square bonuses + bishop pair + a king that changes behaviour
    between middlegame and endgame. It's simple on purpose: add your own ideas here
    (passed pawns, rooks on open files, king safety...) to make the engine yours.
    """
    score = 0   # computed from White's point of view first
    phase = 0   # 24 = full middlegame ... 0 = bare endgame

    for piece_type in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        white_pieces = board.pieces_mask(piece_type, chess.WHITE)
        black_pieces = board.pieces_mask(piece_type, chess.BLACK)
        white_table = WHITE_TABLE[piece_type]
        black_table = BLACK_TABLE[piece_type]
        for square in chess.scan_forward(white_pieces):
            score += white_table[square]
        for square in chess.scan_forward(black_pieces):
            score -= black_table[square]

        white_count = chess.popcount(white_pieces)
        black_count = chess.popcount(black_pieces)
        phase += PHASE_WEIGHT[piece_type] * (white_count + black_count)
        if piece_type == chess.BISHOP:
            if white_count >= 2:
                score += BISHOP_PAIR
            if black_count >= 2:
                score -= BISHOP_PAIR

    # Blend the two king tables depending on how much material is left.
    phase = min(phase, 24)
    white_king = board.king(chess.WHITE)
    black_king = board.king(chess.BLACK)
    score += (KING_MG_WHITE[white_king] * phase + KING_EG_WHITE[white_king] * (24 - phase)) // 24
    score -= (KING_MG_BLACK[black_king] * phase + KING_EG_BLACK[black_king] * (24 - phase)) // 24

    return score if board.turn == chess.WHITE else -score


# =============================================================================
# 2. SEARCH - "which move leads to the best future?"
# =============================================================================
# Negamax: my score for a position = minus my opponent's score for it. So one
# function serves both sides. Alpha-beta pruning skips lines that provably can't
# change the final answer (same result as plain minimax, far less work).

EXACT, LOWER, UPPER = 0, 1, 2   # what a remembered score means (exact / at least / at most)
DELTA_MARGIN = 200              # quiescence: skip captures that can't possibly help


def position_key(board):
    """A number identifying the position (pieces, turn, castling, en passant)."""
    return hash((
        board.occupied_co[chess.WHITE], board.occupied_co[chess.BLACK],
        board.pawns, board.knights, board.bishops, board.rooks, board.queens,
        board.turn, board.castling_rights, board.ep_square,
    ))


def has_non_pawn_material(board, color):
    return bool(board.occupied_co[color] & ~(board.pawns | board.kings))


def format_score(score):
    if abs(score) > MATE_THRESHOLD:
        moves = (MATE - abs(score) + 1) // 2
        return f"mate in {moves}" if score > 0 else f"mated in {moves}"
    return f"{score / 100:+.2f}"


class SearchTimeout(Exception):
    """Raised deep inside the search when the thinking time is used up."""


class Searcher:
    def __init__(self):
        self.lock = threading.Lock()        # one search at a time (Flask may use threads)
        self.tt = {}                        # transposition table: position -> what we learned
        self.killers = []                   # quiet moves that recently caused a cutoff, per ply
        self.history = [0] * (2 * 64 * 64)  # how often a quiet move (from, to) was good
        self.nodes = 0
        self.deadline = 0.0
        self.last_info = {}

    # ------------------------------------------------------------------ public
    def best_move(self, board, seconds=None, max_depth=None):
        """Return the best move for the side to move. `board` is never modified."""
        seconds = SEARCH_TIME if seconds is None else seconds
        max_depth = MAX_DEPTH if max_depth is None else max_depth
        with self.lock:
            move = self._iterative_deepening(board.copy(), seconds, max_depth)
        if VERBOSE and self.last_info:
            info = self.last_info
            print(
                f"[engine] {board.san(move)}  depth {info['depth']}  "
                f"score {format_score(info['score'])}  nodes {info['nodes']:,}  "
                f"{info['seconds']:.2f}s  ({info['nodes'] / max(info['seconds'], 1e-9):,.0f} nodes/s)",
                flush=True,
            )
        return move

    # ------------------------------------------------------- iterative deepening
    def _iterative_deepening(self, board, seconds, max_depth):
        """Search 1 move deep, then 2, then 3... keeping the best move of the last
        COMPLETED depth. Shallow searches are quick and make the deeper ones faster
        (they tell us which moves to try first), and we always have an answer ready
        when the clock runs out."""
        root_moves = list(board.legal_moves)
        if not root_moves:
            raise ValueError("no legal moves: the game is already over")
        self.last_info = {}
        if len(root_moves) == 1:
            return root_moves[0]

        start = time.perf_counter()
        self.deadline = start + seconds
        self.nodes = 0
        self.killers = [[None, None] for _ in range(MAX_PLY + 2)]
        self.history = [h // 2 for h in self.history]   # slowly forget old lessons
        if len(self.tt) > TT_MAX_ENTRIES:
            self.tt.clear()

        root_moves = self.order_moves(board, root_moves, 0, None)
        best_move, best_score, reached = root_moves[0], 0, 0

        for depth in range(1, max_depth + 1):
            try:
                move, score = self._search_root(board, root_moves, depth)
            except SearchTimeout:
                break   # throw away the unfinished depth, keep the previous result
            best_move, best_score, reached = move, score, depth
            root_moves.remove(move)
            root_moves.insert(0, move)     # try the best move first next time
            if abs(score) > MATE_THRESHOLD:
                break                      # forced mate found, no need to look deeper
            if time.perf_counter() - start > seconds * 0.5:
                break                      # the next depth would not finish in time

        self.last_info = {
            "depth": reached, "score": best_score, "nodes": self.nodes,
            "seconds": time.perf_counter() - start,
        }
        return best_move

    def _search_root(self, board, root_moves, depth):
        alpha, beta = -INF, INF
        best_move, best_score = root_moves[0], -INF
        for move in root_moves:
            board.push(move)
            score = -self.negamax(board, depth - 1, -beta, -alpha, 1)
            board.pop()
            if score > best_score:
                best_score, best_move = score, move
                alpha = max(alpha, score)
        return best_move, best_score

    # ----------------------------------------------------------- main search
    def negamax(self, board, depth, alpha, beta, ply, allow_null=True):
        self.nodes += 1
        if (self.nodes & 511) == 0 and time.perf_counter() > self.deadline:
            raise SearchTimeout

        # Draws: fifty-move rule, repeated position, not enough pieces to mate.
        if board.halfmove_clock >= 100:
            return 0
        if board.halfmove_clock >= 4 and board.is_repetition(2):
            return 0
        if chess.popcount(board.occupied) <= 4 and board.is_insufficient_material():
            return 0

        in_check = board.is_check()
        if in_check:
            depth += 1   # check extension: don't stop looking in the middle of a check
        if depth <= 0 or ply >= MAX_PLY:
            return self.quiescence(board, alpha, beta, ply)

        # Transposition table: have we already searched this exact position?
        key = position_key(board)
        alpha_original = alpha
        tt_move = None
        entry = self.tt.get(key)
        if entry is not None:
            tt_depth, tt_flag, tt_score, tt_move = entry
            if tt_depth >= depth:
                if tt_score > MATE_THRESHOLD:       # mate scores are stored relative to
                    tt_score -= ply                 # the position, so convert back
                elif tt_score < -MATE_THRESHOLD:
                    tt_score += ply
                if tt_flag == EXACT:
                    return tt_score
                if tt_flag == LOWER:
                    alpha = max(alpha, tt_score)
                else:
                    beta = min(beta, tt_score)
                if alpha >= beta:
                    return tt_score

        # Null-move pruning: if I could pass and I'm STILL winning big, this line is
        # hopeless for the opponent. (Skipped when in check or with only pawns left,
        # where passing is not a safe assumption.)
        if (USE_NULL_MOVE and allow_null and not in_check and depth >= 3
                and beta < MATE_THRESHOLD and has_non_pawn_material(board, board.turn)
                and evaluate(board) >= beta):
            board.push(chess.Move.null())
            score = -self.negamax(board, depth - 3, -beta, -beta + 1, ply + 1, allow_null=False)
            board.pop()
            if score >= beta:
                return beta

        moves = list(board.legal_moves)
        if not moves:
            return -MATE + ply if in_check else 0   # checkmate (sooner = worse) or stalemate
        moves = self.order_moves(board, moves, ply, tt_move)

        best_score = -INF
        best_move = None
        for index, move in enumerate(moves):
            quiet = not board.is_capture(move) and not move.promotion
            board.push(move)
            if USE_LMR and depth >= 3 and index >= 4 and quiet and not in_check:
                # Late move reduction: moves ordered this late are rarely best, so look
                # at them one ply shallower first...
                score = -self.negamax(board, depth - 2, -alpha - 1, -alpha, ply + 1)
                if score > alpha:
                    # ...and if one surprises us, look again at full depth.
                    score = -self.negamax(board, depth - 1, -beta, -alpha, ply + 1)
            else:
                score = -self.negamax(board, depth - 1, -beta, -alpha, ply + 1)
            board.pop()

            if score > best_score:
                best_score = score
                best_move = move
                if score > alpha:
                    alpha = score
                    if alpha >= beta:            # beta cutoff: the opponent won't allow this
                        if quiet:
                            self.remember_good_quiet_move(board, move, depth, ply)
                        break

        # Remember what we learned about this position.
        if best_score <= alpha_original:
            flag = UPPER      # real score is at most this
        elif best_score >= beta:
            flag = LOWER      # real score is at least this
        else:
            flag = EXACT
        stored = best_score
        if stored > MATE_THRESHOLD:
            stored += ply
        elif stored < -MATE_THRESHOLD:
            stored -= ply
        old = self.tt.get(key)
        if old is None or old[0] <= depth:
            self.tt[key] = (depth, flag, stored, best_move)
        return best_score

    def quiescence(self, board, alpha, beta, ply):
        """At the end of the search, keep playing out captures until things are calm.
        Otherwise the engine might 'win' a queen without noticing it gets recaptured."""
        self.nodes += 1
        if (self.nodes & 511) == 0 and time.perf_counter() > self.deadline:
            raise SearchTimeout
        if ply >= MAX_PLY:
            return evaluate(board)

        stand_pat = None
        if board.is_check():
            moves = list(board.legal_moves)       # in check we must look at every escape
            if not moves:
                return -MATE + ply
            best = -INF
        else:
            stand_pat = evaluate(board)           # "I can just stop capturing here"
            if stand_pat >= beta:
                return stand_pat
            alpha = max(alpha, stand_pat)
            best = stand_pat
            moves = list(board.generate_legal_captures())
        moves = self.order_moves(board, moves, ply, None)

        for move in moves:
            if stand_pat is not None and not move.promotion:
                victim = board.piece_type_at(move.to_square) or chess.PAWN
                if stand_pat + PIECE_VALUE[victim] + DELTA_MARGIN < alpha:
                    continue                      # even winning this piece would not help
            board.push(move)
            score = -self.quiescence(board, -beta, -alpha, ply + 1)
            board.pop()
            if score > best:
                best = score
                if score > alpha:
                    alpha = score
                    if alpha >= beta:
                        break
        return best

    # ------------------------------------------------------------ move ordering
    # Alpha-beta works best when the best move is tried FIRST, so we sort the moves:
    #   1. the move the transposition table remembers
    #   2. captures, most valuable victim first (MVV-LVA: cheap piece takes big piece)
    #   3. queen promotions
    #   4. "killer" moves (quiet moves that caused a cutoff at this depth before)
    #   5. everything else by the history score
    def order_moves(self, board, moves, ply, tt_move):
        killer_1, killer_2 = self.killers[ply]
        history = self.history
        offset = 4096 if board.turn == chess.WHITE else 0

        def score(move):
            if move == tt_move:
                return 1_000_000
            if board.is_capture(move):
                victim = board.piece_type_at(move.to_square) or chess.PAWN   # en passant
                attacker = board.piece_type_at(move.from_square)
                return 100_000 + victim * 10 - attacker
            if move.promotion:
                return 95_000 if move.promotion == chess.QUEEN else 0
            if move == killer_1:
                return 80_000
            if move == killer_2:
                return 70_000
            return min(history[offset + move.from_square * 64 + move.to_square], 60_000)

        return sorted(moves, key=score, reverse=True)

    def remember_good_quiet_move(self, board, move, depth, ply):
        """Called right after board.pop(), so board.turn is the side that played `move`."""
        killers = self.killers[ply]
        if killers[0] != move:
            killers[1] = killers[0]
            killers[0] = move
        offset = 4096 if board.turn == chess.WHITE else 0
        self.history[offset + move.from_square * 64 + move.to_square] += depth * depth


_searcher = Searcher()


# =============================================================================
# The functions app.py calls
# =============================================================================
def choose_moves(board: chess.Board) -> chess.Move:
    """Pick the engine's move. Works on a copy, so `board` is left untouched."""
    return _searcher.best_move(board)


def choose_move(board: chess.Board) -> chess.Move:
    return choose_moves(board)


# =============================================================================
# File helpers used by app.py - leave these as they are
# =============================================================================
def read_positions():
    with POSITIONS_PATH.open("r", encoding="utf-8") as positions_file:
        return json.load(positions_file)


def write_positions(positions):
    with POSITIONS_PATH.open("w", encoding="utf-8") as positions_file:
        json.dump(positions, positions_file, indent=4)
        positions_file.write("\n")


def read_legal_moves():
    with LEGAL_MOVES_PATH.open("r", encoding="utf-8") as legal_moves_file:
        return json.load(legal_moves_file)


def write_legal_moves(legal_moves):
    with LEGAL_MOVES_PATH.open("w", encoding="utf-8") as legal_moves_file:
        json.dump(legal_moves, legal_moves_file, indent=4)
        legal_moves_file.write("\n")