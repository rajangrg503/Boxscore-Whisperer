const fs = require('fs');
const path = require('path');
const { STORAGE } = require('../config/constants');

const ROOT = path.join(__dirname, '..', '..');

function dirFor(storageKey) {
  return path.join(ROOT, STORAGE[storageKey]);
}

function ensureDirs() {
  Object.keys(STORAGE).forEach((key) => {
    const dir = dirFor(key);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  });
}

function sanitizeName(name) {
  return String(name).replace(/[^a-z0-9-_]/gi, '_').slice(0, 80);
}

function saveTemplate(name, schema) {
  const safeName = sanitizeName(name);
  const filePath = path.join(dirFor('TEMPLATES'), `${safeName}.json`);
  fs.writeFileSync(filePath, JSON.stringify(schema, null, 2), 'utf-8');
  return { filename: `${safeName}.json`, filePath };
}

function loadTemplate(name) {
  const safeName = sanitizeName(name);
  const filePath = path.join(dirFor('TEMPLATES'), `${safeName}.json`);
  if (!fs.existsSync(filePath)) return null;
  return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
}

function listTemplates() {
  const dir = dirFor('TEMPLATES');
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => f.replace(/\.json$/, ''));
}

function deleteTemplate(name) {
  const safeName = sanitizeName(name);
  const filePath = path.join(dirFor('TEMPLATES'), `${safeName}.json`);
  if (fs.existsSync(filePath)) {
    fs.unlinkSync(filePath);
    return true;
  }
  return false;
}

module.exports = {
  ensureDirs,
  saveTemplate,
  loadTemplate,
  listTemplates,
  deleteTemplate,
  dirFor,
};
