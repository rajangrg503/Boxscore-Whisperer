import CanvasWorkspace from './components/workspace/CanvasWorkspace';
import ChurnerPanel from './components/churner/ChurnerPanel';
import MentorPanel from './components/mentor/MentorPanel';
import TemplateMaster from './components/templates/TemplateMaster';

function App() {
  return (
    <div className="min-h-screen bg-surface flex flex-col">
      <header className="border-b border-black/40 px-6 py-3 flex items-center justify-between">
        <h1 className="font-bold text-lg tracking-tight">
          POD Design Engine <span className="text-accent">·</span> Local
        </h1>
        <span className="text-xs text-gray-500">4500×5400px @ 300 DPI</span>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <aside className="w-72 bg-panel p-4 border-r border-black/40 overflow-y-auto flex flex-col gap-8">
          <ChurnerPanel />
          <TemplateMaster />
        </aside>

        <main className="flex-1 flex items-center justify-center p-8 overflow-auto">
          <CanvasWorkspace />
        </main>

        <aside className="w-80 bg-panel p-4 border-l border-black/40 overflow-y-auto">
          <MentorPanel />
        </aside>
      </div>
    </div>
  );
}

export default App;
