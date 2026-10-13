"""Recognize a 19x19 Go position from a screenshot of the Tencent Go client."""
import cv2
import numpy as np

SIZE = 19
MARGIN = 0.65  # wood margin around the outer grid line, in grid spacings


def find_board(img):
    """Return (x, y, side) of the wooden board, or None."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (8, 60, 150), (30, 200, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < 400 or h < 400 or not 0.9 < w / h < 1.1:
            continue
        if area < 0.15 * w * h:
            continue
        if best is None or w * h > best[2] * best[3]:
            best = (x, y, w, h)
    if best is None:
        return None
    x, y, w, h = best
    H, W = img.shape[:2]
    if x <= 1 or y <= 1 or x + w >= W - 1 or y + h >= H - 1:
        return None  # board is cut off by the edge of the capture
    board = (x, y, (w + h) / 2)
    return board if has_grid(img, board) else None


def has_grid(img, board):
    """Check that the grid lines are visibly darker than the wood between."""
    pts, s = grid_points(board)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(float)
    H, W = gray.shape
    x0, y0 = pts[0][0]
    x1, y1 = pts[-1][-1]
    if x1 + s >= W or y1 + s >= H or x0 < 0 or y0 < 0:
        return False
    ratios = []
    for k in range(SIZE - 1):
        line_y = int(round(y0 + k * s))
        mid_y = int(round(y0 + (k + 0.5) * s))
        line_x = int(round(x0 + k * s))
        mid_x = int(round(x0 + (k + 0.5) * s))
        for on, off in (
            (gray[line_y - 1:line_y + 2, int(x0):int(x1)],
             gray[mid_y - 1:mid_y + 2, int(x0):int(x1)]),
            (gray[int(y0):int(y1), line_x - 1:line_x + 2],
             gray[int(y0):int(y1), mid_x - 1:mid_x + 2]),
        ):
            ratios.append(on.mean() / max(off.mean(), 1))
    return float(np.median(ratios)) < 0.93


def grid_points(board):
    x, y, side = board
    s = side / (SIZE - 1 + 2 * MARGIN)
    ox, oy = x + MARGIN * s, y + MARGIN * s
    return [[(ox + c * s, oy + r * s) for c in range(SIZE)] for r in range(SIZE)], s


def read_position(img, board=None):
    """Return (grid, board) where grid[r][c] is 0 empty, 1 black, 2 white."""
    board = board or find_board(img)
    if board is None:
        return None, None
    pts, s = grid_points(board)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    rad = max(2, int(s * 0.28))
    disc = np.zeros((2 * rad + 1, 2 * rad + 1), bool)
    yy, xx = np.ogrid[-rad:rad + 1, -rad:rad + 1]
    disc[xx * xx + yy * yy <= rad * rad] = True
    H, W = img.shape[:2]
    grid = [[0] * SIZE for _ in range(SIZE)]
    for r in range(SIZE):
        for c in range(SIZE):
            px, py = int(round(pts[r][c][0])), int(round(pts[r][c][1]))
            if px - rad < 0 or py - rad < 0 or px + rad >= W or py + rad >= H:
                continue
            patch = hsv[py - rad:py + rad + 1, px - rad:px + rad + 1][disc]
            v = np.median(patch[:, 2])
            sat = np.median(patch[:, 1])
            if v < 90:
                grid[r][c] = 1
            elif v > 180 and sat < 50:
                grid[r][c] = 2
    return grid, board


def render(grid):
    return "\n".join("".join(".XO"[v] for v in row) for row in grid)


if __name__ == "__main__":
    import sys
    img = cv2.imread(sys.argv[1])
    grid, board = read_position(img)
    print(board)
    print(render(grid))
    if len(sys.argv) > 2:
        pts, s = grid_points(board)
        for r in range(SIZE):
            for c in range(SIZE):
                col = [(0, 0, 255), (0, 255, 0), (255, 0, 0)][grid[r][c]]
                cv2.circle(img, tuple(int(v) for v in pts[r][c]), 5, col, -1)
        cv2.imwrite(sys.argv[2], img)
