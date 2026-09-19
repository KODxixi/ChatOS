import React, { useState } from 'react';
import { ChatOSProvider, useChatOS } from './context/ChatOSContext';
import { Sidebar } from './components/Sidebar';
import { ConversationsView } from './components/views/ConversationsView';
import { TodosView } from './components/views/TodosView';
import { ProjectsView } from './components/views/ProjectsView';
import { LibraryView } from './components/views/LibraryView';
import { MindmapView } from './components/views/mindmap/MindmapView';
import { RightDetailPanel } from './components/RightDetailPanel';
import { CommandPalette } from './components/CommandPalette';
import { AuxModals } from './components/AuxModals';
import { ThinkingCopilotModal } from './components/ThinkingCopilotModal';
import { CheckCircle, AlertCircle } from 'lucide-react';

const UNAVAILABLE_IMAGE = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96"><rect width="96" height="96" rx="12" fill="#242426"/><path d="M25 28h46v40H25zM25 60l15-16 13 14 7-8 11 12" fill="none" stroke="#a1a1aa" stroke-width="3"/><circle cx="61" cy="39" r="4" fill="#a1a1aa"/></svg>')}`;

const ChatOSMain: React.FC = () => {
  const { activeWorkspace, isInspectorOpen, toastMessage } = useChatOS();
  const [isThinkingModalOpen, setIsThinkingModalOpen] = useState<boolean>(false);

  return (
    <div
      className="chatos-app gary-ui flex h-screen w-screen overflow-hidden bg-[#0b0f17] text-[#e2e8f0] font-sans select-none"
      data-gary-ui-root
      data-gary-application-mode="web-ui"
      data-gary-page-mode="data-page"
      data-gary-theme="dark"
      data-gary-material="regular"
      data-gary-density="balanced"
      data-gary-background="light-rays"
      data-gary-light-scope
      onErrorCapture={event => {
        const image = event.target;
        if (image instanceof HTMLImageElement && image.src !== UNAVAILABLE_IMAGE) {
          image.title = `${image.alt || '图片'}：原图未加载，显示本地占位图`;
          image.src = UNAVAILABLE_IMAGE;
        }
      }}
    >
      <div className="gary-scene chatos-scene" data-gary-scene-engine="light-rays" aria-hidden="true" />

      {/* 1. Left Sidebar */}
      <Sidebar onOpenThinking={() => setIsThinkingModalOpen(true)} />

      {/* 2. Central Workspace + Inspector */}
      <div className="chatos-content flex-1 flex flex-col min-w-0 h-full overflow-hidden">
        {/* Workspace Body: Center Grid/Board + Right Detail Inspector */}
        <div className="chatos-workspace-body flex-1 flex overflow-hidden relative">
          {/* Main Central Workspace */}
          <main className="chatos-main flex-1 flex flex-col min-w-0 h-full overflow-hidden bg-[#0c1018]">
            {activeWorkspace === 'today' && <ConversationsView mode="focus" />}
            {activeWorkspace === 'conversations' && <ConversationsView mode="all" />}
            {activeWorkspace === 'todos' && <TodosView />}
            {activeWorkspace === 'projects' && <ProjectsView />}
            {activeWorkspace === 'library' && <LibraryView />}
            {activeWorkspace === 'mindmap' && <MindmapView />}
          </main>

          {/* 3. Right Detail Panel (Inspector: 2-stage digest + scrollable full reading area) */}
          {activeWorkspace !== 'mindmap' && <RightDetailPanel />}
        </div>
      </div>

      {/* Notification Toast */}
      {toastMessage && (
        <div
          id="chatos-toast"
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 rounded-xl bg-[#151d2d] border border-[#273854] text-slate-100 text-xs font-medium shadow-2xl flex items-center gap-2.5 animate-in fade-in slide-in-from-bottom-3 duration-200"
        >
          {toastMessage.includes('⚠️') ? (
            <AlertCircle className="w-4 h-4 text-amber-400 flex-shrink-0" />
          ) : (
            <CheckCircle className="w-4 h-4 text-emerald-400 flex-shrink-0" />
          )}
          <span>{toastMessage}</span>
        </div>
      )}

      {/* Modals & Dialogs */}
      <CommandPalette />
      <AuxModals />
      <ThinkingCopilotModal isOpen={isThinkingModalOpen} onClose={() => setIsThinkingModalOpen(false)} />
    </div>
  );
};

export function App() {
  return (
    <ChatOSProvider>
      <ChatOSMain />
    </ChatOSProvider>
  );
}

export default App;
