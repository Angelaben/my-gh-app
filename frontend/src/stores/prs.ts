import { writable, get } from 'svelte/store';
import type { PR, Comment, CommentAnalysis } from '../lib/types';
import { api } from '../lib/api';
import { selectedModel } from './ui';
import { prKey } from './reviewSessions';

export const prs = writable<PR[]>([]);
export const activePR = writable<PR | null>(null);
export const activeTab = writable<'review' | 'comments'>('review');
export const comments = writable<Comment[]>([]);
// IDs of comments created since the last visit (computed by the backend).
export const newCommentIds = writable<number[]>([]);
// "owner/repo#pr" the comments store currently holds. Guards against
// double-fetching: each GET /pr/... advances the backend's last-visited
// marker, which would wipe the "new" flags on the second call.
let loadedCommentsKey: string | null = null;

// Findings staged for a batched "Request Changes" review, keyed by
// "owner/repo#pr" so each PR keeps its own staging across PR switches.
export const stagedFindingsByPR = writable<Map<string, import('../lib/types').Finding[]>>(new Map());
export const publishReviewsModalOpen = writable<boolean>(false);

// Per-finding body overrides: lets users edit AI-generated text before
// publishing. Keys are PR-namespaced finding identity strings, so overrides
// for different PRs never collide and survive PR switches.
export const findingBodyOverrides = writable<Map<string, string>>(new Map());

export function findingOverrideKey(prk: string, f: import('../lib/types').Finding): string {
  return `${prk}|${f.priority}:${f.title}:${f.file ?? ''}:${f.line ?? ''}`;
}

export function setFindingBodyOverride(prk: string, finding: import('../lib/types').Finding, body: string): void {
  findingBodyOverrides.update((m: Map<string, string>) => new Map(m).set(findingOverrideKey(prk, finding), body));
}

export function clearFindingBodyOverride(prk: string, finding: import('../lib/types').Finding): void {
  findingBodyOverrides.update((m: Map<string, string>) => { const n = new Map(m); n.delete(findingOverrideKey(prk, finding)); return n; });
}

// Comment "new since last visit" tracking is tied to the open PR.
activePR.subscribe(() => {
  newCommentIds.set([]);
  loadedCommentsKey = null;
});

function sameFinding(a: import('../lib/types').Finding, b: import('../lib/types').Finding): boolean {
  return a.title === b.title && a.priority === b.priority && a.file === b.file && a.line === b.line;
}

export function stageFinding(prk: string, finding: import('../lib/types').Finding): boolean {
  let added = false;
  stagedFindingsByPR.update((m) => {
    const list = m.get(prk) ?? [];
    if (list.some((f) => sameFinding(f, finding))) return m;
    added = true;
    return new Map(m).set(prk, [...list, finding]);
  });
  return added;
}

export function unstageFinding(prk: string, finding: import('../lib/types').Finding): void {
  stagedFindingsByPR.update((m) => {
    const list = (m.get(prk) ?? []).filter((f) => !sameFinding(f, finding));
    const next = new Map(m);
    if (list.length === 0) next.delete(prk);
    else next.set(prk, list);
    return next;
  });
}

export async function loadPRs(owner: string, repo: string, forceRefresh = false): Promise<void> {
  if (forceRefresh) {
    const data = await api.post<PR[]>(`/prs/${owner}/${repo}/refresh`);
    prs.set(data);
  } else {
    const data = await api.get<PR[]>(`/prs/${owner}/${repo}`);
    prs.set(data);
  }
}


export async function loadComments(
  owner: string,
  repo: string,
  prNumber: number,
  force = false,
): Promise<void> {
  const key = `${owner}/${repo}#${prNumber}`;
  if (!force && loadedCommentsKey === key) return;
  const data = await api.get<RawPRDetail>(`/pr/${owner}/${repo}/${prNumber}`);
  comments.set((data.comments ?? []).map((c, i) => normalizeComment(c, i)));
  newCommentIds.set(data.new_comment_ids ?? []);
  loadedCommentsKey = key;
}

export async function analyzeComments(owner: string, repo: string, prNumber: number, signal?: AbortSignal): Promise<void> {
  const data = await api.post<{ comments: RawComment[]; analysis: RawAnalysis[] }>(
    `/comments/${owner}/${repo}/${prNumber}/analyze`,
    undefined,
    signal,
  );
  const authorStr = (author: string | { login: string }) => typeof author === 'string' ? author : author.login;
  // The backend tags each comment with its index as [id=N]; prefer that exact
  // mapping and fall back to author matching for models that omit the id.
  const analysisById = new Map(
    data.analysis.filter((a) => typeof a.id === 'number').map((a) => [a.id as number, a]),
  );
  const analysisByAuthor = new Map(data.analysis.map((a) => [a.author, a]));
  comments.set(
    data.comments.map((c, i) => {
      const raw = normalizeComment(c, i);
      const analysis = analysisById.get(i) ?? analysisByAuthor.get(authorStr(c.author));
      if (analysis) {
        raw.analysis = {
          valid: analysis.valid,
          interest: analysis.interest as CommentAnalysis['interest'],
          critical: analysis.criticality === 'P0',
          priority: analysis.criticality as CommentAnalysis['priority'],
        };
      }
      return raw;
    })
  );
}

export function formatFindingBody(finding: import('../lib/types').Finding, withFooter = true): string {
  const model = get(selectedModel);
  const footer = withFooter
    ? `\n\n---\n*Generated by gh-review-tool${model ? ` using \`${model}\`` : ''}*`
    : '';
  const main = finding.suggestion
    ? `**${finding.priority}: ${finding.title}**\n\n${finding.description}\n\n**Suggestion:**\n${finding.suggestion}`
    : `**${finding.priority}: ${finding.title}**\n\n${finding.description}`;
  return main + footer;
}

export async function publishFinding(
  owner: string,
  repo: string,
  prNumber: number,
  finding: import('../lib/types').Finding
): Promise<void> {
  const fullRepo = `${owner}/${repo}`;
  const bodyOverride = get(findingBodyOverrides).get(findingOverrideKey(prKey(owner, repo, prNumber), finding));
  const body = bodyOverride ?? formatFindingBody(finding);

  // P1 findings escalate to a single-finding REQUEST_CHANGES review so the PR
  // shows a "Changes requested" banner instead of a casual comment.
  if (finding.priority === 'P1') {
    const reviewBody = `Requesting changes: ${finding.title}`;
    const inlineComments = finding.file && finding.line != null
      ? [{ path: finding.file, line: finding.line, body }]
      : [];
    const result = await api.post<{ status: string; warning?: string }>('/review/publish', {
      repo: fullRepo,
      pr_number: prNumber,
      body: inlineComments.length > 0 ? reviewBody : body,
      event: 'REQUEST_CHANGES',
      comments: inlineComments,
    });
    if (result.warning) {
      // Backend folded the inline comment into the body because GitHub
      // rejected it. Caller can still report success — review is published.
      console.warn('[publishFinding]', result.warning);
    }
    return;
  }

  if (finding.file && finding.line != null) {
    await api.post('/comment/inline', {
      repo: fullRepo,
      pr_number: prNumber,
      body,
      path: finding.file,
      line: finding.line,
    });
  } else {
    await api.post('/comment/publish', {
      repo: fullRepo,
      pr_number: prNumber,
      body,
    });
  }
}

export async function publishStagedReview(
  owner: string,
  repo: string,
  prNumber: number,
  reviewBody: string
): Promise<{ warning?: string }> {
  const fullRepo = `${owner}/${repo}`;
  const prk = prKey(owner, repo, prNumber);
  const staged = get(stagedFindingsByPR).get(prk) ?? [];
  if (staged.length === 0) {
    throw new Error('No findings staged for review');
  }

  // Findings with file+line become inline comments. Findings without file/line
  // are appended into the review body so they aren't lost.
  const inlineComments: { path: string; line: number; body: string }[] = [];
  const orphanLines: string[] = [];
  const overrides = get(findingBodyOverrides);
  for (const finding of staged) {
    const bodyOverride = overrides.get(findingOverrideKey(prk, finding));
    const body = bodyOverride ?? formatFindingBody(finding, false);
    if (finding.file && finding.line != null) {
      inlineComments.push({ path: finding.file, line: finding.line, body });
    } else {
      orphanLines.push(`- **${finding.priority}**: ${finding.title}\n  ${finding.description}`);
    }
  }

  const model = get(selectedModel);
  const footer = `\n\n---\n*Generated by gh-review-tool${model ? ` using \`${model}\`` : ''}*`;
  let body = reviewBody.trim() || 'Review requested by gh-review-tool';
  if (orphanLines.length > 0) {
    body += `\n\n**Additional findings (no file/line):**\n${orphanLines.join('\n')}`;
  }
  body += footer;

  const result = await api.post<{ status: string; warning?: string }>('/review/publish', {
    repo: fullRepo,
    pr_number: prNumber,
    body,
    event: 'REQUEST_CHANGES',
    comments: inlineComments,
  });
  stagedFindingsByPR.update((m) => {
    const next = new Map(m);
    next.delete(prk);
    return next;
  });
  return { warning: result.warning };
}

// ── Internal types for raw API responses ──

interface RawComment {
  id: number;
  author: string | { login: string };
  body: string;
  path?: string;
  file?: string;
  line?: number;
  created_at: string;
  comment_type?: string;
  in_reply_to_id?: number | null;
  thread_id?: number | null;
  is_ours?: boolean;
  is_new_reply?: boolean;
  has_new_replies?: boolean;
}

interface RawAnalysis {
  id?: number;
  author: string;
  criticality: string;
  valid: boolean;
  interest: string;
  summary: string;
  original_body: string;
}

interface RawPRDetail {
  comments?: RawComment[];
  new_comment_ids?: number[];
}

function normalizeComment(c: RawComment, index: number): Comment {
  const authorStr = typeof c.author === 'string' ? c.author : c.author.login;
  return {
    id: c.id ?? index,
    author: authorStr,
    body: c.body,
    // Enriched comments (GET /pr/...) use `file`; raw GitHub dicts
    // (POST /comments/.../analyze) use `path`.
    file: c.file ?? c.path,
    line: c.line,
    created_at: c.created_at,
    comment_type: c.comment_type ?? 'pr_comment',
    in_reply_to_id: c.in_reply_to_id,
    thread_id: c.thread_id,
    is_ours: c.is_ours,
    is_new_reply: c.is_new_reply,
    has_new_replies: c.has_new_replies,
  };
}

export async function deleteComment(
  repo: string,
  commentId: number,
  commentType: string
): Promise<void> {
  await api.delete('/comment', { repo, comment_id: commentId, comment_type: commentType });
}
