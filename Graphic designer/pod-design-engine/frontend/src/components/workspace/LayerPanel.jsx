import { Eye, EyeOff, Layers } from 'lucide-react';

export default function LayerPanel({ layers, selectedId, onSelect, onToggleVisibility }) {
  return (
    <div className="w-56 bg-panel rounded-lg p-3 flex flex-col gap-2 h-fit">
      <div className="flex items-center gap-2 text-gray-300 text-sm font-semibold mb-1">
        <Layers size={16} />
        Layers
      </div>

      {layers.length === 0 && (
        <p className="text-xs text-gray-500">No layers yet — generate a design to populate this list.</p>
      )}

      {layers.map((layer) => (
        <div
          key={layer.id}
          onClick={() => onSelect(layer.id)}
          className={`flex items-center justify-between px-2 py-1.5 rounded cursor-pointer text-xs transition-colors ${
            selectedId === layer.id ? 'bg-accent/20 text-accentSoft' : 'hover:bg-panelLight text-gray-300'
          }`}
        >
          <span className="truncate">{layer.name}</span>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleVisibility(layer.id);
            }}
            className="text-gray-400 hover:text-white shrink-0 ml-2"
            title={layer.visible ? 'Hide layer' : 'Show layer'}
          >
            {layer.visible ? <Eye size={14} /> : <EyeOff size={14} />}
          </button>
        </div>
      ))}
    </div>
  );
}
