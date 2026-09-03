# POD Design Engine (Local)

A locally-hosted automated design engine for Print-on-Demand apparel graphics.
Generates 4500×5400px @ 300 DPI transparent PNGs and SVG vectors from
niche/text/element inputs, with a built-in "Canva Mentor" design critic and
reusable JSON template system.

## What's inside

- **backend/** — Node/Express server. Real logic, no stubs:
  - `layoutEngine.js` — auto-positions the central asset + text block, auto-scales
    text to fit width via binary search, guards against vertical overflow.
  - `paletteEngine.js` — resolves niche → color palette + typography defaults.
  - `svgCompiler.js` — assembles the final layered SVG (optimized via SVGO).
  - `rasterizer.js` — SVG → 300 DPI PNG via `sharp`, alpha channel toggleable.
  - `mentorEngine.js` — rule-based critique: WCAG contrast ratio math,
    margin/breathing-room checks, typography hierarchy, alignment delta —
    returns a 0-100 score per category with specific fix instructions.
- **frontend/** — React (Vite) + Tailwind + Fabric.js editing workspace,
  a Churner input panel, live Mentor critique panel, and Template Master.

All engine logic (`layoutEngine`, `paletteEngine`, `mentorEngine`) was
verified to run correctly end-to-end with real numbers before packaging —
see the "Verified" section below.

## Setup (run these two blocks in two separate terminals)

### 1. Backend

```bash
cd backend
npm install
npm run dev
```

Backend boots at `http://localhost:4000`. Confirm with:
```bash
curl http://localhost:4000/api/health
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend opens at `http://localhost:5173`.

That's it — two terminals, two `npm install && npm run dev` pairs.

## Using it

1. In the left **Design Churner** panel, pick a Niche (e.g. "Kids Birthday"),
   set Target Age, Central Element, and Display Text (e.g. "Three-Rex"),
   then click **Generate Design**.
2. The center canvas renders the compiled design as editable Fabric.js
   layers (visible in the right-side Layer list within the workspace).
3. The right **Canva Mentor** panel automatically scores the design on
   Breathing Room, Typography Hierarchy, Contrast, and Alignment, with
   specific notes on how to make it read as premium.
4. Use **Export PNG (300dpi)** or **Export SVG** in the toolbar to write
   real files to `backend/storage/exports/` — links open directly.
5. Use **Template Master** (left sidebar) to save the current churner
   inputs as a named, reusable JSON schema, or reload a saved one.

## Extending with real vector assets

Right now the central "asset" layer is a placeholder silhouette generated
procedurally (see `svgCompiler.js` → `buildPlaceholderAsset`). To use real
artwork:

1. Drop SVG files into `backend/storage/assets/`.
2. Pass the raw inner SVG markup (no outer `<svg>` tag) as `assetSvg` in
   the `/api/generate` and `/api/export` request bodies, along with
   `assetNativeSize: { width, height }` so it scales correctly into the
   layout engine's computed bounding box.

A simple next step would be a small "asset library" endpoint that reads
`storage/assets/`, extracts each file's native viewBox, and returns it to
the frontend so the Churner panel can offer a dropdown instead of requiring
raw SVG to be passed manually.

## Verified before packaging

Since this was built in a network-isolated sandbox, here's exactly what
was and wasn't runnable before handing it to you:

- ✅ All 12 backend `.js` files — syntax-validated with `node --check`
- ✅ All 12 frontend `.jsx`/`.js` files — syntax-validated via TypeScript's
  transpiler (catches JSX/syntax errors without needing installed deps)
- ✅ `layoutEngine.js`, `paletteEngine.js`, `mentorEngine.js` — executed
  live with zero dependencies (pure Node), producing correct real-numbered
  output (verified 4500×5400 canvas math, accurate WCAG contrast ratio
  calculation, sensible 94/100 critique score)
- ⛔ `svgCompiler.js` (needs `svgo`) and `rasterizer.js` (needs `sharp`) —
  could not execute without `npm install`, since this sandbox has no
  network access. Logic was hand-reviewed but not run.
- ⛔ Full Express server boot and Vite dev server — same reason.
- ⛔ Fabric.js `loadSVGFromString` integration — written against the
  documented Fabric v6 API but not runtime-tested.

**First thing to do after `npm install`**: click Generate once and watch
both terminal logs. If the Fabric SVG-loading step or sharp rasterization
throws anything, paste the error back to me and I'll fix it immediately —
that's the one layer I genuinely couldn't verify from here.
