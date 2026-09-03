const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const TOKENS_PATH = path.join(__dirname, '..', '..', 'storage', 'canva-tokens.json');

const CANVA_AUTHORIZE_URL = 'https://www.canva.com/api/oauth/authorize';
const CANVA_TOKEN_URL = 'https://api.canva.com/rest/v1/oauth/token';
const CANVA_ASSET_UPLOADS_URL = 'https://api.canva.com/rest/v1/asset-uploads';
const CANVA_DESIGNS_URL = 'https://api.canva.com/rest/v1/designs';

const SCOPES = 'asset:read asset:write design:content:read design:content:write design:meta:read';

// In-memory store of pending PKCE verifiers, keyed by the `state` param.
const pendingAuth = new Map();

function base64url(buffer) {
  return buffer.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function generatePkcePair() {
  const codeVerifier = base64url(crypto.randomBytes(64));
  const codeChallenge = base64url(crypto.createHash('sha256').update(codeVerifier).digest());
  return { codeVerifier, codeChallenge };
}

function buildAuthorizationUrl({ clientId, redirectUri }) {
  const { codeVerifier, codeChallenge } = generatePkcePair();
  const state = base64url(crypto.randomBytes(32));

  pendingAuth.set(state, codeVerifier);

  const url = new URL(CANVA_AUTHORIZE_URL);
  url.searchParams.set('client_id', clientId);
  url.searchParams.set('redirect_uri', redirectUri);
  url.searchParams.set('response_type', 'code');
  url.searchParams.set('scope', SCOPES);
  url.searchParams.set('code_challenge', codeChallenge);
  url.searchParams.set('code_challenge_method', 'S256');
  url.searchParams.set('state', state);

  return url.toString();
}

function consumePendingAuth(state) {
  const codeVerifier = pendingAuth.get(state);
  pendingAuth.delete(state);
  return codeVerifier;
}

function basicAuthHeader(clientId, clientSecret) {
  return 'Basic ' + Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
}

async function exchangeCodeForTokens({ clientId, clientSecret, code, codeVerifier, redirectUri }) {
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code_verifier: codeVerifier,
    code,
    redirect_uri: redirectUri,
  });

  const res = await fetch(CANVA_TOKEN_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      Authorization: basicAuthHeader(clientId, clientSecret),
    },
    body: body.toString(),
  });

  if (!res.ok) {
    throw new Error(`Canva token exchange failed (${res.status}): ${await res.text()}`);
  }

  return res.json();
}

async function refreshAccessToken({ clientId, clientSecret, refreshToken }) {
  const body = new URLSearchParams({
    grant_type: 'refresh_token',
    refresh_token: refreshToken,
  });

  const res = await fetch(CANVA_TOKEN_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      Authorization: basicAuthHeader(clientId, clientSecret),
    },
    body: body.toString(),
  });

  if (!res.ok) {
    throw new Error(`Canva token refresh failed (${res.status}): ${await res.text()}`);
  }

  return res.json();
}

function saveTokens(tokenData) {
  const record = {
    access_token: tokenData.access_token,
    refresh_token: tokenData.refresh_token,
    expires_at: Date.now() + tokenData.expires_in * 1000,
  };
  fs.mkdirSync(path.dirname(TOKENS_PATH), { recursive: true });
  fs.writeFileSync(TOKENS_PATH, JSON.stringify(record, null, 2), 'utf-8');
  return record;
}

function loadTokens() {
  if (!fs.existsSync(TOKENS_PATH)) return null;
  return JSON.parse(fs.readFileSync(TOKENS_PATH, 'utf-8'));
}

function isConnected() {
  return loadTokens() !== null;
}

async function getValidAccessToken({ clientId, clientSecret }) {
  const tokens = loadTokens();
  if (!tokens) {
    throw new Error('Canva is not connected — no tokens on file');
  }

  if (tokens.expires_at - Date.now() > 60 * 1000) {
    return tokens.access_token;
  }

  const refreshed = await refreshAccessToken({
    clientId,
    clientSecret,
    refreshToken: tokens.refresh_token,
  });

  // Canva may not always return a new refresh_token — keep the old one if so.
  const saved = saveTokens({
    access_token: refreshed.access_token,
    refresh_token: refreshed.refresh_token || tokens.refresh_token,
    expires_in: refreshed.expires_in,
  });

  return saved.access_token;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function uploadAsset({ accessToken, filePath, assetName }) {
  const fileBuffer = fs.readFileSync(filePath);
  // Canva rejects the whole upload-metadata header (not just the name) if
  // the asset name exceeds 50 characters — truncate defensively.
  const truncatedName = String(assetName).slice(0, 50);
  const metadata = { name_base64: base64url(Buffer.from(truncatedName)) };

  const uploadRes = await fetch(CANVA_ASSET_UPLOADS_URL, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/octet-stream',
      'Asset-Upload-Metadata': JSON.stringify(metadata),
    },
    body: fileBuffer,
  });

  if (!uploadRes.ok) {
    throw new Error(`Canva asset upload failed (${uploadRes.status}): ${await uploadRes.text()}`);
  }

  const { job } = await uploadRes.json();
  const jobId = job.id;

  for (let attempt = 0; attempt < 20; attempt += 1) {
    await sleep(1000);

    const statusRes = await fetch(`${CANVA_ASSET_UPLOADS_URL}/${jobId}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    if (!statusRes.ok) {
      throw new Error(`Canva asset upload status check failed (${statusRes.status}): ${await statusRes.text()}`);
    }

    const { job: polledJob } = await statusRes.json();

    if (polledJob.status === 'success') {
      return polledJob.asset;
    }
    if (polledJob.status === 'failed') {
      throw new Error(`Canva asset upload job failed: ${JSON.stringify(polledJob.error || polledJob)}`);
    }
  }

  throw new Error('Canva asset upload timed out waiting for job completion');
}

async function createDesignFromAsset({ accessToken, assetId, title }) {
  const res = await fetch(CANVA_DESIGNS_URL, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ asset_id: assetId, title }),
  });

  if (!res.ok) {
    throw new Error(`Canva design creation failed (${res.status}): ${await res.text()}`);
  }

  const data = await res.json();
  return data.design;
}

module.exports = {
  generatePkcePair,
  buildAuthorizationUrl,
  consumePendingAuth,
  exchangeCodeForTokens,
  refreshAccessToken,
  saveTokens,
  loadTokens,
  isConnected,
  getValidAccessToken,
  uploadAsset,
  createDesignFromAsset,
};
