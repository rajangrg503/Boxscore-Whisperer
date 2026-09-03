const express = require('express');
const router = express.Router();

const { compileDesignSvg } = require('../engine/svgCompiler');
const { critiqueDesign } = require('../engine/mentorEngine');
const { listNiches, listPalettes } = require('../engine/paletteEngine');

/**
 * POST /api/generate
 * Body: {
 *   text: string,                 e.g. "Three-Rex"
 *   niche: string,                e.g. "kids_birthday"
 *   element: string,              e.g. "Dinosaur" (currently informational;
 *                                  drives placeholder shape / future asset lookup)
 *   targetAge: string|number,     e.g. "3" (informational, reserved for future
 *                                  age-based sizing/style rules)
 *   paletteOverride: string,      optional explicit palette key
 *   assetSvg: string,             optional raw SVG markup for the central asset
 *   assetNativeSize: {width,height}, required if assetSvg is provided
 *   assetAspectRatio: number,     fallback aspect ratio if no assetSvg given
 *   transparentBackground: bool
 * }
 *
 * Returns the compiled preview SVG (full-res, embeddable directly in <img>
 * via data URI on the frontend), the layout math, and an automatic Mentor
 * critique so the churner and mentor stay in sync on every generation.
 */
router.post('/', (req, res, next) => {
  try {
    const {
      text,
      niche,
      element,
      targetAge,
      paletteOverride,
      assetSvg,
      assetNativeSize,
      assetAspectRatio,
      transparentBackground,
    } = req.body;

    if (!text || !String(text).trim()) {
      return res.status(400).json({ error: 'text is required (e.g. "Three-Rex")' });
    }

    const { svg, layout, style } = compileDesignSvg({
      text: String(text).trim(),
      niche,
      paletteOverride,
      assetSvg,
      assetNativeSize,
      assetAspectRatio: assetAspectRatio || 1,
      transparentBackground: transparentBackground !== false,
    });

    const critique = critiqueDesign({ layout, style });

    res.json({
      meta: { element: element || null, targetAge: targetAge || null },
      svg,
      layout,
      style,
      critique,
    });
  } catch (err) {
    next(err);
  }
});

// GET /api/generate/options — lets the frontend populate niche/palette dropdowns
router.get('/options', (req, res) => {
  res.json({ niches: listNiches(), palettes: listPalettes() });
});

module.exports = router;
