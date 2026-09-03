import { useEffect, useRef, useState, useCallback } from 'react';
import { Canvas, loadSVGFromString, util, Rect } from 'fabric';
import { PREVIEW_SCALE, SAFE_MARGIN_PX, CANVAS_WIDTH_PX, CANVAS_HEIGHT_PX } from '../lib/canvasConstants';

const PREVIEW_W = Math.round(CANVAS_WIDTH_PX * PREVIEW_SCALE);
const PREVIEW_H = Math.round(CANVAS_HEIGHT_PX * PREVIEW_SCALE);

/**
 * Manages a Fabric.js canvas instance sized to the scaled-down preview
 * dimensions (the real export is always full 4500x5400 @ 300DPI, done
 * server-side — this canvas is purely the interactive editing surface).
 *
 * Exposes:
 *   - canvasElRef: attach to the <canvas> element
 *   - layers: current list of editable objects (for the LayerPanel)
 *   - loadDesignSvg(svgString): replaces canvas content with a compiled design
 *   - selectLayer(id) / toggleLayerVisibility(id)
 */
export function useFabricCanvas() {
  const canvasElRef = useRef(null);
  const fabricRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [layers, setLayers] = useState([]);
  const [selectedId, setSelectedId] = useState(null);

  useEffect(() => {
    const canvas = new Canvas(canvasElRef.current, {
      width: PREVIEW_W,
      height: PREVIEW_H,
      backgroundColor: 'transparent',
      preserveObjectStacking: true,
    });
    fabricRef.current = canvas;

    const safeZone = new Rect({
      left: SAFE_MARGIN_PX * PREVIEW_SCALE,
      top: SAFE_MARGIN_PX * PREVIEW_SCALE,
      width: PREVIEW_W - SAFE_MARGIN_PX * PREVIEW_SCALE * 2,
      height: PREVIEW_H - SAFE_MARGIN_PX * PREVIEW_SCALE * 2,
      fill: 'transparent',
      stroke: '#7c5cff',
      strokeDashArray: [6, 4],
      selectable: false,
      evented: false,
      excludeFromExport: true,
      name: '__safe_zone_guide',
    });
    canvas.add(safeZone);

    const syncSelection = () => {
      const active = canvas.getActiveObject();
      setSelectedId(active?.__layerId || null);
    };
    canvas.on('selection:created', syncSelection);
    canvas.on('selection:updated', syncSelection);
    canvas.on('selection:cleared', () => setSelectedId(null));

    canvas.renderAll();
    setReady(true);

    return () => {
      canvas.dispose();
      fabricRef.current = null;
    };
  }, []);

  const refreshLayerList = useCallback(() => {
    const canvas = fabricRef.current;
    if (!canvas) return;
    const objs = canvas
      .getObjects()
      .filter((o) => o.name !== '__safe_zone_guide')
      .map((o, idx) => ({
        id: o.__layerId || `layer-${idx}`,
        name: o.__layerName || o.type,
        visible: o.visible !== false,
      }));
    setLayers(objs.reverse()); // top-of-stack first, matches visual layering
  }, []);

  /**
   * Loads a full-res compiled SVG string (from POST /api/generate) into
   * the preview canvas, scaling it down to PREVIEW_W/H while keeping the
   * underlying objects individually selectable/movable as layers.
   */
  const loadDesignSvg = useCallback(
    async (svgString) => {
      const canvas = fabricRef.current;
      if (!canvas || !svgString) return;

      const { objects } = await loadSVGFromString(svgString);
      const validObjects = objects.filter(Boolean);

      // Remove previous design layers (keep the safe-zone guide)
      canvas
        .getObjects()
        .filter((o) => o.name !== '__safe_zone_guide')
        .forEach((o) => canvas.remove(o));

      validObjects.forEach((obj, idx) => {
        obj.scaleX = (obj.scaleX || 1) * PREVIEW_SCALE;
        obj.scaleY = (obj.scaleY || 1) * PREVIEW_SCALE;
        obj.left = (obj.left || 0) * PREVIEW_SCALE;
        obj.top = (obj.top || 0) * PREVIEW_SCALE;
        obj.__layerId = `layer-${idx}-${obj.type}`;
        obj.__layerName = obj.type === 'text' || obj.type === 'i-text' ? `Text: ${obj.text?.slice(0, 18) || ''}` : `Asset ${idx + 1}`;
        canvas.add(obj);
      });

      canvas.requestRenderAll();
      refreshLayerList();
    },
    [refreshLayerList]
  );

  const toggleLayerVisibility = useCallback(
    (id) => {
      const canvas = fabricRef.current;
      if (!canvas) return;
      const obj = canvas.getObjects().find((o) => o.__layerId === id);
      if (obj) {
        obj.visible = !obj.visible;
        canvas.requestRenderAll();
        refreshLayerList();
      }
    },
    [refreshLayerList]
  );

  const selectLayer = useCallback((id) => {
    const canvas = fabricRef.current;
    if (!canvas) return;
    const obj = canvas.getObjects().find((o) => o.__layerId === id);
    if (obj) {
      canvas.setActiveObject(obj);
      canvas.requestRenderAll();
      setSelectedId(id);
    }
  }, []);

  return {
    canvasElRef,
    ready,
    layers,
    selectedId,
    loadDesignSvg,
    toggleLayerVisibility,
    selectLayer,
    previewSize: { width: PREVIEW_W, height: PREVIEW_H },
  };
}
