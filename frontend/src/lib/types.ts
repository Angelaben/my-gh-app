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
  is_draft: boolean;
}

export interface Finding {
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  title: string;
  description: string;
  file?: string;
  line?: number;
  suggestion?: string;
  confidence?: 'high' | 'medium' | 'low';
}

export interface Review {
  summary?: string;
  findings: Finding[];
  raw_output?: string;
  head_sha?: string;
  created_at?: string;
  stale?: boolean;
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
  // Threading metadata computed by the backend (CommentService.enrich_comments)
  in_reply_to_id?: number | null;
  thread_id?: number | null;
  is_ours?: boolean;
  is_new_reply?: boolean;
  has_new_replies?: boolean;
}

export type SSEReviewEvent =
  | { type: 'meta'; request_id: string }
  | { type: 'chunk'; text: string }
  | { type: 'progress'; text: string }
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

export interface ReviewSettings {
  review_timeout: number;
  review_diff_max_chars: number;
  review_max_concurrency: number;
  review_ignore_globs: string[];
}

export type PromptName = 'review' | 'fix' | 'analyze';

export interface PromptInfo {
  current: string;
  default: string;
  is_custom: boolean;
  required_placeholders: string[];
}

export interface HunkRow {
  sign: ' ' | '+' | '-';
  old_line: number | null;
  new_line: number | null;
  text: string;
}

export interface IncrementalInfo {
  no_changes: boolean;
  base_sha: string | null;
  head_sha: string | null;
  changed_files: string[];
  carried: number;
  new: number;
}

export interface StatsHistory {
  days: number;
  dates: string[];
  reviews_per_day: number[];
  findings_per_day: number[];
  failed_per_day: number[];
  total_reviews: number;
  findings_by_priority: Record<string, number>;
  by_provider: Record<string, number>;
  avg_duration_ms: number | null;
}
