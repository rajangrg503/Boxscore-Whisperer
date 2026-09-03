const palettes = require('../data/palettes.json');
const niches = require('../data/niches.json');

/**
 * Resolves a color palette + typography defaults for a given niche key.
 * Falls back to 'premium_neutral' if the niche isn't recognized, and
 * allows a manual paletteOverride to win regardless of niche.
 */
function resolveStyle({ niche, paletteOverride } = {}) {
  const nicheConfig = niches[niche] || niches.premium_apparel;
  const paletteKey = paletteOverride && palettes[paletteOverride]
    ? paletteOverride
    : nicheConfig.defaultPalette;

  const palette = palettes[paletteKey] || palettes.premium_neutral;

  return {
    nicheKey: niches[niche] ? niche : 'premium_apparel',
    nicheLabel: nicheConfig.label,
    paletteKey,
    palette,
    typography: {
      fontStack: nicheConfig.fontStack,
      fontWeight: nicheConfig.fontWeight,
      textTransform: nicheConfig.textTransform,
    },
  };
}

function listPalettes() {
  return Object.entries(palettes).map(([key, value]) => ({ key, ...value }));
}

function listNiches() {
  return Object.entries(niches).map(([key, value]) => ({ key, ...value }));
}

module.exports = { resolveStyle, listPalettes, listNiches };
