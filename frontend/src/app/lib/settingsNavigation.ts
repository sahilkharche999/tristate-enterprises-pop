/**
 * Pure helpers for “Edit in settings” deep-links (chip → field reveal).
 *
 * Kept JSX-free so the existing Node test runner can cover URL construction,
 * returnTo allowlisting, and field waits without React Testing Library.
 */

export type SettingsSection =
  | 'database'
  | 'disclosure'
  | 'appendices'
  | 'dre'
  | 'packages'
  | 'data';

export type SettingsEditTab = 'disclosure' | 'database';

export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  'database',
  'disclosure',
  'appendices',
  'dre',
  'packages',
  'data',
] as const;

/** Property-tab anchors that must exist as data-setting-field in SettingsScreen. */
export const PROPERTY_SETTING_FIELD_ANCHORS = [
  'hoaName',
  'units',
  'city',
  'taxId',
  'fiscalYearStart',
] as const;

export function resolveSettingsSection(raw: string | null | undefined): SettingsSection {
  if (raw && (SETTINGS_SECTIONS as readonly string[]).includes(raw)) {
    return raw as SettingsSection;
  }
  return 'database';
}

/**
 * Restrict returnTo to same-HOA relative paths. Rejects absolute/protocol-relative
 * URLs and other HOAs.
 */
export function safeReturnTo(
  raw: string | null | undefined,
  hoaId: string | number,
): string | null {
  if (raw == null) return null;
  const value = String(raw).trim();
  if (!value) return null;
  if (!value.startsWith('/')) return null;
  if (value.startsWith('//')) return null;
  if (value.includes('://')) return null;
  if (value.includes('\\')) return null;
  const pathOnly = value.split(/[?#]/, 1)[0] ?? value;
  const prefix = `/hoa/${hoaId}`;
  if (pathOnly !== prefix && !pathOnly.startsWith(`${prefix}/`)) return null;
  return value;
}

export function resolveSettingsBackHref(
  raw: string | null | undefined,
  hoaId: string | number,
): string {
  return safeReturnTo(raw, hoaId) ?? `/hoa/${hoaId}`;
}

export function buildSettingsEditHref(args: {
  hoaId: string | number;
  tab: SettingsEditTab;
  field: string;
  returnTo: string;
}): string {
  const params = new URLSearchParams();
  if (args.tab !== 'database') params.set('section', args.tab);
  params.set('field', args.field);
  const safe = safeReturnTo(args.returnTo, args.hoaId) ?? `/hoa/${args.hoaId}`;
  params.set('returnTo', safe);
  return `/hoa/${args.hoaId}/settings?${params.toString()}`;
}

export function withSection(
  params: URLSearchParams,
  section: SettingsSection,
): URLSearchParams {
  const next = new URLSearchParams(params);
  if (section === 'database') next.delete('section');
  else next.set('section', section);
  return next;
}

export function withRevealField(
  params: URLSearchParams,
  tab: SettingsEditTab,
  field: string,
): URLSearchParams {
  const next = withSection(params, tab === 'database' ? 'database' : 'disclosure');
  next.set('field', field);
  return next;
}

export function clearFieldParam(params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params);
  next.delete('field');
  return next;
}

export function settingFieldSelector(field: string): string {
  const escaped =
    typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
      ? CSS.escape(field)
      : field.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  return `[data-setting-field="${escaped}"]`;
}

export function findSettingFieldEl(
  field: string,
  root: ParentNode = document,
): HTMLElement | null {
  return root.querySelector<HTMLElement>(settingFieldSelector(field));
}

export type WaitForSettingFieldOptions = {
  root?: ParentNode;
  timeoutMs?: number;
  signal?: AbortSignal;
};

/**
 * Resolve when `[data-setting-field]` is present. Immediate hit if already
 * mounted; otherwise MutationObserver until timeout.
 */
export function waitForSettingField(
  field: string,
  options: WaitForSettingFieldOptions = {},
): Promise<HTMLElement | null> {
  const root = options.root ?? document;
  const timeoutMs = options.timeoutMs ?? 5000;
  const signal = options.signal;

  const existing = findSettingFieldEl(field, root);
  if (existing) return Promise.resolve(existing);

  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve(null);
      return;
    }

    let settled = false;
    const finish = (el: HTMLElement | null) => {
      if (settled) return;
      settled = true;
      observer.disconnect();
      window.clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      resolve(el);
    };

    const onAbort = () => finish(null);

    const observer = new MutationObserver(() => {
      const el = findSettingFieldEl(field, root);
      if (el) finish(el);
    });

    observer.observe(root === document ? document.documentElement : (root as Node), {
      childList: true,
      subtree: true,
    });

    const timer = window.setTimeout(() => finish(null), timeoutMs);
    signal?.addEventListener('abort', onAbort, { once: true });

    // Catch a node that appeared between the first query and observe().
    const raced = findSettingFieldEl(field, root);
    if (raced) finish(raced);
  });
}

export type RevealCleanup = () => void;

/**
 * Scroll, flash, and focus a settings field element. Returns cleanup for the
 * flash timer / class.
 */
export function revealSettingElement(el: HTMLElement): RevealCleanup {
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.classList.add('setting-field-flash');
  el.querySelector<HTMLElement>('input, textarea, select, button')?.focus();
  const flashTimer = window.setTimeout(() => {
    el.classList.remove('setting-field-flash');
  }, 2000);
  return () => {
    window.clearTimeout(flashTimer);
    el.classList.remove('setting-field-flash');
  };
}
