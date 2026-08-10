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

/** Sub-tabs inside Disclosure Defaults progressive UI. */
export type DisclosureDefaultsTab =
  | 'letterhead'
  | 'money'
  | 'section5570'
  | 'forecast'
  | 'wording';

export const DISCLOSURE_DEFAULTS_TABS: readonly DisclosureDefaultsTab[] = [
  'letterhead',
  'money',
  'section5570',
  'forecast',
  'wording',
] as const;

const MONEY_FIELDS = new Set([
  'reserve_cash_balance_eoy_prior',
  'fund_balance_boy_operations',
  'monthly_assessment_per_unit_prior',
  'interest_rate_after_tax',
  'replacement_fund_monthly_assessment_per_unit',
  'approved_monthly_assessment_per_unit',
  'reserve_interest_income_override',
  'income_tax_provision_override',
  'reserve_funding_manual_amount',
  'reserve_funding_source',
  'financial_packet_archetype',
]);

const LETTERHEAD_FIELDS = new Set([
  'management_company',
  'management_company_address',
  'management_company_phone',
  'management_company_fax',
  'management_company_web',
  'cpa_firm_name',
  'cpa_firm_address',
  'reserve_study_expert_name',
  'reserve_study_date',
  'letter_signed_by',
  'letter_date',
  'accountant_report_date',
  'reserve_funding_plan_date',
  'letterhead_logo_mode',
]);

const SECTION_5570_FIELDS = new Set([
  'special_assessments_json',
  'additional_assessments_needed_json',
  'outstanding_loan_json',
]);

const FORECAST_FIELDS = new Set([
  'assessment_increase_schedule_json',
  'board_deferrals_json',
]);

export function resolveDisclosureDefaultsTab(
  raw: string | null | undefined,
): DisclosureDefaultsTab {
  if (raw && (DISCLOSURE_DEFAULTS_TABS as readonly string[]).includes(raw)) {
    return raw as DisclosureDefaultsTab;
  }
  return 'money';
}

/** Map a settings field key to the Disclosure Defaults sub-tab that hosts it. */
export function disclosureTabForField(field: string | null | undefined): DisclosureDefaultsTab {
  if (!field) return 'money';
  if (MONEY_FIELDS.has(field)) return 'money';
  if (LETTERHEAD_FIELDS.has(field)) return 'letterhead';
  if (SECTION_5570_FIELDS.has(field)) return 'section5570';
  if (FORECAST_FIELDS.has(field)) return 'forecast';
  return 'money';
}

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
  'packageYear',
] as const;

export function resolveSettingsSection(raw: string | null | undefined): SettingsSection {
  if (raw && (SETTINGS_SECTIONS as readonly string[]).includes(raw)) {
    return raw as SettingsSection;
  }
  return 'database';
}

/**
 * Query flag: after leaving package wording via “Edit in settings”, Back
 * (or the returnTo destination) should reopen the package-language workbench.
 */
export const OPEN_PACKAGE_LANGUAGE_PARAM = 'openPackageLanguage';

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

/**
 * Append ``openPackageLanguage=1`` so the destination can reopen the workbench.
 * Idempotent if the flag is already present.
 */
export function withOpenPackageLanguagePath(path: string): string {
  const trimmed = String(path || '').trim();
  if (!trimmed) return trimmed;
  const hashIdx = trimmed.indexOf('#');
  const withoutHash = hashIdx >= 0 ? trimmed.slice(0, hashIdx) : trimmed;
  const hash = hashIdx >= 0 ? trimmed.slice(hashIdx) : '';
  const qIdx = withoutHash.indexOf('?');
  const base = qIdx >= 0 ? withoutHash.slice(0, qIdx) : withoutHash;
  const qs = qIdx >= 0 ? withoutHash.slice(qIdx + 1) : '';
  const params = new URLSearchParams(qs);
  params.set(OPEN_PACKAGE_LANGUAGE_PARAM, '1');
  const q = params.toString();
  return `${base}?${q}${hash}`;
}

export function wantsOpenPackageLanguage(
  params: Pick<URLSearchParams, 'get'> | null | undefined,
): boolean {
  if (!params) return false;
  const v = params.get(OPEN_PACKAGE_LANGUAGE_PARAM);
  return v === '1' || v === 'true';
}

/** True when a returnTo (or any path) asks to reopen package wording. */
export function pathWantsOpenPackageLanguage(
  raw: string | null | undefined,
): boolean {
  if (raw == null || !String(raw).trim()) return false;
  try {
    const url = new URL(String(raw).trim(), 'http://local.test');
    return wantsOpenPackageLanguage(url.searchParams);
  } catch {
    return false;
  }
}

export function stripOpenPackageLanguageParam(
  params: URLSearchParams,
): URLSearchParams {
  const next = new URLSearchParams(params);
  next.delete(OPEN_PACKAGE_LANGUAGE_PARAM);
  return next;
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
  if (tab === 'disclosure') {
    next.set('tab', disclosureTabForField(field));
  }
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
