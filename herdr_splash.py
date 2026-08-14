"""Title cards for the wait while herdr-agents starts an agent.

Starting an agent takes about a second, and it used to be one dim line of text
in a popup taking up 90% of the window. Now it is a title card, and `launch()`
rotates to the next one every time, so the thing you see most days is not the
thing you saw yesterday.

How long the card runs is `launch()`'s call, not this module's: it passes a
`running` predicate and the loop below holds the terminal until that goes false.
Do not assume it tracks `herdr agent start` -- that call has a fixed ~3s
readiness wait that outlasts the agent, so `launch()` races it against the
agent's own lifecycle report and ends the card on whichever comes first.

Four cards, in rotation order: the rotating torus, hyperspace, digital rain,
and the agent's own name in lights. They share three ideas:

  * A card is built for one terminal size and then only asked for frames, so
    per-frame work is arithmetic over a prepared structure. Resizing rebuilds.
  * Half of them draw pixels and half draw characters. `pixel_lines()` folds a
    pixel buffer two rows to a cell through the half block, which doubles the
    vertical resolution and is the only reason a curve or a slanted edge does
    not read as a staircase; `char_lines()` does the same job for cards that
    are made of glyphs.
  * `flash` runs 0 -> 1 once the agent answers, and every card blends its whole
    palette toward white by it. That is the entire outro, and it lasts 220ms.

Nothing here may cost anyone their agent, so `play()` catches everything and the
caller falls back to printing a line. Import it as a module (herdr-agents does)
or run it directly to watch:

  python3 herdr_splash.py                 every card, three seconds each
  python3 herdr_splash.py donut 6         one card, six seconds
"""

from __future__ import annotations

import math
import os
import random
import shutil
import sys
import time

RESET = "\033[0m"
DIM = "\033[90m"

FPS = 24
FLASH_MS = 220        # the agent answered: go bright, then get out of the way
PATIENCE_S = 6        # past this, the caption admits how long it has been
MIN_COLS = 34
MIN_ROWS = 16
CAPTION_ROWS = 3      # a blank row, the caption, and a row of air beneath it

_SEQ_CACHE: dict = {}


def color_seq(rgb, layer: int = 38) -> str:
    """An SGR sequence for `rgb` -- truecolor where the terminal claims it.

    COLORTERM is the only portable signal, and a terminal that does not set it
    gets the 6x6x6 cube instead. Every card here is a few flat hues and a
    highlight, so they survive the quantisation with nothing worse than a duller
    red or a flatter green.
    """
    key = (rgb, layer)
    seq = _SEQ_CACHE.get(key)
    if seq is None:
        red, green, blue = (max(0, min(255, int(c))) for c in rgb)
        if os.environ.get("COLORTERM") in ("truecolor", "24bit"):
            seq = f"\033[{layer};2;{red};{green};{blue}m"
        else:
            cube = [round(c / 255 * 5) for c in (red, green, blue)]
            seq = f"\033[{layer};5;{16 + 36 * cube[0] + 6 * cube[1] + cube[2]}m"
        _SEQ_CACHE[key] = seq
    return seq


def mix(one, two, amount: float):
    """`one` moved `amount` of the way toward `two`."""
    return tuple(a + (b - a) * amount for a, b in zip(one, two))


def ladder(dark, light, steps: int, flash: float = 0.0) -> list:
    """`steps` colours from `dark` to `light`, whitened by `flash`.

    Cards quantise to a ladder rather than computing a colour per cell, which is
    not about the arithmetic: `char_lines` emits an SGR only when the colour
    changes, so a continuous gradient means an escape sequence per cell. On a
    240x62 terminal that was 140KB a frame for a starfield, and neighbours
    sharing a rung cuts most of it.
    """
    out = []
    for i in range(steps):
        rgb = mix(dark, light, i / max(1, steps - 1))
        out.append(tuple(int(c) for c in mix(rgb, (255, 255, 255), flash)))
    return out


def pixel_lines(pixels: list) -> list:
    """Pixel rows folded two to a terminal cell, through the half block.

    Only the top pixel lit is the upper half block, only the bottom is the lower,
    both and equal is a full block, both and different is the upper half with the
    lower colour behind it. Nothing paints a background it does not need, so a
    card sits on the popup's own colour rather than on a black rectangle.
    """
    lines = []
    for y in range(0, len(pixels) - 1, 2):
        upper, lower = pixels[y], pixels[y + 1]
        out, fg_now, bg_now = [], None, None
        for top, bottom in zip(upper, lower):
            if top is None and bottom is None:
                char, want_fg, want_bg = " ", fg_now, None
            elif bottom is None:
                char, want_fg, want_bg = "\u2580", top, None
            elif top is None:
                char, want_fg, want_bg = "\u2584", bottom, None
            elif top == bottom:
                char, want_fg, want_bg = "\u2588", top, None
            else:
                char, want_fg, want_bg = "\u2580", top, bottom
            if want_bg != bg_now:
                out.append(color_seq(want_bg, 48) if want_bg else "\033[49m")
                bg_now = want_bg
            if want_fg != fg_now and want_fg is not None:
                out.append(color_seq(want_fg))
                fg_now = want_fg
            out.append(char)
        lines.append("".join(out).rstrip() + RESET)
    return lines


def char_lines(grid: list) -> list:
    """Rows of (character, colour) or None, with one SGR per colour change."""
    lines = []
    for row in grid:
        out, fg_now = [], None
        for cell in row:
            if cell is None:
                out.append(" ")
                continue
            char, rgb = cell
            if rgb != fg_now:
                out.append(color_seq(rgb))
                fg_now = rgb
            out.append(char)
        lines.append("".join(out).rstrip() + RESET)
    return lines


class Card:
    """One title card, built for a terminal size and then asked for frames.

    Subclasses set `width` (the display width of what they return, so the player
    can centre it) and implement `frame`. `rows` is what the card may use, the
    caption having already been subtracted.
    """

    name = "?"
    accent = (255, 255, 255)

    def __init__(self, kind: str, cols: int, rows: int):
        self.kind, self.cols, self.rows = kind, cols, rows
        self.width = cols
        self.build()

    def build(self) -> None:
        """Everything that depends on size but not on time."""

    def frame(self, elapsed: float, flash: float) -> list:
        raise NotImplementedError


# ------------------------------------------------------------- the torus

class Donut(Card):
    """The rotating torus, because a terminal that can draw one should.

    Straight out of donut.c: a point per (theta, phi) on the surface, rotated
    through two axes, perspective-divided, and z-buffered per cell, with the
    surface normal's dot product against the light picking a glyph off the ramp.
    The two circles never change, so their sines and cosines are tabulated at
    build time and a frame is arithmetic. The steps are coarser than the original
    because this has 40ms to spend, not a whole terminal's attention.
    """

    name = "donut"
    accent = (255, 196, 120)

    RAMP = ".,-~:;=!*#$@"
    DIM_LIGHT = (78, 34, 12)
    FULL_LIGHT = (255, 240, 208)
    R1, R2, K2 = 1.0, 2.0, 5.0

    def build(self) -> None:
        self.high = max(8, min(self.rows, 24))
        self.wide = max(16, min(self.cols, self.high * 2))
        self.width = self.wide
        self.k1 = self.wide * self.K2 * 3 / (8 * (self.R1 + self.R2))
        self.circle = [(math.cos(t), math.sin(t))
                       for t in [i * 0.14 for i in range(int(math.tau / 0.14))]]
        self.sweep = [(math.cos(p), math.sin(p))
                      for p in [i * 0.055 for i in range(int(math.tau / 0.055))]]
        self.ladder = [mix(self.DIM_LIGHT, self.FULL_LIGHT, i / (len(self.RAMP) - 1))
                       for i in range(len(self.RAMP))]

    def frame(self, elapsed: float, flash: float) -> list:
        spin, tilt = 1.1 + elapsed * 1.15, 0.4 + elapsed * 0.62
        cos_a, sin_a = math.cos(spin), math.sin(spin)
        cos_b, sin_b = math.cos(tilt), math.sin(tilt)
        wide, high, k1 = self.wide, self.high, self.k1
        ladder = [tuple(int(c) for c in mix(rgb, (255, 255, 255), flash))
                  for rgb in self.ladder]
        grid = [[None] * wide for _ in range(high)]
        depth = [[0.0] * wide for _ in range(high)]
        for cos_t, sin_t in self.circle:
            ring, lift = self.R2 + self.R1 * cos_t, self.R1 * sin_t
            for cos_p, sin_p in self.sweep:
                x = ring * (cos_b * cos_p + sin_a * sin_b * sin_p) - lift * cos_a * sin_b
                y = ring * (sin_b * cos_p - sin_a * cos_b * sin_p) + lift * cos_a * cos_b
                over = 1 / (self.K2 + cos_a * ring * sin_p + lift * sin_a)
                col = int(wide / 2 + k1 * over * x)
                row = int(high / 2 - k1 * over * y / 2)
                if not (0 <= col < wide and 0 <= row < high) or over <= depth[row][col]:
                    continue
                light = (cos_p * cos_t * sin_b - cos_a * cos_t * sin_p - sin_a * sin_t
                         + cos_b * (cos_a * sin_t - cos_t * sin_a * sin_p))
                if light <= 0:
                    continue
                depth[row][col] = over
                step = min(len(self.RAMP) - 1, int(light * len(self.RAMP) / 1.42))
                grid[row][col] = (self.RAMP[step], ladder[step])
        return char_lines(grid)


# ---------------------------------------------------------- hyperspace

class Warp(Card):
    """Stars streaming past, faster the longer the agent takes.

    Tying the speed to the wait is the point: at three seconds it is a title
    card, and at twenty it is visibly straining, which is information. Each star
    is a fixed direction and a phase; a frame projects it at the current depth
    and again slightly behind, and draws the segment between as its trail.
    """

    name = "warp"
    accent = (150, 200, 255)

    RAMP = ".\u00b7:+*#@"
    FAR = (48, 64, 108)
    NEAR = (255, 255, 255)
    TRAIL_STEPS = 12
    RUNGS = 14

    def build(self) -> None:
        self.high, self.wide = max(6, self.rows), self.cols
        self.width = self.wide
        # A direction on the disc rather than in the square, so the field is even
        # once it is projected, and seeded, so the same launch renders the same
        # sky twice -- which is what makes a test of this possible at all.
        rng = random.Random(0x4E464C58)
        self.stars = []
        # Density follows the area up to a cap. Past it the sky is not visibly
        # busier, but the frame is: every lit cell carries its own colour, and a
        # full-screen 4K terminal was spending 2MB a second on stars nobody can
        # pick out.
        for _ in range(max(90, min(700, self.wide * self.high // 9))):
            angle, radius = rng.uniform(0, math.tau), math.sqrt(rng.random())
            self.stars.append((math.cos(angle) * radius, math.sin(angle) * radius,
                               rng.random()))

    def frame(self, elapsed: float, flash: float) -> list:
        travel = elapsed * (0.30 + 0.05 * elapsed)
        wide, high = self.wide, self.high
        mid_x, mid_y = wide / 2, high / 2
        # A short focal length keeps a star on screen for most of its run in;
        # a long one throws it past the edge while it is still a faint dot.
        focal_x, focal_y = wide * 0.16, high * 0.16
        bright = [[0.0] * wide for _ in range(high)]
        for x, y, phase in self.stars:
            depth = 1.0 - ((phase + travel) % 1.0)
            if depth < 0.06:
                continue
            head_x, head_y = mid_x + x / depth * focal_x, mid_y + y / depth * focal_y
            behind = depth + 0.12
            tail_x, tail_y = mid_x + x / behind * focal_x, mid_y + y / behind * focal_y
            steps = min(self.TRAIL_STEPS,
                        int(max(abs(head_x - tail_x), abs(head_y - tail_y))))
            for i in range(steps + 1):
                along = i / max(1, steps)
                col = round(tail_x + (head_x - tail_x) * along)
                row = round(tail_y + (head_y - tail_y) * along)
                if 0 <= col < wide and 0 <= row < high:
                    lit = (1 - depth) ** 1.1 * (0.28 + 0.72 * along)
                    if lit > bright[row][col]:
                        bright[row][col] = lit
        rungs = ladder(self.FAR, self.NEAR, self.RUNGS, flash)
        chars = [self.RAMP[min(len(self.RAMP) - 1, i * len(self.RAMP) // self.RUNGS)]
                 for i in range(self.RUNGS)]
        grid = []
        for row in bright:
            line = []
            for lit in row:
                if lit < 0.06:
                    line.append(None)
                    continue
                rung = min(self.RUNGS - 1, int(lit * self.RUNGS))
                line.append((chars[rung], rungs[rung]))
            grid.append(line)
        return char_lines(grid)


# --------------------------------------------------------- digital rain

class Matrix(Card):
    """Rain. Every column is a phase and a speed, and a frame is one modulo.

    The glyphs are halfwidth katakana on purpose: they are one cell wide, so the
    column arithmetic and the width the player centres on stay honest, which the
    fullwidth forms would quietly break.
    """

    name = "matrix"
    accent = (120, 255, 150)

    GLYPHS = ("\uff71\uff72\uff73\uff74\uff75\uff76\uff77\uff78\uff79\uff7a\uff7b"
              "\uff7c\uff7d\uff7e\uff7f\uff80\uff81\uff82\uff83\uff84\uff85\uff86"
              "\uff87\uff88\uff89\uff8a\uff8b\uff8c\uff8d\uff8e\uff8f\uff90\uff91"
              "\uff92\uff93\uff94\uff95\uff96\uff97\uff98\uff99\uff9a\uff9b\uff9d"
              "0123456789:=+*<>|")
    HEAD = (208, 255, 216)
    BODY = (0, 226, 92)
    DEEP = (6, 26, 13)
    RUNGS = 12

    def build(self) -> None:
        self.high, self.wide = max(6, self.rows), self.cols
        self.width = self.wide
        rng = random.Random(0x4D5452)
        self.streams = []
        for _ in range(self.wide):
            # A seventh of the columns stay dark. Rain with a stream in every
            # column is a curtain; the gaps are what make it rain.
            self.streams.append(None if rng.random() < 0.14 else
                                (rng.uniform(-self.high, 0.0),        # where it starts
                                 rng.uniform(5.0, 17.0),              # rows per second
                                 rng.randint(4, max(6, self.high // 2 + 4)),  # tail
                                 rng.randrange(97)))                  # glyph phase

    def frame(self, elapsed: float, flash: float) -> list:
        wide, high, glyphs = self.wide, self.high, self.GLYPHS
        span = high + max((s[2] for s in self.streams if s), default=4) + 4
        flicker = int(elapsed * 7)
        rungs = ladder(self.DEEP, self.BODY, self.RUNGS, flash)
        head_rgb = tuple(int(c) for c in mix(self.HEAD, (255, 255, 255), flash))
        grid = [[None] * wide for _ in range(high)]
        for col, stream in enumerate(self.streams):
            if stream is None:
                continue
            start, speed, tail, seed = stream
            head = (start + elapsed * speed) % span
            for step in range(tail):
                row = int(head) - step
                if not 0 <= row < high:
                    continue
                if step == 0:
                    rgb = head_rgb
                else:
                    fade = 1 - step / tail
                    rgb = rungs[min(self.RUNGS - 1, int(fade * fade * self.RUNGS))]
                # A cell's glyph is a hash of where it is, so a stream falling
                # past the same rows shows the same characters -- and a seventh
                # of them churn, which is the flicker without the seizure.
                churn = flicker if step and (row + col) % 7 == 0 else 0
                mixed = (seed * 2654435761 + row * 40503 + col * 12289 + churn * 97)
                grid[row][col] = (glyphs[mixed % len(glyphs)], rgb)
        return char_lines(grid)


# ------------------------------------------------------------- the name

# 5x7 uppercase, one glyph per line so it stays legible as source. Only letters:
# every agent kind herdr knows is letters, and the fallback is a solid block.
FONT = {
    "A": ".###./#...#/#...#/#####/#...#/#...#/#...#",
    "B": "####./#...#/#...#/####./#...#/#...#/####.",
    "C": ".###./#...#/#..../#..../#..../#...#/.###.",
    "D": "####./#...#/#...#/#...#/#...#/#...#/####.",
    "E": "#####/#..../#..../####./#..../#..../#####",
    "F": "#####/#..../#..../####./#..../#..../#....",
    "G": ".###./#...#/#..../#.###/#...#/#...#/.####",
    "H": "#...#/#...#/#...#/#####/#...#/#...#/#...#",
    "I": ".###./..#../..#../..#../..#../..#../.###.",
    "J": "..###/...#./...#./...#./...#./#..#./.##..",
    "K": "#...#/#..#./#.#../##.../#.#../#..#./#...#",
    "L": "#..../#..../#..../#..../#..../#..../#####",
    "M": "#...#/##.##/#.#.#/#.#.#/#...#/#...#/#...#",
    "N": "#...#/##..#/##..#/#.#.#/#..##/#..##/#...#",
    "O": ".###./#...#/#...#/#...#/#...#/#...#/.###.",
    "P": "####./#...#/#...#/####./#..../#..../#....",
    "Q": ".###./#...#/#...#/#...#/#.#.#/#..#./.##.#",
    "R": "####./#...#/#...#/####./#.#../#..#./#...#",
    "S": ".####/#..../#..../.###./....#/....#/####.",
    "T": "#####/..#../..#../..#../..#../..#../..#..",
    "U": "#...#/#...#/#...#/#...#/#...#/#...#/.###.",
    "V": "#...#/#...#/#...#/#...#/#...#/.#.#./..#..",
    "W": "#...#/#...#/#...#/#.#.#/#.#.#/##.##/#...#",
    "X": "#...#/#...#/.#.#./..#../.#.#./#...#/#...#",
    "Y": "#...#/#...#/.#.#./..#../..#../..#../..#..",
    "Z": "#####/....#/...#./..#../.#.../#..../#####",
}
GLYPH_W, GLYPH_H = 5, 7

# One accent per agent, so the card says which one is coming up before the
# caption does. Anything herdr supports that is not listed gets the default.
KIND_COLOR = {
    "pi": (150, 130, 255),
    "claude": (217, 119, 87),
    "codex": (225, 228, 235),
    "gemini": (110, 170, 255),
    "cursor": (190, 190, 200),
    "copilot": (120, 220, 200),
    "grok": (235, 235, 235),
    "droid": (140, 220, 130),
    "amp": (255, 190, 90),
}


class Wordmark(Card):
    """The agent's name in lights, with a shine crossing it.

    A 5x7 bitmap font blown up to whatever the popup can hold, drawn as pixels so
    the half block gives it twice the vertical resolution -- the difference
    between letters and a row of bricks. The shine is a diagonal band, so it
    reads as light travelling across a surface rather than a wipe.
    """

    name = "wordmark"

    SHINE_MS = 1900
    BASE_DARK = 0.45      # how far down the unlit parts of a stroke sit
    MAX_CELLS = 20        # a two-letter word should not become the whole popup

    def build(self) -> None:
        self.accent = KIND_COLOR.get(self.kind, (235, 235, 240))
        letters = [c.upper() for c in self.kind] or ["?"]
        # One column of air between letters, and the biggest whole scale that
        # fits both ways -- a fractional one would ripple the stroke widths.
        # `mastracode` is ten letters wide before any scaling, so a narrow popup
        # cannot have the word at a size worth reading. It gets the initial
        # instead: a cut-off word looks like a bug, one big letter looks meant.
        if self.fit(len(letters)) < 2:
            letters = letters[:1]
        self.scale = max(1, self.fit(len(letters)))
        self.glyphs = [FONT[c].split("/") if c in FONT else ["#####"] * GLYPH_H
                       for c in letters]
        cells = len(self.glyphs)
        native_w = cells * GLYPH_W + (cells - 1)
        scale = self.scale
        self.width = native_w * scale
        self.high = GLYPH_H * scale
        if self.high % 2:                    # pixel rows must pair into cells
            self.high += 1
        self.on = self.build_pixels()

    def fit(self, cells: int) -> int:
        """The largest whole scale at which `cells` letters fit the terminal."""
        return min((self.cols - 4) // (cells * GLYPH_W + cells - 1),
                   (min(self.rows, self.MAX_CELLS) * 2 - 2) // GLYPH_H)

    def build_pixels(self) -> list:
        """Which pixels are ink, once."""
        on = [[False] * self.width for _ in range(self.high)]
        for index, glyph in enumerate(self.glyphs):
            left = index * (GLYPH_W + 1) * self.scale
            for gy, row in enumerate(glyph):
                for gx, cell in enumerate(row):
                    if cell != "#":
                        continue
                    for dy in range(self.scale):
                        for dx in range(self.scale):
                            on[gy * self.scale + dy][left + gx * self.scale + dx] = True
        return on

    def frame(self, elapsed: float, flash: float) -> list:
        # -1 to 2 so the band starts and ends off the edge of the word.
        phase = (elapsed * 1000 % self.SHINE_MS) / self.SHINE_MS
        band = -1.0 + 3.0 * phase
        width, high = self.width, self.high
        pixels = []
        for y in range(high):
            row = []
            for x in range(width):
                if not self.on[y][x]:
                    row.append(None)
                    continue
                across = x / max(1, width - 1) - 0.35 * (y / max(1, high - 1))
                glow = math.exp(-(((across - band) / 0.14) ** 2))
                rgb = mix(mix(self.accent, (12, 12, 16), self.BASE_DARK),
                          (255, 255, 255), max(glow, flash))
                row.append(tuple(int(c) for c in rgb))
            pixels.append(row)
        return pixel_lines(pixels)


CARDS = (Donut, Warp, Matrix, Wordmark)
CARD_NAMES = tuple(card.name for card in CARDS)
BY_NAME = {card.name: card for card in CARDS}


# ------------------------------------------------------------ the player

def wanted() -> bool:
    """Whether to animate rather than print one line.

    HERDR_AGENTS_SPLASH=0 turns it off; anything that is not an interactive
    terminal, and anything too small to hold a card and its caption, bows out.
    """
    if os.environ.get("HERDR_AGENTS_SPLASH") == "0":
        return False
    if not sys.stdout.isatty() or os.environ.get("TERM", "") in ("", "dumb"):
        return False
    cols, rows = shutil.get_terminal_size((80, 24))
    return cols >= MIN_COLS and rows >= MIN_ROWS


def caption(kind: str, label: str, elapsed: float, width: int, accent) -> tuple:
    """The line under the card, as (plain text, styled text), fitted to `width`.

    The dots are padded to full width so the centred line does not shuffle
    sideways as they cycle. Past PATIENCE_S it starts counting out loud: three
    seconds is a title card, twelve is something going wrong, and the difference
    should not need a guess. The folder is what gets cut when the line does not
    fit, because a wrapped caption would shove the whole frame down.
    """
    dots = ("." * (1 + int(elapsed / 0.35) % 3)).ljust(3)
    waited = f"  {int(elapsed)}s" if elapsed >= PATIENCE_S else ""
    room = width - len(f"starting {kind} in {dots}{waited}")
    if len(label) > room:
        label = label[:max(0, room - 1)] + "\u2026"
    plain = f"starting {kind} in {label}{dots}{waited}"
    styled = (f"{DIM}starting {RESET}{color_seq(accent)}{kind}{RESET}"
              f"{DIM} in {RESET}{label}{DIM}{dots}{waited}{RESET}")
    return plain, styled


def screen(card: Card, term: tuple, text: tuple) -> str:
    """One full terminal: the card centred, a blank row, then the caption."""
    cols, rows = term
    body = [" " * max(0, (cols - card.width) // 2) + line for line in card.lines]
    plain, styled = text
    body += ["", " " * max(0, (cols - len(plain)) // 2) + styled]
    top = max(0, (rows - len(body)) // 2)
    lines = ([""] * top + body)[:rows]
    return "\033[H" + "".join(f"{line}\033[K\r\n" for line in lines) + "\033[J"


def play(kind: str, label: str, running, limit: float = 0.0, name: str = "") -> None:
    """Hold the terminal with a title card until `running()` goes false.

    Runs on the alternate screen, so the popup's scrollback is not left holding a
    few hundred frames of block characters. It never delays a launch: the flash
    is the only thing that happens after the agent is up, and it is two frames. A
    rendering bug must not cost anyone their agent, so the whole loop is caught
    and the terminal restored either way.
    """
    chosen = BY_NAME.get(name, Donut)
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()
    try:
        begin = time.monotonic()
        term, card, done_at = None, None, None
        while True:
            now = time.monotonic()
            elapsed = now - begin
            size = shutil.get_terminal_size((80, 24))
            if size != term:
                term = size
                card = chosen(kind, size[0], max(1, size[1] - CAPTION_ROWS))
            flash = 0.0
            if not running() or (limit and elapsed >= limit):
                done_at = now if done_at is None else done_at
                step = (now - done_at) * 1000 / FLASH_MS
                if step >= 1:
                    break
                flash = min(1.0, step * 1.6)
            card.lines = card.frame(elapsed, flash)
            sys.stdout.write(screen(card, term, caption(kind, label, elapsed,
                                                        term[0], card.accent)))
            sys.stdout.flush()
            time.sleep(max(0.0, 1 / FPS - (time.monotonic() - now)))
    except Exception:
        pass
    finally:
        sys.stdout.write(RESET + "\033[?25h\033[?1049l")
        sys.stdout.flush()


def main(argv: list) -> int:
    """Watch the cards. A number is seconds, anything else is a card name."""
    seconds = next((float(a) for a in argv if a.replace(".", "", 1).isdigit()), 3.0)
    names = [a for a in argv if a in BY_NAME] or list(CARD_NAMES)
    kind = next((a for a in argv if a.startswith("kind=")), "kind=pi")[5:]
    for name in names:
        play(kind, os.path.basename(os.getcwd()) or "/", lambda: True,
             limit=seconds, name=name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
