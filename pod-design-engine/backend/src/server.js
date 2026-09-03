require('dotenv').config();
const express = require('express');
const cors = require('cors');
const morgan = require('morgan');
const path = require('path');

const { STORAGE } = require('./config/constants');
const { ensureDirs } = require('./utils/fileManager');

const generateRoutes = require('./routes/generate.routes');
const exportRoutes = require('./routes/export.routes');
const templateRoutes = require('./routes/templates.routes');
const mentorRoutes = require('./routes/mentor.routes');
const canvaRoutes = require('./routes/canva.routes');

const app = express();
const PORT = process.env.PORT || 4000;

// --- Ensure local storage dirs exist on boot ---
ensureDirs();

// --- Core middleware ---
app.use(cors({ origin: process.env.CLIENT_ORIGIN || 'http://localhost:5173' }));
app.use(express.json({ limit: '25mb' })); // raw SVG asset payloads can be large
app.use(morgan('dev'));

// Serve generated exports + source assets statically
app.use('/exports', express.static(path.join(__dirname, '..', STORAGE.EXPORTS)));
app.use('/assets', express.static(path.join(__dirname, '..', STORAGE.ASSETS)));

// --- API routes ---
app.use('/api/generate', generateRoutes);
app.use('/api/export', exportRoutes);
app.use('/api/templates', templateRoutes);
app.use('/api/mentor', mentorRoutes);
app.use('/api/canva', canvaRoutes);

app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', service: 'pod-design-engine-backend' });
});

// --- 404 handler ---
app.use((req, res) => {
  res.status(404).json({ error: `No route for ${req.method} ${req.originalUrl}` });
});

// --- Central error handler ---
app.use((err, req, res, next) => {
  console.error(err.stack);
  res.status(err.status || 500).json({ error: err.message || 'Internal server error' });
});

app.listen(PORT, () => {
  console.log(`🎨 Design Engine backend running at http://localhost:${PORT}`);
});
