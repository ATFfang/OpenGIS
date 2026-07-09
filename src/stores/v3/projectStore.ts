/**
 * ProjectStore — 当前工程（workspace + 元数据）
 *
 * workspace 和 project 是一体的：打开一个 workspace 目录就是打开一个 project。
 */

import { create } from 'zustand';

export interface ProjectMeta {
  workspace_path: string;
  /** `.opengis/` 绝对路径，由 Electron Main 提供。 */
  opengis_dir: string;
  /** 工程名，默认取目录名。 */
  project_name: string;
  opened_at: number;
  /** 当前 workspace git snapshot 的 HEAD commit（可选）。 */
  head_commit?: string;
}

interface ProjectState {
  current: ProjectMeta | null;

  // actions
  open: (meta: Omit<ProjectMeta, 'opened_at'> & { opened_at?: number }) => ProjectMeta;
  close: () => string | null; // 返回被关闭的 workspace_path
  updateHead: (commit: string | undefined) => void;
  get: () => ProjectMeta | null;
  isOpen: () => boolean;
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  current: null,

  open: (meta) => {
    const project: ProjectMeta = {
      opened_at: Date.now(),
      ...meta,
    };
    set({ current: project });
    return project;
  },

  close: () => {
    const path = get().current?.workspace_path ?? null;
    set({ current: null });
    return path;
  },

  updateHead: (commit) => {
    const cur = get().current;
    if (!cur) return;
    set({ current: { ...cur, head_commit: commit } });
  },

  get: () => get().current,

  isOpen: () => get().current !== null,
}));
