const express = require('express');
const router = express.Router();

const { compileDesignSvg } = require('../engine/svgCompiler');
const { rasterizeSvgToPng, writeSvgFile } = require('../engine/rasterizer');
const { CANVAS_WIDTH_PX, CANVAS_HEIGHT_PX } = require('../config/constants');

/**
 * POST /api/export
 * Body: same churner fields as /api/generate, plus:
 *   format: "png" | "svg" | "both"   (default "both")
 *
 * Compiles the design fresh from inputs and writes real files to
 * backend/storage/exports/, returning public URLs the frontend can
 * link to for download (served via the /exports static route).
 */
router.post('/', async (req, res, next) => {
  try {
    const {
      text,
      niche,
      paletteOverride,
      assetSvg,
      assetNativeSize,
      assetAspectRatio,
      transparentBackground,
      format = 'both',
      filenamePrefix,
    } = req.body;

    if (!text || !String(text).trim()) {
      return res.status(400).json({ error: 'text is required' });
    }

    const { svg } = compileDesignSvg({
      text: String(text).trim(),
      niche,
      paletteOverride,
      assetSvg,
      assetNativeSize,
      assetAspectRatio: assetAspectRatio || 1,
      transparentBackground: transparentBackground !== false,
    });

    const prefix = filenamePrefix ? String(filenamePrefix).replace(/[^a-z0-9-_]/gi, '_') : 'design';
    const result = {};

    if (format === 'svg' || format === 'both') {
      const svgFile = writeSvgFile({ svg, filenamePrefix: prefix });
      result.svg = svgFile;
    }

    if (format === 'png' || format === 'both') {
      const pngFile = await rasterizeSvgToPng({
        svg,
        widthPx: CANVAS_WIDTH_PX,
        heightPx: CANVAS_HEIGHT_PX,
        transparentBackground: transparentBackground !== false,
        filenamePrefix: prefix,
      });
      result.png = pngFile;
    }

    res.status(201).json({ message: 'Export complete', files: result });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
