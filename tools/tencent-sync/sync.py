"""Mirror the Tencent Go board on screen into ~/sabaki-live.sgf.

Sabaki: File > Live Sync with ~/sabaki-live.sgf
"""
import argparse
import atexit
import signal
import json
import os
import subprocess
import sys
import tempfile
import time

import cv2

from recognize import SIZE, grid_points, read_position, render

EMPTY, BLACK, WHITE = 0, 1, 2


def find_window(owner):
    """(CGWindowID, bounds in points) of the largest window owned by `owner`."""
    try:
        import Quartz
    except ImportError:
        return None, None
    best, best_area = None, 0
    for w in Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
    ):
        if w.get("kCGWindowOwnerName") != owner or w.get("kCGWindowLayer") != 0:
            continue
        b = w["kCGWindowBounds"]
        area = b["Width"] * b["Height"]
        if area > best_area:
            best, best_area = (w["kCGWindowNumber"], b), area
    return best or (None, None)


def board_visible(geom, owner):
    """True when the whole board is on screen and not covered by other windows
    (checks the four corners and the centre of the grid)."""
    try:
        import Quartz
    except ImportError:
        return True
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    far = (geom["size"] - 1) * geom["spacing"]
    points = [
        (geom["x"] + dx * far, geom["y"] + dy * far)
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1), (0.5, 0.5))
    ]
    for px, py in points:
        for w in windows:  # ordered front to back
            if w.get("kCGWindowLayer") != 0 or w.get("kCGWindowAlpha", 1) == 0:
                continue
            b = w["kCGWindowBounds"]
            if b["X"] <= px < b["X"] + b["Width"] and b["Y"] <= py < b["Y"] + b["Height"]:
                if w.get("kCGWindowOwnerName") != owner:
                    return False
                break
        else:
            return False  # not on any screen
    return True


def capture(path, owner=None):
    """Capture just the app's window (works even when it is covered by other
    windows). Returns (image, window bounds); image is None when the app has
    no visible window. Without `owner` the whole screen is captured."""
    wid, bounds = find_window(owner) if owner else (None, None)
    if owner and not wid:
        return None, None
    cmd = ["screencapture", "-x", "-t", "png"]
    if wid:
        cmd += ["-o", "-l", str(wid)]
    subprocess.run(cmd + [path], check=True)
    return cv2.imread(path), bounds


def screen_geometry(board, img, bounds):
    """Grid origin and spacing in screen points (for drawing overlays)."""
    pts, spacing = grid_points(board)
    if bounds:
        scale = img.shape[1] / bounds["Width"]
        ox, oy = bounds["X"], bounds["Y"]
    else:
        scale, ox, oy = 2.0, 0, 0  # assume a retina full-screen capture
    return {
        "x": ox + pts[0][0][0] / scale,
        "y": oy + pts[0][0][1] / scale,
        "spacing": spacing / scale,
        "size": SIZE,
    }


def coord(r, c):
    return "abcdefghijklmnopqrs"[c] + "abcdefghijklmnopqrs"[r]


def diff(old, new):
    added, removed = [], []
    for r in range(SIZE):
        for c in range(SIZE):
            a, b = old[r][c], new[r][c]
            if a == b:
                continue
            if a == EMPTY:
                added.append((r, c, b))
            elif b == EMPTY:
                removed.append((r, c, a))
            else:
                return None  # a stone changed colour: not a legal single step
    return added, removed


class Tracker:
    """Keeps a base position plus a list of moves, so Sabaki shows a game
    tree that can be stepped through. Falls back to a plain position when the
    screen jumps somewhere we cannot explain with a single move."""

    def __init__(self):
        self.base = None
        self.moves = []  # (color, r, c)
        self.positions = []  # positions[i] = position after i moves
        self.sgf = None

    def update(self, grid):
        if self.base is None:
            return self._reset(grid)
        cur = self.positions[-1]
        if grid == cur:
            return False
        # stepped back (or jumped) to an earlier position of this game
        for i, p in enumerate(self.positions):
            if p == grid:
                del self.moves[i:]
                del self.positions[i + 1:]
                return True
        d = diff(cur, grid)
        if d is not None:
            added, removed = d
            if len(added) == 1 and all(
                s != added[0][2] for _, _, s in removed
            ):
                r, c, color = added[0]
                self.moves.append((color, r, c))
                self.positions.append(grid)
                return True
        return self._reset(grid)

    def _reset(self, grid):
        self.base = grid
        self.moves = []
        self.positions = [grid]
        return True

    def to_sgf(self):
        out = [f"(;GM[1]FF[4]SZ[{SIZE}]KM[7.5]RU[Chinese]CA[UTF-8]AP[tencent-sync]"]
        for color, key in ((BLACK, "AB"), (WHITE, "AW")):
            pts = [
                f"[{coord(r, c)}]"
                for r in range(SIZE)
                for c in range(SIZE)
                if self.base[r][c] == color
            ]
            if pts:
                out.append(key + "".join(pts))
        for color, r, c in self.moves:
            out.append(f";{'B' if color == BLACK else 'W'}[{coord(r, c)}]")
        out.append(")")
        return "".join(out)


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/sabaki-live.sgf"))
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--app", default="腾讯围棋", help="app name to capture; empty = whole screen")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    shot = os.path.join(tempfile.gettempdir(), "tencent-sync.png")
    geom_path = os.path.splitext(args.out)[0] + "-board.json"

    def drop_geometry():
        try:
            os.remove(geom_path)
        except FileNotFoundError:
            pass

    atexit.register(drop_geometry)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    tracker = Tracker()
    pending, seen_board, last_geom = None, None, None
    print("Watching the screen; Ctrl+C to stop.")
    while True:
        try:
            if os.getppid() == 1:
                return  # the app that started us is gone
            img, bounds = capture(shot, args.app)
            grid, board = (None, None) if img is None else read_position(img)
            if grid is None:
                if seen_board is not None:
                    print("Board not visible.")
                seen_board = None
                drop_geometry()
                last_geom = None
            else:
                seen_board = board
                geom = screen_geometry(board, img, bounds)
                if args.app and not board_visible(geom, args.app):
                    # covered by another window or partly off screen
                    drop_geometry()
                    last_geom = None
                elif geom != last_geom:
                    write_atomic(geom_path, json.dumps(geom))
                    last_geom = geom
                # accept a position only once it was seen in two frames in a row
                # (filters out stone placement animations)
                if grid == pending:
                    if tracker.update(grid):
                        write_atomic(args.out, tracker.to_sgf())
                        print(f"moves={len(tracker.moves)} stones="
                              f"{sum(v != 0 for row in grid for v in row)}")
                pending = grid
            if args.once:
                if grid:
                    print(render(grid))
                return
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
