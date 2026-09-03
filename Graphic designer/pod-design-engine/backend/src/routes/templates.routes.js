const express = require('express');
const router = express.Router();
const { saveTemplate, loadTemplate, listTemplates, deleteTemplate } = require('../utils/fileManager');

// GET /api/templates — list all saved template names
router.get('/', (req, res) => {
  res.json({ templates: listTemplates() });
});

// GET /api/templates/:name — fetch one saved template's schema
router.get('/:name', (req, res) => {
  const schema = loadTemplate(req.params.name);
  if (!schema) return res.status(404).json({ error: 'Template not found' });
  res.json({ name: req.params.name, schema });
});

// POST /api/templates — save a new (or overwrite an existing) template
// Body: { name: string, schema: object }
router.post('/', (req, res) => {
  const { name, schema } = req.body;
  if (!name || !schema) return res.status(400).json({ error: 'name and schema are required' });
  const { filename } = saveTemplate(name, schema);
  res.status(201).json({ message: 'Template saved', filename });
});

// DELETE /api/templates/:name
router.delete('/:name', (req, res) => {
  const deleted = deleteTemplate(req.params.name);
  if (!deleted) return res.status(404).json({ error: 'Template not found' });
  res.json({ message: 'Template deleted' });
});

module.exports = router;
