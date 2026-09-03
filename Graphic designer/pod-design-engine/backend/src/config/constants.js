// Print-on-demand export standard: 4500x5400px @ 300 DPI = 15in x 18in
module.exports = {
  CANVAS_WIDTH_PX: 4500,
  CANVAS_HEIGHT_PX: 5400,
  DPI: 300,

  // Preview/working canvas used by the frontend Fabric.js editor
  PREVIEW_SCALE: 0.15, // 675 x 810 preview

  EXPORT_FORMATS: ['png', 'svg', 'pdf'],

  // 0.75in safe zone at 300dpi — keep critical content inside this
  SAFE_MARGIN_PX: 225,

  STORAGE: {
    TEMPLATES: 'storage/templates',
    ASSETS: 'storage/assets',
    EXPORTS: 'storage/exports',
  },

  // Layout engine defaults for the Design Churner
  LAYOUT: {
    ASSET_MAX_HEIGHT_RATIO: 0.55, // central graphic occupies up to 55% of canvas height
    TEXT_BLOCK_MAX_WIDTH_RATIO: 0.8,
    VERTICAL_GAP_RATIO: 0.04, // gap between asset and text as % of canvas height
    MIN_FONT_SIZE_PX: 120,
    MAX_FONT_SIZE_PX: 620,
  },
};
