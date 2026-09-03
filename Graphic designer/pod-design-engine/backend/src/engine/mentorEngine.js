const { SAFE_MARGIN_PX, CANVAS_WIDTH_PX, CANVAS_HEIGHT_PX } = require('../config/constants');

/**
 * Relative luminance (WCAG formula) for a hex color — used to compute
 * contrast ratios between text and accent/background colors.
 */
function luminance(hex) {
  const c = hex.replace('#', '');
  const r = parseInt(c.substring(0, 2), 16) / 255;
  const g = parseInt(c.substring(2, 4), 16) / 255;
  const b = parseInt(c.substring(4, 6), 16) / 255;
  const toLinear = (v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
}

function contrastRatio(hexA, hexB) {
  const lumA = luminance(hexA) + 0.05;
  const lumB = luminance(hexB) + 0.05;
  return lumA > lumB ? lumA / lumB : lumB / lumA;
}

/**
 * Evaluates a compiled layout + style against premium-design heuristics.
 * Returns a score (0-100) per category plus specific, actionable notes —
 * this is the logic behind the "Canva Mentor Panel".
 */
function critiqueDesign({ layout, style }) {
  const notes = [];
  const scores = {};

  // --- 1. Breathing room / margins ---
  const marginRatio = SAFE_MARGIN_PX / Math.min(CANVAS_WIDTH_PX, CANVAS_HEIGHT_PX);
  let marginScore = 100;
  if (layout.overflowScale < 1) {
    marginScore = Math.round(60 * layout.overflowScale);
    notes.push({
      category: 'Breathing Room',
      severity: 'warning',
      note: `Content was auto-shrunk ${Math.round((1 - layout.overflowScale) * 100)}% to fit the safe zone. Consider shortening the text or reducing the central asset size — cramped compositions read as cheap, not premium.`,
    });
  } else if (marginRatio < 0.045) {
    marginScore = 70;
    notes.push({
      category: 'Breathing Room',
      severity: 'suggestion',
      note: 'Safe margin is tight for a premium look. Widen the margin so the design doesn\'t feel like it\'s fighting the print edges.',
    });
  } else {
    notes.push({
      category: 'Breathing Room',
      severity: 'pass',
      note: 'Margins give the composition room to breathe — reads clean at print size.',
    });
  }
  scores.breathingRoom = marginScore;

  // --- 2. Typography hierarchy ---
  const fontSize = layout.text.fontSize;
  let hierarchyScore = 100;
  if (fontSize < 160) {
    hierarchyScore = 55;
    notes.push({
      category: 'Typography Hierarchy',
      severity: 'warning',
      note: 'Text is rendering small relative to the canvas. On apparel, undersized type loses impact at viewing distance — push it larger or shorten the phrase.',
    });
  } else if (fontSize > 560) {
    hierarchyScore = 75;
    notes.push({
      category: 'Typography Hierarchy',
      severity: 'suggestion',
      note: 'Text is near max scale — good for impact, but double check it isn\'t crowding the central asset. Premium designs usually leave one clear focal point.',
    });
  } else {
    notes.push({
      category: 'Typography Hierarchy',
      severity: 'pass',
      note: 'Text scale creates a clear secondary focal point beneath the main graphic — solid hierarchy.',
    });
  }
  scores.typographyHierarchy = hierarchyScore;

  // --- 3. Contrast (text vs accent color, as a proxy for garment contrast) ---
  const ratio = contrastRatio(style.palette.textColor, style.palette.accentColor);
  let contrastScore;
  if (ratio >= 4.5) {
    contrastScore = 100;
    notes.push({
      category: 'Contrast',
      severity: 'pass',
      note: `Text-to-accent contrast ratio is ${ratio.toFixed(1)}:1 — well above the 4.5:1 legibility threshold, holds up on light and dark garments.`,
    });
  } else if (ratio >= 3) {
    contrastScore = 70;
    notes.push({
      category: 'Contrast',
      severity: 'suggestion',
      note: `Contrast ratio is ${ratio.toFixed(1)}:1 — acceptable, but pushing text color darker/lighter relative to the accent would read more premium at a glance.`,
    });
  } else {
    contrastScore = 40;
    notes.push({
      category: 'Contrast',
      severity: 'warning',
      note: `Contrast ratio is only ${ratio.toFixed(1)}:1 — text may wash out against the accent color on certain garment colors. Increase separation.`,
    });
  }
  scores.contrast = contrastScore;

  // --- 4. Alignment ---
  const assetCenterX = layout.asset.x + layout.asset.width / 2;
  const textCenterX = layout.text.x + layout.text.width / 2;
  const centerDelta = Math.abs(assetCenterX - textCenterX);
  let alignmentScore = 100;
  if (centerDelta > 4) {
    alignmentScore = 65;
    notes.push({
      category: 'Alignment',
      severity: 'warning',
      note: `Asset and text centers are offset by ~${Math.round(centerDelta)}px. Misalignment is one of the fastest ways a design reads as amateur — re-center both on the same vertical axis.`,
    });
  } else {
    notes.push({
      category: 'Alignment',
      severity: 'pass',
      note: 'Central asset and text share a common vertical axis — clean, intentional alignment.',
    });
  }
  scores.alignment = alignmentScore;

  const overall = Math.round(
    (scores.breathingRoom + scores.typographyHierarchy + scores.contrast + scores.alignment) / 4
  );

  return {
    overallScore: overall,
    scores,
    notes,
    verdict:
      overall >= 85
        ? 'Premium-ready. This composition would hold up in a paid Canva Pro template pack.'
        : overall >= 65
        ? 'Solid foundation, but a few adjustments stand between this and premium.'
        : 'Needs work before this is print-ready at a premium tier — see notes above.',
  };
}

module.exports = { critiqueDesign, contrastRatio };
