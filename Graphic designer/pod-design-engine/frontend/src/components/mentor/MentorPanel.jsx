import { Sparkles, CheckCircle2, AlertTriangle, Info } from 'lucide-react';
import { useDesignStore } from '../../store/designStore';

const severityConfig = {
  pass: { icon: CheckCircle2, className: 'text-green-400' },
  suggestion: { icon: Info, className: 'text-blue-400' },
  warning: { icon: AlertTriangle, className: 'text-amber-400' },
};

function ScoreBar({ label, score }) {
  const color = score >= 85 ? 'bg-green-500' : score >= 65 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-xs text-gray-400">
        <span>{label}</span>
        <span>{score}/100</span>
      </div>
      <div className="w-full h-1.5 bg-panelLight rounded-full overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${score}%` }} />
      </div>
    </div>
  );
}

export default function MentorPanel() {
  const critique = useDesignStore((s) => s.critique);
  const isCritiquing = useDesignStore((s) => s.isCritiquing);

  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-bold text-lg flex items-center gap-2">
        <Sparkles size={18} className="text-accent" />
        Canva Mentor
      </h2>

      {isCritiquing && (
        <p className="text-sm text-gray-400 animate-pulse">Evaluating hierarchy, margins, contrast…</p>
      )}

      {!critique && !isCritiquing && (
        <p className="text-sm text-gray-500">
          Generate a design and the Mentor will grade it on typography hierarchy, breathing room,
          contrast, and alignment — with specific fixes to make it look premium.
        </p>
      )}

      {critique && (
        <>
          <div className="bg-panelLight rounded-lg p-3 flex flex-col gap-1">
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-bold text-accentSoft">{critique.overallScore}</span>
              <span className="text-gray-500 text-sm">/ 100</span>
            </div>
            <p className="text-xs text-gray-400">{critique.verdict}</p>
          </div>

          <div className="flex flex-col gap-2.5">
            <ScoreBar label="Breathing Room" score={critique.scores.breathingRoom} />
            <ScoreBar label="Typography Hierarchy" score={critique.scores.typographyHierarchy} />
            <ScoreBar label="Contrast" score={critique.scores.contrast} />
            <ScoreBar label="Alignment" score={critique.scores.alignment} />
          </div>

          <div className="flex flex-col gap-2 mt-1">
            {critique.notes.map((note, idx) => {
              const cfg = severityConfig[note.severity] || severityConfig.suggestion;
              const Icon = cfg.icon;
              return (
                <div key={idx} className="flex gap-2 text-xs bg-panelLight/60 rounded-lg p-2.5">
                  <Icon size={14} className={`shrink-0 mt-0.5 ${cfg.className}`} />
                  <div>
                    <p className="font-semibold text-gray-300">{note.category}</p>
                    <p className="text-gray-400 mt-0.5">{note.note}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
