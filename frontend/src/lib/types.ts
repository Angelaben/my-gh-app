export interface Repo {
  owner: string;
  name: string;
  full_name: string;
}

export interface PR {
  number: number;
  title: string;
  author: string;
  branch: string;
  base_branch: string;
  additions: number;
  deletions: number;
  updated_at: string;
  url: string;
}

export interface Finding {
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  title: string;
  description: string;
  file?: string;
  line?: number;
  suggestion?: string;
}

export interface Review {
  summary?: string;
  findings: Finding[];
  raw_output?: string;
}

export interface CommentAnalysis {
  valid: boolean;
  interest: 'low' | 'medium' | 'high';
  critical: boolean;
  priority: 'P0' | 'P1' | 'P2' | 'P3';
}

export interface Comment {
  id: number;
  author: string;
  body: string;
  file?: string;
  line?: number;
  created_at: string;
  comment_type: string;
  analysis?: CommentAnalysis;
}

export type SSEReviewEvent =
  | { type: 'chunk'; text: string }
  | { type: 'result'; review: Review }
  | { type: 'warning'; lines: string[] }
  | { type: 'done' }
  | { type: 'error'; message: string };

export type ToastType = 'success' | 'error' | 'info';

export interface Toast {
  id: number;
  message: string;
  type: ToastType;
}
