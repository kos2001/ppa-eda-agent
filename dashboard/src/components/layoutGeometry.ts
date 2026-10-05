import type { LayoutSummary } from "../api/referenceDb";

export interface CoverageTile { x: number; y: number; w: number; h: number; coverage: number }

// Exact clipped cell-footprint area per grid tile. This is geometric
// coverage of recorded instances, not routing congestion or a DRC result.
export function cellCoverage(layout: LayoutSummary, bins = 16): CoverageTile[] {
  const die = layout.die;
  if (!die || !die.every(Number.isFinite) || !Number.isInteger(bins) || bins < 1 || bins > 64) return [];
  const [x0, y0, x1, y1] = die;
  if (x1 <= x0 || y1 <= y0) return [];
  const w = (x1 - x0) / bins, h = (y1 - y0) / bins;
  const tiles = Array.from({ length: bins * bins }, (_, i) => ({ x: x0 + (i % bins) * w,
    y: y0 + Math.floor(i / bins) * h, w, h, coverage: 0 }));
  for (const cell of layout.cells) {
    if (![cell.x, cell.y, cell.w, cell.h].every(Number.isFinite) || cell.w <= 0 || cell.h <= 0) continue;
    const left = Math.max(0, Math.floor((cell.x - x0) / w));
    const right = Math.min(bins - 1, Math.floor((cell.x + cell.w - x0) / w));
    const bottom = Math.max(0, Math.floor((cell.y - y0) / h));
    const top = Math.min(bins - 1, Math.floor((cell.y + cell.h - y0) / h));
    for (let row = bottom; row <= top; row++) for (let col = left; col <= right; col++) {
      const tile = tiles[row * bins + col];
      const width = Math.max(0, Math.min(tile.x + w, cell.x + cell.w) - Math.max(tile.x, cell.x));
      const height = Math.max(0, Math.min(tile.y + h, cell.y + cell.h) - Math.max(tile.y, cell.y));
      tile.coverage += width * height / (w * h);
    }
  }
  return tiles;
}
