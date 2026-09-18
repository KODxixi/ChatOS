import React from 'react';
import { Search, Command, AlertCircle, CheckCircle2, Plus } from 'lucide-react';
import { useChatOS } from '../context/ChatOSContext';

interface HeaderProps {
  onOpenCreateModal?: () => void;
}

export const Header: React.FC<HeaderProps> = ({ onOpenCreateModal }) => {
  const {
    activeWorkspace,
    setIsCommandPaletteOpen,
    runIncrementalOrganize,
    isSyncing,
    modelDiagnosticStatus,
    setModelDiagnosticStatus,
    showToast,
  } = useChatOS();

  const getWorkspaceTitle = () => {
    switch (activeWorkspace) {
      case 'conversations':
        return {
          title: '会话工作区',
          subtitle: '默认聚焦星标与挂起事项 · 依据可追溯 · 随时查看全部会话',
        };
      case 'todos':
        return {
          title: '待办工作区 · 演示',
          subtitle: '草稿箱 / 待处理 / 进行中 / 已完成 · 覆盖我负责、我承诺与我跟进',
        };
      case 'projects':
        return {
          title: '项目工作区 · 演示',
          subtitle: '跨会话证据聚合 · 专属任务与资料联动 · 隔离私人闲聊',
        };
      case 'library':
        return {
          title: '资料库工作区 · 演示',
          subtitle: '图片缩略图与文件归档 · 内容哈希去重 · 区分待确认与缺失文件',
        };
      case 'mindmap':
        return {
          title: '思维图谱工作区 · 演示',
          subtitle: '双向链接知识网与时序演化网络 · 会话、项目与行动动态推演',
        };
    }
  };

  const { title, subtitle } = getWorkspaceTitle();

  const toggleModelSim = () => {
    if (modelDiagnosticStatus === 'normal') {
      setModelDiagnosticStatus('simulate_offline');
      showToast('已进入模型故障演示；不会执行真实回滚或缓存操作');
    } else {
      setModelDiagnosticStatus('normal');
      showToast('已退出故障演示；本地模型将在推演时实时检查');
    }
  };

  return (
    <header
      className="chatos-header gary-web-bar gary-ultrathin select-none"
      aria-label="工作区导航与操作"
      data-has-secondary={activeWorkspace === 'conversations' ? 'true' : undefined}
    >
      <div className="chatos-control-center">
        <div className="chatos-header-primary">
          <div className="chatos-header-identity chatos-control-tile flex flex-col">
            <div className="flex items-center gap-2.5 min-w-0">
              <h2 className="chatos-workspace-title text-base font-bold text-white">{title}</h2>
              <span className="chatos-workspace-context hidden sm:inline-flex text-[11px] font-mono">
                混合数据 · 含演示
              </span>
            </div>
            <p className="chatos-workspace-subtitle text-xs text-slate-400 hidden md:block">{subtitle}</p>
          </div>

          <button
            id="global-search-trigger"
            onClick={() => setIsCommandPaletteOpen(true)}
            className="chatos-global-search chatos-control-tile group"
            title="打开全局搜索与操作"
          >
            <Search className="w-4 h-4" />
            <span className="chatos-global-search-copy">搜索会话、任务、项目、资料...</span>
            <kbd className="chatos-command-key hidden sm:flex">
              <Command className="w-3 h-3" /> K
            </kbd>
          </button>

          {activeWorkspace !== 'conversations' && <div className="chatos-header-actions">
            <button
              id="model-sim-toggle"
              onClick={toggleModelSim}
              className="chatos-control-action chatos-control-tile"
              data-state={modelDiagnosticStatus === 'simulate_offline' ? 'warning' : 'ready'}
              title="Thinking Mode 使用本机 LM Studio；点击切换故障演示"
            >
              {modelDiagnosticStatus === 'simulate_offline' ? (
                <AlertCircle className="w-4 h-4" />
              ) : (
                <CheckCircle2 className="w-4 h-4" />
              )}
              <span>{modelDiagnosticStatus === 'simulate_offline' ? '仿真离线' : '本地模型'}</span>
            </button>

            {onOpenCreateModal && (
              <button
                id="workspace-primary-add-btn"
                onClick={onOpenCreateModal}
                className="chatos-control-action chatos-control-tile is-primary"
              >
                <Plus className="w-4 h-4" />
                <span>
                  {activeWorkspace === 'todos'
                    ? '新建待办'
                    : activeWorkspace === 'projects'
                    ? '创建项目'
                    : '快速新建'}
                </span>
              </button>
            )}
          </div>}
        </div>

        {activeWorkspace === 'conversations' && (
          <div id="conversation-toolbar-host" className="chatos-header-secondary" />
        )}
      </div>
    </header>
  );
};
