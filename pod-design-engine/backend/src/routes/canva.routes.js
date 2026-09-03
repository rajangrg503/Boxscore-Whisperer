const express = require('express');
const path = require('path');
const router = express.Router();

const canva = require('../engine/canvaClient');
const { dirFor } = require('../utils/fileManager');

/**
 * GET /api/canva/status
 */
router.get('/status', (req, res) => {
  res.json({ connected: canva.isConnected() });
});

/**
 * GET /api/canva/connect
 * Redirects the browser into Canva's OAuth consent screen.
 */
router.get('/connect', (req, res) => {
  const authUrl = canva.buildAuthorizationUrl({
    clientId: process.env.CANVA_CLIENT_ID,
    redirectUri: process.env.CANVA_REDIRECT_URI,
  });
  res.redirect(authUrl);
});

/**
 * GET /api/canva/callback
 * Canva redirects here with ?code=&state= (or ?error=) after user consent.
 */
router.get('/callback', async (req, res) => {
  const clientOrigin = process.env.CLIENT_ORIGIN || 'http://localhost:5173';
  const { code, state, error } = req.query;

  try {
    if (error || !code || !state) {
      throw new Error(error || 'Missing code/state from Canva callback');
    }

    const codeVerifier = canva.consumePendingAuth(state);
    if (!codeVerifier) {
      throw new Error('Unknown or expired OAuth state');
    }

    const tokenData = await canva.exchangeCodeForTokens({
      clientId: process.env.CANVA_CLIENT_ID,
      clientSecret: process.env.CANVA_CLIENT_SECRET,
      code,
      codeVerifier,
      redirectUri: process.env.CANVA_REDIRECT_URI,
    });

    canva.saveTokens(tokenData);

    res.redirect(`${clientOrigin}?canva=connected`);
  } catch (err) {
    console.error('Canva OAuth callback failed:', err.message);
    res.redirect(`${clientOrigin}?canva=denied`);
  }
});

/**
 * POST /api/canva/push
 * Body: { filename, title }
 * Uploads an existing export from storage/exports/ to Canva as an asset
 * and creates a new design from it.
 */
router.post('/push', async (req, res, next) => {
  try {
    const { filename, title } = req.body;

    if (!filename) {
      return res.status(400).json({ error: 'filename is required' });
    }

    const filePath = path.join(dirFor('EXPORTS'), filename);

    const accessToken = await canva.getValidAccessToken({
      clientId: process.env.CANVA_CLIENT_ID,
      clientSecret: process.env.CANVA_CLIENT_SECRET,
    });

    const asset = await canva.uploadAsset({
      accessToken,
      filePath,
      assetName: title || filename,
    });

    const design = await canva.createDesignFromAsset({
      accessToken,
      assetId: asset.id,
      title: title || filename,
    });

    res.status(201).json({
      message: 'Pushed to Canva',
      design: {
        id: design.id,
        editUrl: design.urls && design.urls.edit_url,
        viewUrl: design.urls && design.urls.view_url,
      },
    });
  } catch (err) {
    next(err);
  }
});

module.exports = router;
