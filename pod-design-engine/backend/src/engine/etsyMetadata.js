const NICHE_METADATA = {
  real_estate_agents: {
    audience: 'real estate agents',
    tagPool: [
      'real estate agent shirt',
      'realtor gift',
      'real estate agent gift',
      'realtor shirt',
      'closing gift realtor',
      'real estate broker gift',
      'real estate quote shirt',
      'listing agent gift',
      'realtor life shirt',
      'real estate team shirt',
      'realtor swag',
      'house hustle shirt',
      'real estate era shirt',
    ],
    descriptionTemplate: (text) =>
      `"${text}" — a premium minimalist tee made for real estate agents who live the hustle. Soft, breathable unisex fit that reads professional at open houses and comfortable off the clock. A thoughtful gift for realtors, brokers, and closing-day celebrations.`,
  },
  aesthetic_services: {
    audience: 'lash techs and beauty professionals',
    tagPool: [
      'lash tech shirt',
      'esthetician gift',
      'lash tech gift',
      'beauty pro shirt',
      'aesthetician shirt',
      'lash artist shirt',
      'self care shirt',
      'beauty business shirt',
      'lash tech life',
      'spa owner gift',
      'skincare pro shirt',
      'brow tech gift',
      'beauty industry gift',
    ],
    descriptionTemplate: (text) =>
      `"${text}" — a trendy, neutral-toned tee designed for lash techs and beauty professionals. Soft unisex fit perfect for the studio, client days, or lounging between appointments. Makes a sweet gift for any esthetician or beauty business owner.`,
  },
  corporate_coaches: {
    audience: 'corporate coaches',
    tagPool: [
      'corporate coach shirt',
      'business coach gift',
      'motivational shirt',
      'coach gift shirt',
      'leadership shirt',
      'entrepreneur shirt',
      'mindset shirt',
      'hustle culture shirt',
      'consultant gift shirt',
      'coaching business shirt',
      'bold statement shirt',
      'career coach gift',
      'success mindset tee',
    ],
    descriptionTemplate: (text) =>
      `"${text}" — a bold, Swiss-inspired graphic tee for corporate coaches and consultants who mean business. Clean geometric type on a soft unisex fit, built to make a statement in the boardroom or the gym. A great gift for coaches, mentors, and entrepreneurs.`,
  },
};

function toTitleCase(str) {
  return String(str).replace(/\w\S*/g, (word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase());
}

/**
 * Generates Etsy-ready listing metadata for a compiled quote-tee design.
 * Returns exactly 13 tags (Etsy's per-listing tag limit).
 */
function generateEtsyMetadata({ niche, text }) {
  const meta = NICHE_METADATA[niche];
  if (!meta) {
    throw new Error(`No Etsy metadata configured for niche "${niche}"`);
  }

  const title = `${text} Shirt | Gift for ${toTitleCase(meta.audience)} | Unisex Graphic Tee`;
  const tags = meta.tagPool.slice(0, 13);
  const description = meta.descriptionTemplate(text);

  return {
    title,
    tags,
    description,
    niche,
    sourceText: text,
    generatedAt: new Date().toISOString(),
  };
}

module.exports = { generateEtsyMetadata, NICHE_METADATA };
