const { optimize } = require('svgo');
const { computeLayout } = require('./layoutEngine');
const { resolveStyle } = require('./paletteEngine');

/**
 * Generates a placeholder central-asset SVG fragment when no real vector
 * asset is supplied. This keeps the churner fully functional out of the
 * box (e.g. "Dinosaur" -> a simple silhouette placeholder) until you drop
 * real vector art into backend/storage/assets and wire it in via assetSvg.
 */
function buildPlaceholderAsset({ width, height, accentColor }) {
  // Simple rounded silhouette as a stand-in shape — swap this out by
  // passing a real `assetSvg` markup string into compileDesignSvg().
  const r = Math.min(width, height) * 0.5;
  const cx = width / 2;
  const cy = height / 2;
  return `
    <g>
      <ellipse cx="${cx}" cy="${cy * 1.15}" rx="${r * 0.62}" ry="${r * 0.5}" fill="${accentColor}" />
      <circle cx="${cx}" cy="${cy * 0.55}" r="${r * 0.42}" fill="${accentColor}" />
    </g>
  `;
}

/**
 * Wraps arbitrary asset SVG markup (inner content only, no outer <svg> tag)
 * in a positioning/scaling <g> so it lands exactly where the layout
 * engine calculated, regardless of the asset's native viewBox size.
 */
function positionAsset({ assetSvg, assetNativeSize, layoutAsset, accentColor }) {
  if (!assetSvg) {
    return `<g transform="translate(${layoutAsset.x}, ${layoutAsset.y})">
      ${buildPlaceholderAsset({
        width: layoutAsset.width,
        height: layoutAsset.height,
        accentColor,
      })}
    </g>`;
  }

  const nativeW = assetNativeSize?.width || layoutAsset.width;
  const nativeH = assetNativeSize?.height || layoutAsset.height;
  const scaleX = layoutAsset.width / nativeW;
  const scaleY = layoutAsset.height / nativeH;

  return `<g transform="translate(${layoutAsset.x}, ${layoutAsset.y}) scale(${scaleX}, ${scaleY})">
    ${assetSvg}
  </g>`;
}

function buildTextLayer({ layoutText, palette, typography }) {
  const { fontStack, fontWeight, textTransform } = typography;
  let displayText = layoutText.content;
  if (textTransform === 'uppercase') displayText = displayText.toUpperCase();
  if (textTransform === 'capitalize') {
    displayText = displayText.replace(/\b\w/g, (c) => c.toUpperCase());
  }

  return `<text
      x="${layoutText.x + layoutText.width / 2}"
      y="${layoutText.y + layoutText.fontSize * 0.85}"
      text-anchor="middle"
      font-family="${fontStack}"
      font-weight="${fontWeight}"
      font-size="${layoutText.fontSize}"
      fill="${palette.textColor}"
    >${escapeXml(displayText)}</text>`;
}

function escapeXml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Top-level compiler: takes churner inputs, runs the layout engine,
 * resolves niche palette/typography, and returns a complete, optimized
 * SVG document string at full 4500x5400 export dimensions.
 */
function compileDesignSvg({
  text,
  niche,
  paletteOverride,
  assetSvg,
  assetNativeSize,
  assetAspectRatio = 1,
  transparentBackground = true,
}) {
  const style = resolveStyle({ niche, paletteOverride });
  const layout = computeLayout({ text, assetAspectRatio });

  const backgroundRect = transparentBackground
    ? ''
    : `<rect x="0" y="0" width="${layout.canvas.width}" height="${layout.canvas.height}" fill="${style.palette.colors[2] || '#FFFFFF'}" />`;

  const assetLayer = positionAsset({
    assetSvg,
    assetNativeSize,
    layoutAsset: layout.asset,
    accentColor: style.palette.accentColor,
  });

  const textLayer = buildTextLayer({
    layoutText: layout.text,
    palette: style.palette,
    typography: style.typography,
  });

  const rawSvg = `<svg
    xmlns="http://www.w3.org/2000/svg"
    width="${layout.canvas.width}"
    height="${layout.canvas.height}"
    viewBox="0 0 ${layout.canvas.width} ${layout.canvas.height}"
  >
    ${backgroundRect}
    ${assetLayer}
    ${textLayer}
  </svg>`;

  const { data: optimizedSvg } = optimize(rawSvg, {
    multipass: true,
    plugins: [
      {
        name: 'preset-default',
        params: { overrides: { removeViewBox: false } },
      },
    ],
  });

  return {
    svg: optimizedSvg,
    layout,
    style,
  };
}

module.exports = { compileDesignSvg };
