import { useEffect } from 'react';
import { useFabricCanvas } from '../../hooks/useFabricCanvas';
import { useDesignStore } from '../../store/designStore';
import LayerPanel from './LayerPanel';
import Toolbar from './Toolbar';

export default function CanvasWorkspace() {
  const {
    canvasElRef,
    ready,
    layers,
    selectedId,
    loadDesignSvg,
    toggleLayerVisibility,
    selectLayer,
    previewSize,
  } = useFabricCanvas();

  const currentSvg = useDesignStore((s) => s.currentSvg);
  const isGenerating = useDesignStore((s) => s.isGenerating);

  useEffect(() => {
    if (ready && currentSvg) {
      loadDesignSvg(currentSvg);
    }
  }, [ready, currentSvg, loadDesignSvg]);

  return (
    <div className="flex gap-4 w-full h-full">
      <div className="flex-1 flex flex-col items-center gap-3">
        <Toolbar />

        <div className="text-xs uppercase tracking-wider text-gray-400">
          Working Preview — {previewSize.width}×{previewSize.height}px
          <span className="text-gray-600"> (represents 4500×5400 @ 300 DPI)</span>
        </div>

        <div className="relative border border-panelLight shadow-2xl checker-bg">
          <canvas ref={canvasElRef} />

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

        {!ready && <p className="text-gray-500 text-sm">Initializing canvas engine…</p>}
      </div>

      <LayerPanel
        layers={layers}
        selectedId={selectedId}
        onSelect={selectLayer}
        onToggleVisibility={toggleLayerVisibility}
      />
    </div>
  );
}
