// Types for scripts/ci/pin-freshness.mjs, which stays plain JavaScript so it runs with node alone.
// Kept beside it; the tests that import it are what type-check it.

export type Upstream =
  | { github: string; strip?: string }
  | { chrome: string }
  | { terraform: true }
  | { cloudSdk: true }
  | { pypi: string }
  | { gcrTag: { repository: string; tag: string } }
  | { age: number };

export type Pin = { name: string; where: string; read: () => string; upstream: Upstream };

export type Verdict = { status: "current" | "behind" | "unchecked"; detail: string };

export type Row = Verdict & { pin: Pin; pinned: string };

export const SNAPSHOT_MAX_AGE_DAYS: number;
export function pins(): Pin[];
export function latest(
  upstream: Upstream,
  env?: Record<string, string | undefined>,
): Promise<string>;
export function verdict(pin: Pin, pinned: string, upstreamValue: string): Verdict;
export function check(
  list?: Pin[],
  lookup?: (upstream: Upstream) => Promise<string>,
): Promise<Row[]>;
export function report(rows: Row[]): { text: string; behind: number };
