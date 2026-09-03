import { useEffect, useRef, useState, useCallback } from 'react';
import { Canvas, loadSVGFromString, Rect } from 'fabric';
import { PREVIEW_SCALE, SAFE_MARGIN_PX, CANVAS_WIDTH_PX, CANVAS_HEIGHT_PX } from '../lib/canvasConstants';

const PREVIEW_W = Math.round(CANVAS_WIDTH_PX * PREVIEW_SCALE);
const PREVIEW_H = Math.round(CANVAS_HEIGHT_PX * PREVIEW_SCALE);

/**
 * Manages a Fabric.js canvas whose INTERNAL coordinate system matches the
 * full 4500x5400 export resolution exactly — objects parsed from the
 * compiled SVG are added completely unscaled, at their native size and
 * position. The canvas is then visually shrunk to preview size using
 * Fabric's `cssOnly` dimension mode, which resizes only the on-screen
 * <canvas> element via CSS while leaving the internal render/coordinate
 * space untouched (and correctly rescales mouse/pointer math for us).
 *
 * This deliberately avoids ANY manual per-object or per-group scaling.
 * Manual scaling was the root cause of a recurring bug where Fabric's
 * SVG importer handled scale differently for <text> elements than for
 * path/shape geometry, causing text to render oversized regardless of
 * whether scaling was applied via direct property assignment, .set(),
 * or group-level transforms. Removing scaling entirely removes that
 * whole class of bug.
 */
export function useFabricCanvas() {
  const canvasElRef = useRef(null);
  const fabricRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [layers, setLayers] = useState([]);
  const [selectedId, setSelectedId] = useState(null);

  useEffect(() => {
    const canvas = new Canvas(canvasElRef.current, {
      width: CANVAS_WIDTH_PX,
      height: CANVAS_HEIGHT_PX,
      backgroundColor: 'transparent',
      preserveObjectStacking: true,
    });
    fabricRef.current = canvas;

    // Shrink ONLY the visual/CSS size to preview dimensions. Internal
    // coordinate space stays at full 4500x5400 resolution.
    canvas.setDimensions({ width: PREVIEW_W, height: PREVIEW_H }, { cssOnly: true });

    // Safe-zone guide drawn at REAL (unscaled) coordinates, since the
    // canvas's internal space is already full resolution.
    const safeZone = new Rect({
      left: SAFE_MARGIN_PX,
      top: SAFE_MARGIN_PX,
      width: CANVAS_WIDTH_PX - SAFE_MARGIN_PX * 2,
      height: CANVAS_HEIGHT_PX - SAFE_MARGIN_PX * 2,
      fill: 'transparent',
      stroke: '#7c5cff',
      strokeWidth: 12, // thicker so it stays visible after CSS downscale
      strokeDashArray: [40, 28],
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
    setLayers(objs.reverse());
  }, []);

  const loadDesignSvg = useCallback(
    async (svgString) => {
      const canvas = fabricRef.current;
      if (!canvas || !svgString) return;

      const { objects } = await loadSVGFromString(svgString);
      const validObjects = objects.filter(Boolean);

      canvas
        .getObjects()
        .filter((o) => o.name !== '__safe_zone_guide')
        .forEach((o) => canvas.remove(o));

      // No scaling applied — objects come in at their native full-res
      // coordinates, which already match the canvas's internal space.
      validObjects.forEach((obj, idx) => {
        obj.__layerId = `layer-${idx}-${obj.type}`;
        obj.__layerName =
          obj.type === 'text' || obj.type === 'i-text'
            ? `Text: ${obj.text ? obj.text.slice(0, 18) : ''}`
            : `Asset ${idx + 1}`;
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
