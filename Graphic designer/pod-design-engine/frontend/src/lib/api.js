import axios from 'axios';

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:4000';

const client = axios.create({ baseURL: BASE_URL });

export const api = {
  // --- Churner ---
  generate: (payload) => client.post('/api/generate', payload).then((r) => r.data),
  getOptions: () => client.get('/api/generate/options').then((r) => r.data),

  // --- Export ---
  exportDesign: (payload) => client.post('/api/export', payload).then((r) => r.data),

  // --- Mentor ---
  critique: (payload) => client.post('/api/mentor/critique', payload).then((r) => r.data),

  // --- Templates ---
  listTemplates: () => client.get('/api/templates').then((r) => r.data),
  getTemplate: (name) => client.get(`/api/templates/${name}`).then((r) => r.data),
  saveTemplate: (name, schema) =>
    client.post('/api/templates', { name, schema }).then((r) => r.data),
  deleteTemplate: (name) => client.delete(`/api/templates/${name}`).then((r) => r.data),

  // --- Health ---
  health: () => client.get('/api/health').then((r) => r.data),

  BASE_URL,
};

export default api;
