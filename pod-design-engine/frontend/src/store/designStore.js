import { create } from 'zustand';

export const useDesignStore = create((set, get) => ({
  // --- Churner inputs ---
  churnerInputs: {
    niche: 'kids_birthday',
    element: 'Dinosaur',
    targetAge: '3',
    text: 'Three-Rex',
    paletteOverride: '',
    transparentBackground: true,
  },
  setChurnerInput: (key, value) =>
    set((state) => ({ churnerInputs: { ...state.churnerInputs, [key]: value } })),

  // --- Generation result (from POST /api/generate) ---
  currentSvg: null,
  currentLayout: null,
  currentStyle: null,
  isGenerating: false,
  setGenerationResult: ({ svg, layout, style }) =>
    set({ currentSvg: svg, currentLayout: layout, currentStyle: style }),
  setIsGenerating: (val) => set({ isGenerating: val }),

  // --- Mentor critique ---
  critique: null,
  isCritiquing: false,
  setCritique: (critique) => set({ critique }),
  setIsCritiquing: (val) => set({ isCritiquing: val }),

  // --- Reference data (niches / palettes from backend) ---
  niches: [],
  palettes: [],
  setOptions: ({ niches, palettes }) => set({ niches, palettes }),

  // --- Export state ---
  isExporting: false,
  lastExport: null,
  setIsExporting: (val) => set({ isExporting: val }),
  setLastExport: (result) => set({ lastExport: result }),

  // --- Templates ---
  savedTemplates: [],
  setSavedTemplates: (list) => set({ savedTemplates: list }),
}));
