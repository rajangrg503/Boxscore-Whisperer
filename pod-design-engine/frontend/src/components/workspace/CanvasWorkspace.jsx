import { useMemo } from 'react';
import { useDesignStore } from '../../store/designStore';
import Toolbar from './Toolbar';
import { PREVIEW_SCALE, SAFE_MARGIN_PX, CANVAS_WIDTH_PX, CANVAS_HEIGHT_PX } from '../../lib/canvasConstants';

const PREVIEW_W = Math.round(CANVAS_WIDTH_PX * PREVIEW_SCALE);
const PREVIEW_H = Math.round(CANVAS_HEIGHT_PX * PREVIEW_SCALE);

/**
 * Renders the compiled design as a plain <img>, built directly from the
 * exact SVG string returned by POST /api/generate — the same SVG that
 * gets written to disk on export. This guarantees the preview always
 * matches the real deliverable pixel-for-pixel.
 *
 * NOTE: This intentionally does NOT use Fabric.js for the live preview.
 * An earlier version loaded the SVG into a Fabric canvas for interactive
 * layer editing, but Fabric's SVG importer handled <text> sizing
 * inconsistently versus shape geometry across multiple scaling
 * strategies (per-object, per-group, and full-resolution + CSS-only
 * downscale), consistently rendering text oversized in the editor even
 * though the underlying compiled SVG (verified via direct export) was
 * always correct. Removing Fabric from the preview path removes that
 * bug entirely. Interactive drag-to-edit layers can be reintroduced
 * later as a dedicated feature once it can be properly built and tested
 * end-to-end, rather than patched under time pressure.
 */
export default function CanvasWorkspace() {
  const currentSvg = useDesignStore((s) => s.currentSvg);
  const currentLayout = useDesignStore((s) => s.currentLayout);
  const isGenerating = useDesignStore((s) => s.isGenerating);

  const imgSrc = useMemo(() => {
    if (!currentSvg) return null;
    return `data:image/svg+xml;utf8,${encodeURIComponent(currentSvg)}`;
  }, [currentSvg]);

  // Static, display-only layer summary derived from the last generation.
  // Not interactive (no Fabric canvas backing it), just informational.
  const displayLayers = useMemo(() => {
    if (!currentLayout) return [];
    return [
      { id: 'text', name: `Text: ${currentLayout.text?.content?.slice(0, 18) || ''}` },
      { id: 'asset', name: 'Central Asset' },
    ];
  }, [currentLayout]);

  return (
    <div className="flex gap-4 w-full h-full">
      <div className="flex-1 flex flex-col items-center gap-3">
        <Toolbar />

        <div className="text-xs uppercase tracking-wider text-gray-400">
          Preview — {PREVIEW_W}×{PREVIEW_H}px display
          <span className="text-gray-600"> (actual file is {CANVAS_WIDTH_PX}×{CANVAS_HEIGHT_PX} @ 300 DPI)</span>
        </div>

        <div
          className="relative border border-panelLight shadow-2xl checker-bg overflow-hidden"
          style={{ width: PREVIEW_W, height: PREVIEW_H }}
        >
          {imgSrc && (
            <img
              src={imgSrc}
              alt="Compiled design preview"
              className="absolute inset-0 w-full h-full object-contain"
            />
          )}

          {/* Safe-zone guide, drawn as a plain CSS overlay */}
          <div
            className="absolute pointer-events-none border-2 border-dashed border-accent/70"
            style={{
              left: SAFE_MARGIN_PX * PREVIEW_SCALE,
              top: SAFE_MARGIN_PX * PREVIEW_SCALE,
              width: PREVIEW_W - SAFE_MARGIN_PX * PREVIEW_SCALE * 2,
              height: PREVIEW_H - SAFE_MARGIN_PX * PREVIEW_SCALE * 2,
            }}
          />

          {isGenerating && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/50">
              <span className="text-sm text-gray-200 animate-pulse">Compiling design…</span>
            </div>
          )}

          {!currentSvg && !isGenerating && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <span className="text-sm text-gray-500 text-center px-8">
                Use the Churner panel to generate your first design
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="w-56 bg-panel rounded-lg p-3 flex flex-col gap-2 h-fit">
        <div className="text-gray-300 text-sm font-semibold mb-1">Layers</div>
        {displayLayers.length === 0 && (
          <p className="text-xs text-gray-500">No layers yet — generate a design to populate this list.</p>
        )}
        {displayLayers.map((layer) => (
          <div key={layer.id} className="px-2 py-1.5 rounded text-xs text-gray-300 hover:bg-panelLight">
            {layer.name}
          </div>
        ))}
      </div>
    </div>
  );
}
