import { Download, FileImage, Loader2 } from 'lucide-react';
import { useDesignStore } from '../../store/designStore';
import api from '../../lib/api';

export default function Toolbar() {
  const churnerInputs = useDesignStore((s) => s.churnerInputs);
  const currentSvg = useDesignStore((s) => s.currentSvg);
  const isExporting = useDesignStore((s) => s.isExporting);
  const setIsExporting = useDesignStore((s) => s.setIsExporting);
  const setLastExport = useDesignStore((s) => s.setLastExport);
  const lastExport = useDesignStore((s) => s.lastExport);

  const handleExport = async (format) => {
    if (!currentSvg) return;
    setIsExporting(true);
    try {
      const result = await api.exportDesign({ ...churnerInputs, format });
      setLastExport(result.files);
    } catch (err) {
      console.error('Export failed', err);
      alert(`Export failed: ${err.response?.data?.error || err.message}`);
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="w-full flex items-center justify-between bg-panel rounded-lg px-4 py-2">
      <span className="text-sm text-gray-400">
        {currentSvg ? 'Design ready' : 'No design yet'}
      </span>

      <div className="flex gap-2">
        <button
          disabled={!currentSvg || isExporting}
          onClick={() => handleExport('png')}
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-panelLight hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {isExporting ? <Loader2 size={14} className="animate-spin" /> : <FileImage size={14} />}
          Export PNG (300dpi)
        </button>
        <button
          disabled={!currentSvg || isExporting}
          onClick={() => handleExport('svg')}
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-panelLight hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <Download size={14} />
          Export SVG
        </button>
      </div>

      {lastExport && (
        <div className="absolute mt-16 text-xs text-green-400">
          {lastExport.png && (
            <a href={`${api.BASE_URL}${lastExport.png.publicPath}`} target="_blank" rel="noreferrer" className="underline mr-3">
              Open PNG
            </a>
          )}
          {lastExport.svg && (
            <a href={`${api.BASE_URL}${lastExport.svg.publicPath}`} target="_blank" rel="noreferrer" className="underline">
              Open SVG
            </a>
          )}
        </div>
      )}
    </div>
  );
}
