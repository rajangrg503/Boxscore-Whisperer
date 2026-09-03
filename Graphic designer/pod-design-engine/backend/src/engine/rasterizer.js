const sharp = require('sharp');
const fs = require('fs');
const path = require('path');
const { v4: uuidv4 } = require('uuid');
const { DPI, STORAGE } = require('../config/constants');

const EXPORTS_DIR = path.join(__dirname, '..', '..', STORAGE.EXPORTS);

/**
 * Rasterizes an SVG string to a 300 DPI PNG file on disk.
 * `transparentBackground` controls whether the alpha channel is preserved
 * (POD-ready transparent PNG) or flattened to a solid background.
 *
 * Returns the absolute file path + the public /exports/ URL path.
 */
async function rasterizeSvgToPng({
  svg,
  widthPx,
  heightPx,
  transparentBackground = true,
  filenamePrefix = 'design',
}) {
  const filename = `${filenamePrefix}-${uuidv4()}.png`;
  const outputPath = path.join(EXPORTS_DIR, filename);

  let pipeline = sharp(Buffer.from(svg), { density: DPI })
    .resize(widthPx, heightPx, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .withMetadata({ density: DPI });

  if (!transparentBackground) {
    pipeline = pipeline.flatten({ background: '#FFFFFF' });
  }

  await pipeline.png({ compressionLevel: 9 }).toFile(outputPath);

  return {
    absolutePath: outputPath,
    publicPath: `/exports/${filename}`,
    filename,
  };
}

/**
 * Writes the raw (already-optimized) SVG string to disk as a standalone
 * vector export — the "SVG vector" deliverable from the churner.
 */
function writeSvgFile({ svg, filenamePrefix = 'design' }) {
  const filename = `${filenamePrefix}-${uuidv4()}.svg`;
  const outputPath = path.join(EXPORTS_DIR, filename);
  fs.writeFileSync(outputPath, svg, 'utf-8');
  return {
    absolutePath: outputPath,
    publicPath: `/exports/${filename}`,
    filename,
  };
}

module.exports = { rasterizeSvgToPng, writeSvgFile };
