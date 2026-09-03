import { useEffect } from 'react';
import { Wand2, Loader2 } from 'lucide-react';
import { useDesignStore } from '../../store/designStore';
import api from '../../lib/api';

export default function ChurnerPanel() {
  const churnerInputs = useDesignStore((s) => s.churnerInputs);
  const setChurnerInput = useDesignStore((s) => s.setChurnerInput);
  const niches = useDesignStore((s) => s.niches);
  const palettes = useDesignStore((s) => s.palettes);
  const setOptions = useDesignStore((s) => s.setOptions);
  const isGenerating = useDesignStore((s) => s.isGenerating);
  const setIsGenerating = useDesignStore((s) => s.setIsGenerating);
  const setGenerationResult = useDesignStore((s) => s.setGenerationResult);
  const setCritique = useDesignStore((s) => s.setCritique);
  const setIsCritiquing = useDesignStore((s) => s.setIsCritiquing);

  useEffect(() => {
    api
      .getOptions()
      .then((data) => setOptions(data))
      .catch((err) => console.error('Failed to load niches/palettes', err));
  }, [setOptions]);

  const handleGenerate = async () => {
    setIsGenerating(true);
    setIsCritiquing(true);
    try {
      const result = await api.generate(churnerInputs);
      setGenerationResult(result);
      setCritique(result.critique);
    } catch (err) {
      console.error('Generation failed', err);
      alert(`Generation failed: ${err.response?.data?.error || err.message}`);
    } finally {
      setIsGenerating(false);
      setIsCritiquing(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-bold text-lg flex items-center gap-2">
        <Wand2 size={18} className="text-accent" />
        Design Churner
      </h2>

      <Field label="Niche">
        <select
          value={churnerInputs.niche}
          onChange={(e) => setChurnerInput('niche', e.target.value)}
          className="input-select"
        >
          {niches.length === 0 && <option>Loading…</option>}
          {niches.map((n) => (
            <option key={n.key} value={n.key}>
              {n.label}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Target Age">
        <input
          type="text"
          value={churnerInputs.targetAge}
          onChange={(e) => setChurnerInput('targetAge', e.target.value)}
          placeholder="e.g. 3"
          className="input-text"
        />
      </Field>

      <Field label="Central Element">
        <input
          type="text"
          value={churnerInputs.element}
          onChange={(e) => setChurnerInput('element', e.target.value)}
          placeholder="e.g. Dinosaur"
          className="input-text"
        />
      </Field>

      <Field label="Display Text">
        <input
          type="text"
          value={churnerInputs.text}
          onChange={(e) => setChurnerInput('text', e.target.value)}
          placeholder='e.g. "Three-Rex"'
          className="input-text"
        />
      </Field>

      <Field label="Palette Override (optional)">
        <select
          value={churnerInputs.paletteOverride}
          onChange={(e) => setChurnerInput('paletteOverride', e.target.value)}
          className="input-select"
        >
          <option value="">Auto (from niche)</option>
          {palettes.map((p) => (
            <option key={p.key} value={p.key}>
              {p.label}
            </option>
          ))}
        </select>
      </Field>

      <label className="flex items-center gap-2 text-sm text-gray-300">
        <input
          type="checkbox"
          checked={churnerInputs.transparentBackground}
          onChange={(e) => setChurnerInput('transparentBackground', e.target.checked)}
        />
        Transparent background
      </label>

      <button
        onClick={handleGenerate}
        disabled={isGenerating || !churnerInputs.text}
        className="mt-2 flex items-center justify-center gap-2 bg-accent hover:bg-accentSoft transition-colors rounded-lg py-2.5 font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isGenerating ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
        {isGenerating ? 'Generating…' : 'Generate Design'}
      </button>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-gray-400 text-xs uppercase tracking-wide">{label}</span>
      {children}
    </label>
  );
}
