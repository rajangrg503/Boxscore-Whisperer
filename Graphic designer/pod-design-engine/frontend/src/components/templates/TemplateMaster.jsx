import { useEffect, useState } from 'react';
import { Save, FolderOpen, Trash2, LayoutTemplate } from 'lucide-react';
import { useDesignStore } from '../../store/designStore';
import api from '../../lib/api';

export default function TemplateMaster() {
  const churnerInputs = useDesignStore((s) => s.churnerInputs);
  const setChurnerInput = useDesignStore((s) => s.setChurnerInput);
  const currentLayout = useDesignStore((s) => s.currentLayout);
  const currentStyle = useDesignStore((s) => s.currentStyle);
  const savedTemplates = useDesignStore((s) => s.savedTemplates);
  const setSavedTemplates = useDesignStore((s) => s.setSavedTemplates);

  const [templateName, setTemplateName] = useState('');
  const [busy, setBusy] = useState(false);

  const refreshList = async () => {
    try {
      const { templates } = await api.listTemplates();
      setSavedTemplates(templates);
    } catch (err) {
      console.error('Failed to list templates', err);
    }
  };

  useEffect(() => {
    refreshList();
  }, []);

  const handleSave = async () => {
    if (!templateName.trim()) return;
    setBusy(true);
    try {
      await api.saveTemplate(templateName.trim(), {
        churnerInputs,
        layout: currentLayout,
        style: currentStyle,
        savedAt: new Date().toISOString(),
      });
      setTemplateName('');
      await refreshList();
    } catch (err) {
      alert(`Save failed: ${err.response?.data?.error || err.message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleLoad = async (name) => {
    setBusy(true);
    try {
      const { schema } = await api.getTemplate(name);
      if (schema.churnerInputs) {
        Object.entries(schema.churnerInputs).forEach(([key, value]) => {
          setChurnerInput(key, value);
        });
      }
    } catch (err) {
      alert(`Load failed: ${err.response?.data?.error || err.message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (name) => {
    setBusy(true);
    try {
      await api.deleteTemplate(name);
      await refreshList();
    } catch (err) {
      alert(`Delete failed: ${err.response?.data?.error || err.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <h2 className="font-bold text-lg flex items-center gap-2">
        <LayoutTemplate size={18} className="text-accent" />
        Template Master
      </h2>

      <div className="flex gap-2">
        <input
          type="text"
          value={templateName}
          onChange={(e) => setTemplateName(e.target.value)}
          placeholder="Template name…"
          className="input-text flex-1"
        />
        <button
          onClick={handleSave}
          disabled={busy || !templateName.trim() || !currentLayout}
          className="flex items-center gap-1 text-xs px-3 rounded bg-accent hover:bg-accentSoft disabled:opacity-40 disabled:cursor-not-allowed"
          title={!currentLayout ? 'Generate a design first' : 'Save current design as template'}
        >
          <Save size={14} /> Save
        </button>
      </div>

      <div className="flex flex-col gap-1.5 max-h-64 overflow-y-auto">
        {savedTemplates.length === 0 && (
          <p className="text-xs text-gray-500">No saved templates yet.</p>
        )}
        {savedTemplates.map((name) => (
          <div
            key={name}
            className="flex items-center justify-between bg-panelLight rounded px-2.5 py-1.5 text-xs"
          >
            <span className="truncate">{name}</span>
            <div className="flex gap-2 shrink-0 ml-2">
              <button
                onClick={() => handleLoad(name)}
                disabled={busy}
                className="text-gray-400 hover:text-accentSoft"
                title="Load into churner"
              >
                <FolderOpen size={14} />
              </button>
              <button
                onClick={() => handleDelete(name)}
                disabled={busy}
                className="text-gray-400 hover:text-red-400"
                title="Delete template"
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
