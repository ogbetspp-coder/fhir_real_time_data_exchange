// Types for scripts/ci/workflow-runs.mjs, which stays plain JavaScript so it runs with node
// alone. Kept beside it; the tests that import it are what type-check it.

export type WorkflowRun = {
  id: number;
  head_sha: string;
  head_branch: string | null;
  event: string;
  status: string;
  conclusion: string | null;
  html_url?: string;
  updated_at?: string;
};

export type WorkflowStep = {
  name: string;
  status: string;
  conclusion: string | null;
  started_at?: string | null;
  completed_at?: string | null;
};

export type WorkflowJob = {
  name: string;
  status: string;
  conclusion: string | null;
  completed_at?: string | null;
  steps?: WorkflowStep[];
};

export type Verdict = { state: "wait" | "pass" | "fail"; reason: string };

export const CI_JOBS: readonly string[];
export const DEPLOY_JOB: string;
export const MUTATING_STEPS: readonly string[];
export const CLOCK_MARGIN_MS: number;
export const API_TRIES: number;
export function rateLimitReset(status: number, headers: Record<string, string>): number | undefined;
export class ApiError extends Error {
  constructor(
    message: string,
    options?: { transient?: boolean; resetAt?: number; cause?: unknown },
  );
  transient: boolean;
  resetAt: number | undefined;
}
export function retryDelay(
  status: number,
  headers: Record<string, string>,
  attempt: number,
  backoff: number,
): number | undefined;
export function ciRunFor(runs: readonly WorkflowRun[], commit: string): WorkflowRun | undefined;
export function ciVerdict(run: WorkflowRun | undefined, jobs: readonly WorkflowJob[]): Verdict;
export function mutationWindow(
  job: WorkflowJob,
): { started: number; ended: number | null } | undefined;
export function overlapping<T extends WorkflowJob>(
  jobs: readonly T[],
  since: number,
  now: number,
): T[];
