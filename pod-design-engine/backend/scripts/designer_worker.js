require('dotenv').config();

const fs = require('fs');
const path = require('path');

const { compileTypographyDesignSvg } = require('../src/engine/svgCompiler');
const { rasterizeSvgToPng } = require('../src/engine/rasterizer');
const { generateEtsyMetadata } = require('../src/engine/etsyMetadata');
const { CANVAS_WIDTH_PX, CANVAS_HEIGHT_PX } = require('../src/config/constants');
const canva = require('../src/engine/canvaClient');

const OUTPUT_ROOT = path.join(__dirname, '..', 'completed_etsy_packages');

const DESIGN_QUEUE = [
  { niche: 'real_estate_agents', text: 'In My Real Estate Era' },
  { niche: 'aesthetic_services', text: 'Self Care & Fine Lines' },
  { niche: 'corporate_coaches', text: 'Consistency Beats Talent' },
];

function slugify(text) {
  return String(text)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function logError(niche, message) {
  const nicheDir = path.join(OUTPUT_ROOT, niche);
  fs.mkdirSync(nicheDir, { recursive: true });
  const logLine = `[${new Date().toISOString()}] ${message}\n`;
  fs.appendFileSync(path.join(nicheDir, 'error.log'), logLine, 'utf-8');
}

async function processDesign({ niche, text }) {
  const slug = slugify(text);
  const nicheDir = path.join(OUTPUT_ROOT, niche);
  fs.mkdirSync(nicheDir, { recursive: true });

  console.log(`\n--- Processing [${niche}] "${text}" ---`);

  const { svg } = compileTypographyDesignSvg({
    text,
    niche,
    transparentBackground: true,
  });

  const svgPath = path.join(nicheDir, `${slug}.svg`);
  fs.writeFileSync(svgPath, svg, 'utf-8');
  console.log(`  SVG written -> ${path.relative(process.cwd(), svgPath)}`);

  const pngResult = await rasterizeSvgToPng({
    svg,
    widthPx: CANVAS_WIDTH_PX,
    heightPx: CANVAS_HEIGHT_PX,
    transparentBackground: true,
    filenamePrefix: slug,
  });
  const pngPath = path.join(nicheDir, `${slug}.png`);
  fs.copyFileSync(pngResult.absolutePath, pngPath);
  console.log(`  PNG written -> ${path.relative(process.cwd(), pngPath)}`);

  const metadata = generateEtsyMetadata({ niche, text });

  if (process.env.PUSH_TO_CANVA === 'true') {
    console.log('  PUSH_TO_CANVA=true — uploading to Canva...');
    const accessToken = await canva.getValidAccessToken({
      clientId: process.env.CANVA_CLIENT_ID,
      clientSecret: process.env.CANVA_CLIENT_SECRET,
    });

    const asset = await canva.uploadAsset({
      accessToken,
      filePath: pngPath,
      assetName: metadata.title,
    });

    const design = await canva.createDesignFromAsset({
      accessToken,
      assetId: asset.id,
      title: metadata.title,
    });

    metadata.canvaEditUrl = design.urls && design.urls.edit_url;
    console.log(`  Canva design created -> ${metadata.canvaEditUrl}`);
  }

  const metaPath = path.join(nicheDir, `${slug}.meta.json`);
  fs.writeFileSync(metaPath, JSON.stringify(metadata, null, 2), 'utf-8');
  console.log(`  Metadata written -> ${path.relative(process.cwd(), metaPath)}`);

  return { niche, text, slug };
}

async function main() {
  console.log(`Designer Worker starting — ${DESIGN_QUEUE.length} design(s) queued.`);
  fs.mkdirSync(OUTPUT_ROOT, { recursive: true });

  const succeeded = [];
  const failed = [];

  for (const design of DESIGN_QUEUE) {
    try {
      const result = await processDesign(design);
      succeeded.push(result);
    } catch (err) {
      console.error(`  FAILED [${design.niche}] "${design.text}": ${err.message}`);
      logError(design.niche, `"${design.text}" — ${err.stack || err.message}`);
      failed.push({ ...design, error: err.message });
    }
  }

  console.log('\n=== Designer Worker Summary ===');
  console.log(`Succeeded: ${succeeded.length}/${DESIGN_QUEUE.length}`);
  succeeded.forEach((s) => console.log(`  ✓ [${s.niche}] ${s.slug}`));
  console.log(`Failed: ${failed.length}/${DESIGN_QUEUE.length}`);
  failed.forEach((f) => console.log(`  ✗ [${f.niche}] "${f.text}" — ${f.error}`));
  console.log('================================\n');
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error('Designer Worker crashed:', err);
    process.exit(1);
  });
