const {
  CANVAS_WIDTH_PX,
  CANVAS_HEIGHT_PX,
  SAFE_MARGIN_PX,
  LAYOUT,
} = require('../config/constants');

/**
 * Rough heuristic for how wide a line of text will render at a given
 * font size, assuming an average glyph-width ratio for bold display
 * fonts (~0.58 of font size per character). This is intentionally
 * conservative — better to slightly under-size than clip on export.
 */
function estimateTextWidth(text, fontSizePx, avgCharWidthRatio = 0.58) {
  return text.length * fontSizePx * avgCharWidthRatio;
}

/**
 * Given a text string and the max width it must fit inside, solves for
 * the largest font size (bounded by MIN/MAX) that keeps the text within
 * that width. This is how "Three-Rex" gets auto-scaled to fill its slot
 * without manual resizing.
 */
function fitFontSizeToWidth(text, maxWidthPx) {
  const { MIN_FONT_SIZE_PX, MAX_FONT_SIZE_PX } = LAYOUT;
  let low = MIN_FONT_SIZE_PX;
  let high = MAX_FONT_SIZE_PX;
  let best = MIN_FONT_SIZE_PX;

  // Binary search for largest font size that fits maxWidthPx
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    const width = estimateTextWidth(text, mid);
    if (width <= maxWidthPx) {
      best = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return best;
}

/**
 * Core layout compiler for the Design Churner.
 * Positions:
 *   1. A central graphic/vector asset, scaled to a max height ratio,
 *      horizontally centered, sitting in the upper-to-mid canvas area.
 *   2. A styled text block beneath it, auto-scaled to fit safe-zone
 *      width, horizontally centered, with a proportional gap.
 *
 * Returns absolute pixel coordinates + dimensions for both layers,
 * ready to be handed to svgCompiler.
 */
function computeLayout({ text, assetAspectRatio = 1 } = {}) {
  const safeWidth = CANVAS_WIDTH_PX - SAFE_MARGIN_PX * 2;
  const safeHeight = CANVAS_HEIGHT_PX - SAFE_MARGIN_PX * 2;

  // 1. Central asset sizing
  const assetMaxHeight = safeHeight * LAYOUT.ASSET_MAX_HEIGHT_RATIO;
  let assetHeight = assetMaxHeight;
  let assetWidth = assetHeight * assetAspectRatio;

  // If the asset would overflow safe width (e.g. very wide/landscape asset),
  // re-derive from width instead so it never clips horizontally.
  if (assetWidth > safeWidth) {
    assetWidth = safeWidth;
    assetHeight = assetWidth / assetAspectRatio;
  }

  const assetX = (CANVAS_WIDTH_PX - assetWidth) / 2;
  const assetY = SAFE_MARGIN_PX;

  // 2. Text block sizing — fit within a slightly narrower band than
  // the full safe width, to preserve visual breathing room on the sides.
  const textMaxWidth = safeWidth * LAYOUT.TEXT_BLOCK_MAX_WIDTH_RATIO;
  const fontSize = fitFontSizeToWidth(text || '', textMaxWidth);
  const textActualWidth = estimateTextWidth(text || '', fontSize);
  const textHeight = fontSize * 1.2; // approximate line-height box

  const gap = CANVAS_HEIGHT_PX * LAYOUT.VERTICAL_GAP_RATIO;
  const textY = assetY + assetHeight + gap;
  const textX = (CANVAS_WIDTH_PX - textActualWidth) / 2;

  // Guard: if asset + text + gap would overflow the safe zone vertically,
  // scale both down proportionally so nothing clips off-canvas.
  const totalContentHeight = assetHeight + gap + textHeight;
  let overflowScale = 1;
  if (totalContentHeight > safeHeight) {
    overflowScale = safeHeight / totalContentHeight;
  }

  return {
    canvas: { width: CANVAS_WIDTH_PX, height: CANVAS_HEIGHT_PX },
    safeZone: {
      x: SAFE_MARGIN_PX,
      y: SAFE_MARGIN_PX,
      width: safeWidth,
      height: safeHeight,
    },
    asset: {
      x: assetX,
      y: assetY,
      width: assetWidth * overflowScale,
      height: assetHeight * overflowScale,
    },
    text: {
      x: textX,
      y: textY * overflowScale + SAFE_MARGIN_PX * (1 - overflowScale),
      width: textActualWidth * overflowScale,
      height: textHeight * overflowScale,
      fontSize: Math.round(fontSize * overflowScale),
      content: text || '',
    },
    overflowScale, // exposed so the Mentor Panel can flag "text was auto-shrunk"
  };
}

/**
 * Layout compiler for typography-only designs (apparel quote tees with
 * no central graphic). Text is auto-scaled via fitFontSizeToWidth() to
 * fill the full safe-zone width, then vertically centered within the
 * safe zone height. Returns the same shape as computeLayout(), but
 * with asset: null so downstream consumers can skip the asset layer.
 */
function computeTextOnlyLayout({ text } = {}) {
  const safeWidth = CANVAS_WIDTH_PX - SAFE_MARGIN_PX * 2;
  const safeHeight = CANVAS_HEIGHT_PX - SAFE_MARGIN_PX * 2;

  const fontSize = fitFontSizeToWidth(text || '', safeWidth);
  const textActualWidth = estimateTextWidth(text || '', fontSize);
  const textHeight = fontSize * 1.2; // approximate line-height box

  const textX = (CANVAS_WIDTH_PX - textActualWidth) / 2;
  const textY = SAFE_MARGIN_PX + (safeHeight - textHeight) / 2;

  return {
    canvas: { width: CANVAS_WIDTH_PX, height: CANVAS_HEIGHT_PX },
    safeZone: {
      x: SAFE_MARGIN_PX,
      y: SAFE_MARGIN_PX,
      width: safeWidth,
      height: safeHeight,
    },
    asset: null,
    text: {
      x: textX,
      y: textY,
      width: textActualWidth,
      height: textHeight,
      fontSize,
      content: text || '',
    },
    overflowScale: 1,
  };
}

module.exports = {
  computeLayout,
  computeTextOnlyLayout,
  fitFontSizeToWidth,
  estimateTextWidth,
};
