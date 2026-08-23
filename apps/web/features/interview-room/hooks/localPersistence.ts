export const DEMO_EDITOR_STORAGE_KEY = "counterq.interview-demo.editor-source";
export const DEMO_SPLITTER_STORAGE_KEY = "counterq.interview-demo.problem-width";

export const DEFAULT_PROBLEM_WIDTH = 35;
export const MIN_PROBLEM_WIDTH = 28;
export const MAX_PROBLEM_WIDTH = 44;

export function clampProblemWidth(value: number): number {
  return Math.min(MAX_PROBLEM_WIDTH, Math.max(MIN_PROBLEM_WIDTH, Math.round(value)));
}

export function readStoredProblemWidth(storage: Storage | undefined): number {
  if (!storage) {
    return DEFAULT_PROBLEM_WIDTH;
  }
  const stored = Number.parseInt(storage.getItem(DEMO_SPLITTER_STORAGE_KEY) ?? "", 10);
  if (Number.isNaN(stored)) {
    return DEFAULT_PROBLEM_WIDTH;
  }
  return clampProblemWidth(stored);
}

export function writeStoredProblemWidth(storage: Storage | undefined, value: number): number {
  const clamped = clampProblemWidth(value);
  storage?.setItem(DEMO_SPLITTER_STORAGE_KEY, String(clamped));
  return clamped;
}

export function readStoredEditorCode(storage: Storage | undefined, fallback: string): string {
  const stored = storage?.getItem(DEMO_EDITOR_STORAGE_KEY);
  return stored && stored.length > 0 ? stored : fallback;
}

export function writeStoredEditorCode(storage: Storage | undefined, value: string): void {
  storage?.setItem(DEMO_EDITOR_STORAGE_KEY, value);
}
