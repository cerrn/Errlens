export interface FormErrors {
  trace?: string;
  repo_url?: string;
}

const GH_URL = /^(?:https?:\/\/)?(?:www\.)?github\.com[/:]([\w.-]+)\/([\w.-]+?)(?:\.git)?(?:\/(?:tree|blob|commit)\/[^?#]+?)?\/?(?:[?#].*)?$/;
const GH_SSH = /^git@github\.com:([\w.-]+)\/([\w.-]+?)(?:\.git)?$/;
const SHORT = /^([\w.-]+)\/([\w.-]+)$/;
const LOCAL = /^(?:file:\/\/)?(?:\/|[A-Za-z]:[\\/])/; // when the backend has ERRLENS_ALLOW_LOCAL_REPOS enabled

export function isValidRepoUrl(url: string): boolean {
  const u = url.trim();
  return GH_URL.test(u) || GH_SSH.test(u) || SHORT.test(u) || LOCAL.test(u);
}

export function detectLanguage(trace: string): "python" | "javascript" | null {
  if (/Traceback \(most recent call last\)/.test(trace) || /^\s*File ".+", line \d+/m.test(trace)) return "python";
  if (/^\s+at\s+.+/m.test(trace)) return "javascript";
  return null;
}

export function validateForm(trace: string, repoUrl: string): FormErrors {
  const errors: FormErrors = {};
  if (!trace.trim()) errors.trace = "The stack trace must not be empty.";
  else if (!detectLanguage(trace))
    errors.trace = "Trace not recognized. Paste a Python (Traceback ...) or JavaScript/Node (at fn (file:line:column)) trace.";
  if (!repoUrl.trim()) errors.repo_url = "A repository link is required.";
  else if (!isValidRepoUrl(repoUrl))
    errors.repo_url = "Invalid GitHub link. Example: https://github.com/owner/repo or owner/repo";
  return errors;
}
