const express = require('express');
const router = express.Router();
const { critiqueDesign } = require('../engine/mentorEngine');

/**
 * POST /api/mentor/critique
 * Body: { layout: {...}, style: {...} }
 *
 * Accepts the exact `layout` and `style` objects returned by /api/generate
 * (or a frontend-reconstructed equivalent after the user manually drags/
 * resizes layers in the Fabric.js workspace) and re-runs the design
 * critique heuristics. This lets the Mentor Panel re-evaluate live as
 * the user edits, without recompiling the SVG.
 */
router.post('/critique', (req, res, next) => {
  try {
    const { layout, style } = req.body;
    if (!layout || !style) {
      return res.status(400).json({ error: 'layout and style objects are required' });
    }
    const critique = critiqueDesign({ layout, style });
    res.json({ critique });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
