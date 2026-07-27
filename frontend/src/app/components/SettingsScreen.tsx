import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { useParams, Link, useSearchParams } from 'react-router';
import {
  ArrowLeft,
  Archive,
  Database,
  Download,
  Eye,
  FileArchive,
  FileText,
  FolderOpen,
  Landmark,
  PackageCheck,
  Settings as SettingsIcon,
} from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { toast } from 'sonner';
import { exportData } from '../api/macros';
import { getHOA, updateHOA, type HOARecord } from '../api/hoa';
import { getErrorMessage } from '../lib/errors';
import { MONTH_NAMES, monthNameToNumber, monthNumberToName } from '../lib/hoa';
import {
  clearFieldParam,
  pathWantsOpenPackageLanguage,
  resolveSettingsBackHref,
  resolveSettingsSection,
  revealSettingElement,
  waitForSettingField,
  withRevealField,
  withSection,
  type SettingsEditTab,
  type SettingsSection,
} from '../lib/settingsNavigation';
import { HOADisclosureSettingsForm, type HOADisclosureSettingsFormHandle } from './HOADisclosureSettingsForm';
import { BoilerplateWorkbench } from './BoilerplateWorkbench';
// full-screen package language workbench (same shell as DRE PDF compare)
import { AppendixManifestEditor } from './AppendixManifestEditor';
import { AnnualPackagesPanel } from './AnnualPackagesPanel';
import { DREPanel } from './DREPanel';

interface SettingsFormState {
  name: string;
  hoaId: string;
  fiscalYearStart: string;
  /** Package / disclosure year (e.g. 2026) → properties.portfolio_year. */
  packageYear: string;
  taxId: string;
  units: string;
  city: string;
  allocationType: string;
  driveFolderPath: string;
}

type ValidationField = 'name' | 'units' | 'fiscalYearStart' | 'packageYear';
type ValidationErrors = Partial<Record<ValidationField, string>>;

function databaseFormFingerprint(form: SettingsFormState): string {
  return JSON.stringify({
    name: form.name,
    hoaId: form.hoaId,
    fiscalYearStart: form.fiscalYearStart,
    packageYear: form.packageYear,
    taxId: form.taxId,
    units: form.units,
    city: form.city,
  });
}

const SETTINGS_NAV_GROUPS: Array<{
  label: string;
  items: Array<{
    value: SettingsSection;
    label: string;
    helper: string;
    icon: typeof Database;
  }>;
}> = [
  {
    label: 'HOA Setup',
    items: [
      {
        value: 'database',
        label: 'HOA Database',
        helper: 'Identity, package year, units',
        icon: Database,
      },
      {
        value: 'disclosure',
        label: 'Disclosure Defaults',
        helper: 'Contacts, rates, package language',
        icon: FileText,
      },
    ],
  },
  {
    label: 'Documents',
    items: [
      {
        value: 'appendices',
        label: 'Appendices',
        helper: 'Static package attachments',
        icon: FileArchive,
      },
      {
        value: 'dre',
        label: 'DRE & Review',
        helper: 'Assessment setup review',
        icon: Landmark,
      },
    ],
  },
  {
    label: 'Lifecycle',
    items: [
      {
        value: 'packages',
        label: 'Annual Packages',
        helper: 'Create, approve, finalize',
        icon: PackageCheck,
      },
    ],
  },
  {
    label: 'Tools',
    items: [
      {
        value: 'data',
        label: 'Data Export',
        helper: 'Download system data',
        icon: Archive,
      },
    ],
  },
];

const DEFAULT_FORM: SettingsFormState = {
  name: '',
  hoaId: '',
  fiscalYearStart: 'January',
  packageYear: String(new Date().getFullYear()),
  taxId: '',
  units: '',
  city: '',
  allocationType: 'Flat',
  driveFolderPath: '/Tri-State/HOAs/401-HOA',
};

function buildFormState(
  hoa: HOARecord,
  previous?: SettingsFormState,
): SettingsFormState {
  return {
    ...DEFAULT_FORM,
    allocationType: previous?.allocationType ?? DEFAULT_FORM.allocationType,
    driveFolderPath: previous?.driveFolderPath ?? DEFAULT_FORM.driveFolderPath,
    name: hoa.name,
    hoaId: hoa.hoa_code,
    fiscalYearStart: monthNumberToName(hoa.fiscal_year_start_month),
    packageYear: String(
      hoa.portfolio_year ?? new Date().getFullYear(),
    ),
    taxId: hoa.tax_id,
    units: String(hoa.units),
    city: hoa.city || '',
  };
}

function validateForm(form: SettingsFormState): ValidationErrors {
  const errors: ValidationErrors = {};

  if (!form.name.trim()) errors.name = 'HOA name is required.';

  const unitCount = Number(form.units);
  if (form.units.trim() && (!Number.isInteger(unitCount) || unitCount <= 0)) {
    errors.units = 'Units must be a positive whole number.';
  }

  if (!form.fiscalYearStart) errors.fiscalYearStart = 'Fiscal year start month is required.';

  const packageYear = Number(form.packageYear);
  if (
    !form.packageYear.trim() ||
    !Number.isInteger(packageYear) ||
    packageYear < 1990 ||
    packageYear > 2100
  ) {
    errors.packageYear = 'Package year must be a whole year between 1990 and 2100.';
  }
  return errors;
}

export function SettingsScreen() {
  const { id } = useParams<{ id: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [hoa, setHoa] = useState<HOARecord | null>(null);
  const [hoaConfig, setHoaConfig] = useState<SettingsFormState>(DEFAULT_FORM);
  const [validationErrors, setValidationErrors] = useState<ValidationErrors>({});
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [packageLanguageOpen, setPackageLanguageOpen] = useState(false);
  // Set when the operator leaves package wording via “Edit in settings” on this
  // screen so the header Back reopens the workbench instead of leaving Settings.
  const [resumePackageLanguage, setResumePackageLanguage] = useState(false);
  const [disclosureReady, setDisclosureReady] = useState(false);
  const [disclosureDirty, setDisclosureDirty] = useState(false);
  const [databaseBaseline, setDatabaseBaseline] = useState<string | null>(null);
  const disclosureFormRef = useRef<HOADisclosureSettingsFormHandle>(null);
  const flashCleanupRef = useRef<(() => void) | null>(null);
  const returnTo = searchParams.get('returnTo');
  const backHref = resolveSettingsBackHref(returnTo, id ?? '');
  // Cross-route: disclosure (or other host) asked Back to reopen package wording.
  const returnToOpensPackageLanguage = pathWantsOpenPackageLanguage(returnTo);
  const selectedSection = resolveSettingsSection(searchParams.get('section'));
  const databaseDirty =
    databaseBaseline != null && databaseFormFingerprint(hoaConfig) !== databaseBaseline;

  useEffect(() => {
    let cancelled = false;

    async function loadHOA() {
      if (!id) {
        setLoadError('HOA not found');
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      setLoadError(null);
      try {
        const response = await getHOA(id);
        if (!cancelled) {
          setHoa(response);
          const form = buildFormState(response);
          setHoaConfig(form);
          setDatabaseBaseline(databaseFormFingerprint(form));
        }
      } catch (error) {
        if (!cancelled) {
          setLoadError(getErrorMessage(error, 'Failed to load HOA settings.'));
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    loadHOA();
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (selectedSection !== 'disclosure') {
      setDisclosureReady(false);
      setDisclosureDirty(false);
    }
  }, [selectedSection, id]);

  const confirmLeaveDirty = (leaving: SettingsSection): boolean => {
    const dirty =
      (leaving === 'disclosure' && disclosureDirty) ||
      (leaving === 'database' && databaseDirty);
    if (!dirty) return true;
    return window.confirm(
      'You have unsaved changes. Leave this section without saving?',
    );
  };

  const handleSectionChange = (value: string) => {
    const nextSection = resolveSettingsSection(value);
    if (nextSection === selectedSection) return;
    if (!confirmLeaveDirty(selectedSection)) return;
    setSearchParams(
      (current) => withSection(clearFieldParam(current), nextSection),
      { replace: true },
    );
  };

  const handleBackClick = (event: MouseEvent<HTMLAnchorElement>) => {
    // Same-screen path: package wording → Edit in settings → Back should
    // reopen the workbench, not leave Settings for the prior page.
    if (resumePackageLanguage) {
      event.preventDefault();
      setResumePackageLanguage(false);
      setPackageLanguageOpen(true);
      return;
    }

    const dirty =
      (selectedSection === 'disclosure' && disclosureDirty) ||
      (selectedSection === 'database' && databaseDirty);
    if (
      dirty &&
      !window.confirm('You have unsaved changes. Leave settings without saving?')
    ) {
      event.preventDefault();
    }
  };

  /**
   * Reveal the settings field behind a chip the operator clicked in the
   * disclosure editor. Transports intent as `field` (+ section) in the URL so
   * the same path works from this screen and from the disclosure workspace.
   */
  const revealSettingField = (tab: SettingsEditTab, field: string) => {
    setSearchParams(
      (current) => withRevealField(current, tab, field),
      { replace: true },
    );
  };

  /** From package wording on this screen: reveal field and mark Back → workbench. */
  const revealSettingFieldFromPackageLanguage = (
    tab: SettingsEditTab,
    field: string,
  ) => {
    setResumePackageLanguage(true);
    revealSettingField(tab, field);
  };

  // Scroll to and flash the requested field once the hosting form is ready,
  // then drop `field` (success or failure) so refresh does not replay the jump.
  useEffect(() => {
    const field = searchParams.get('field');
    if (!field || isLoading) return;

    const section = resolveSettingsSection(searchParams.get('section'));
    if (section === 'disclosure' && !disclosureReady) return;

    const controller = new AbortController();
    let cancelled = false;

    void (async () => {
      const el = await waitForSettingField(field, {
        signal: controller.signal,
        timeoutMs: 5000,
      });
      if (cancelled || controller.signal.aborted) return;

      if (!el) {
        toast.message('Could not find that setting. It may have moved.');
        setSearchParams((current) => clearFieldParam(current), { replace: true });
        return;
      }

      flashCleanupRef.current?.();
      flashCleanupRef.current = revealSettingElement(el);
      setSearchParams((current) => clearFieldParam(current), { replace: true });
    })();

    return () => {
      cancelled = true;
      controller.abort();
      flashCleanupRef.current?.();
      flashCleanupRef.current = null;
    };
  }, [searchParams, isLoading, disclosureReady, setSearchParams]);

  const handleFieldChange = (field: keyof SettingsFormState, value: string) => {
    setHoaConfig((current) => ({ ...current, [field]: value }));
    if (field in validationErrors) {
      setValidationErrors((current) => {
        const next = { ...current };
        delete next[field as ValidationField];
        return next;
      });
    }
  };

  const handleSave = async () => {
    if (!id) return;

    if (selectedSection === 'disclosure') {
      // The disclosure form owns its own state; drive the header button's
      // spinner + a success/error toast off its save() result so the button
      // gives visible feedback (previously it silently proxied with none).
      setIsSaving(true);
      try {
        const ok = await disclosureFormRef.current?.save();
        if (ok) {
          toast.success('Settings saved.');
        } else {
          toast.error('Failed to save settings. Please try again.');
        }
      } finally {
        setIsSaving(false);
      }
      return;
    }

    const nextErrors = validateForm(hoaConfig);
    setValidationErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      toast.error('Please fix the highlighted settings fields.');
      return;
    }

    setIsSaving(true);
    try {
      const payload = {
        name: hoaConfig.name.trim(),
        fiscal_year_start_month: monthNameToNumber(hoaConfig.fiscalYearStart),
        portfolio_year: Number(hoaConfig.packageYear),
        ...(hoaConfig.hoaId.trim() ? { hoa_code: hoaConfig.hoaId.trim() } : {}),
        ...(hoaConfig.taxId.trim() ? { tax_id: hoaConfig.taxId.trim() } : {}),
        ...(hoaConfig.units.trim() ? { units: Number(hoaConfig.units) } : {}),
        city: hoaConfig.city.trim(),
      };
      const savedHoa = await updateHOA(id, payload);
      setHoa(savedHoa);
      const form = buildFormState(savedHoa);
      setHoaConfig(form);
      setDatabaseBaseline(databaseFormFingerprint(form));
      setValidationErrors({});
      toast.success('Settings saved.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to save settings. Please try again.'));
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <p className="text-[#666666]">Loading HOA settings...</p>
      </div>
    );
  }

  if (loadError || !hoa) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <p className="text-[#666666]">{loadError || 'HOA not found'}</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-[#e5e5e5] bg-white sticky top-0 z-10 shadow-sm">
        <div className="px-8 py-6 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <Link
              to={backHref}
              onClick={handleBackClick}
              className="p-2 hover:bg-[#f5f5f5] rounded-lg transition-colors"
              aria-label={
                resumePackageLanguage || returnToOpensPackageLanguage
                  ? 'Back to package wording'
                  : 'Back'
              }
              title={
                resumePackageLanguage || returnToOpensPackageLanguage
                  ? 'Back to package wording'
                  : undefined
              }
            >
              <ArrowLeft className="w-5 h-5 text-[#525252]" />
            </Link>
            <div>
              <h1 className="text-xl font-semibold text-[#111111]">Settings</h1>
              <p className="text-sm text-[#737373]">{hoa.name}</p>
            </div>
          </div>
          <Button
            onClick={handleSave}
            disabled={isSaving}
            className="bg-[#111111] text-white hover:bg-[#262626] shadow-sm disabled:opacity-60"
          >
            {isSaving ? 'Saving...' : 'Save Changes'}
          </Button>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-5 py-8 md:px-8">
        <Tabs value={selectedSection} onValueChange={handleSectionChange} className="gap-0">
          <div className="grid gap-8 lg:grid-cols-[280px_minmax(0,1fr)]">
            <aside className="rounded-lg border border-[#E5E5E5] bg-white p-3 shadow-sm lg:sticky lg:top-28 lg:self-start">
              <TabsList className="flex h-auto w-full flex-col items-stretch justify-start gap-4 rounded-none border-0 bg-transparent p-0">
                {SETTINGS_NAV_GROUPS.map((group) => (
                  <div key={group.label} className="space-y-2">
                    <p className="px-2 text-xs font-semibold uppercase tracking-[0.12em] text-[#737373]">
                      {group.label}
                    </p>
                    <div className="grid gap-1">
                      {group.items.map((item) => {
                        const Icon = item.icon;
                        return (
                          <TabsTrigger
                            key={item.value}
                            value={item.value}
                            className="h-auto w-full cursor-pointer justify-start rounded-lg border border-transparent px-3 py-3 text-left transition-colors hover:border-[#e5e5e5] hover:bg-[#f7f7f7] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#111111] data-[state=active]:border-[#111111] data-[state=active]:bg-[#f7f7f7] data-[state=active]:shadow-none"
                          >
                            <span className="flex w-full min-w-0 items-start gap-3">
                              <Icon className="mt-0.5 h-4 w-4 shrink-0 text-[#525252]" />
                              <span className="min-w-0">
                                <span className="block text-sm font-semibold text-[#111111]">
                                  {item.label}
                                </span>
                                <span className="hidden text-xs font-normal leading-5 text-[#737373] lg:block">
                                  {item.helper}
                                </span>
                              </span>
                            </span>
                          </TabsTrigger>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </TabsList>
            </aside>

            <div className="min-w-0">
          <TabsContent value="database" className="space-y-6">
            <div className="rounded-lg border border-[#E5E5E5] bg-white p-6 shadow-sm">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h3 className="text-lg font-medium text-[#111111]">Budget Workflow Shortcuts</h3>
                  <p className="mt-1 text-sm text-[#666666]">
                    Jump back into the active draft or review historical sync artifacts without turning Settings into a file-management screen.
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <Link to={`/hoa/${id}`}>
                    <Button variant="outline" className="border-[#E5E5E5]">
                      <Eye className="mr-2 h-4 w-4" />
                      Open Current Draft
                    </Button>
                  </Link>
                  <Link to={`/hoa/${id}/sync-history`}>
                    <Button variant="outline" className="border-[#E5E5E5]">
                      <FolderOpen className="mr-2 h-4 w-4" />
                      Open Sync History
                    </Button>
                  </Link>
                </div>
              </div>
            </div>

            <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg p-8 space-y-6">
              <div className="grid gap-6 md:grid-cols-2">
                <div data-setting-field="hoaName" className="space-y-2">
                  <Label htmlFor="hoaName">HOA Name</Label>
                  <Input
                    id="hoaName"
                    value={hoaConfig.name}
                    onChange={(e) => handleFieldChange('name', e.target.value)}
                    className="bg-white border-[#E5E5E5]"
                  />
                  {validationErrors.name && (
                    <p className="text-xs text-[#b91c1c]">{validationErrors.name}</p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="hoaId">HOA ID</Label>
                  <Input
                    id="hoaId"
                    value={hoaConfig.hoaId}
                    onChange={(e) => handleFieldChange('hoaId', e.target.value)}
                    className="bg-white border-[#E5E5E5]"
                  />
                </div>
              </div>

              <div className="grid gap-6 md:grid-cols-2">
                <div data-setting-field="fiscalYearStart" className="space-y-2">
                  <Label htmlFor="fiscalStart">Fiscal Year Start</Label>
                  <Select
                    value={hoaConfig.fiscalYearStart}
                    onValueChange={(value) => handleFieldChange('fiscalYearStart', value)}
                  >
                    <SelectTrigger id="fiscalStart" className="bg-white border-[#E5E5E5]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {MONTH_NAMES.map((month) => (
                        <SelectItem key={month} value={month}>
                          {month}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-[#737373]">
                    Month the association&apos;s fiscal year begins (calendar cycle).
                  </p>
                  {validationErrors.fiscalYearStart && (
                    <p className="text-xs text-[#b91c1c]">{validationErrors.fiscalYearStart}</p>
                  )}
                </div>
                <div data-setting-field="packageYear" className="space-y-2">
                  <Label htmlFor="packageYear">Package year</Label>
                  <Input
                    id="packageYear"
                    type="number"
                    min={1990}
                    max={2100}
                    step={1}
                    value={hoaConfig.packageYear}
                    onChange={(e) => handleFieldChange('packageYear', e.target.value)}
                    className="bg-white border-[#E5E5E5]"
                  />
                  <p className="text-xs text-[#737373]">
                    Year on disclosure PDFs, generate, budget export, and assessment
                    mapping defaults (e.g. 2026).
                  </p>
                  {validationErrors.packageYear && (
                    <p className="text-xs text-[#b91c1c]">{validationErrors.packageYear}</p>
                  )}
                </div>
              </div>

              <div className="grid gap-6 md:grid-cols-2">
                <div data-setting-field="taxId" className="space-y-2">
                  <Label htmlFor="taxId">Tax ID</Label>
                  <Input
                    id="taxId"
                    value={hoaConfig.taxId}
                    onChange={(e) => handleFieldChange('taxId', e.target.value)}
                    className="bg-white border-[#E5E5E5]"
                  />
                </div>
                <div data-setting-field="units" className="space-y-2">
                  <Label htmlFor="units">Number of Units</Label>
                  <Input
                    id="units"
                    type="number"
                    value={hoaConfig.units}
                    onChange={(e) => handleFieldChange('units', e.target.value)}
                    className="bg-white border-[#E5E5E5]"
                  />
                  {validationErrors.units && (
                    <p className="text-xs text-[#b91c1c]">{validationErrors.units}</p>
                  )}
                </div>
              </div>

              <div className="grid gap-6 md:grid-cols-2">
                <div data-setting-field="city" className="space-y-2">
                  <Label htmlFor="city">City</Label>
                  <Input
                    id="city"
                    value={hoaConfig.city}
                    onChange={(e) => handleFieldChange('city', e.target.value)}
                    className="bg-white border-[#E5E5E5]"
                    placeholder="San Francisco"
                  />
                </div>
              </div>
            </div>
          </TabsContent>


          <TabsContent value="data" className="space-y-6">
            <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg p-8 space-y-6">
              <div className="flex items-start gap-4">
                <div className="w-12 h-12 bg-white border border-[#E5E5E5] rounded-lg flex items-center justify-center">
                  <Database className="w-6 h-6 text-[#525252]" />
                </div>
                <div className="flex-1">
                  <h3 className="text-lg font-medium text-[#111111]">Export Database</h3>
                  <p className="text-sm text-[#666666] mt-1">
                    Download all AI pipeline data including feedback cases, suggestion runs, user decisions, and SOP rules as a JSON file.
                  </p>
                </div>
              </div>

              <div className="bg-white border border-[#E5E5E5] rounded-lg p-6 space-y-4">
                <div className="grid gap-4 text-sm sm:grid-cols-2">
                  <div className="text-[#666666]">Format</div>
                  <div className="text-[#111111] font-medium">JSON</div>
                  <div className="text-[#666666]">Includes</div>
                  <div className="text-[#111111]">Users, Properties, Suggestion Runs, Feedback Cases, SOP Rules</div>
                </div>
                <Button
                  onClick={async () => {
                    try {
                      const data = await exportData();
                      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                      const url = URL.createObjectURL(blob);
                      const anchor = document.createElement('a');
                      anchor.href = url;
                      anchor.download = `tri-state-export-${new Date().toISOString().slice(0, 10)}.json`;
                      anchor.click();
                      URL.revokeObjectURL(url);
                      toast.success('Database exported successfully');
                    } catch {
                      toast.error('Failed to export database. Please try again.');
                    }
                  }}
                  className="bg-[#111111] text-white hover:bg-[#262626] shadow-sm"
                >
                  <Download className="w-4 h-4 mr-2" />
                  Export All Data
                </Button>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="disclosure" className="space-y-6">
            <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg p-8">
              <HOADisclosureSettingsForm
                hoaId={hoa.id}
                packageYear={
                  hoa.portfolio_year ??
                  (Number(hoaConfig.packageYear) || undefined)
                }
                ref={disclosureFormRef}
                onReadyChange={setDisclosureReady}
                onDirtyChange={setDisclosureDirty}
              />
              <div className="mt-8 border-t border-[#e5e5e5] pt-6">
                <h3 className="text-base font-semibold text-[#1a1a1a]">
                  Disclosure package wording
                </h3>
                <p className="mt-1 max-w-2xl text-sm text-[#666666]">
                  Open the whole report and edit it like a document — the cover letter,
                  every Note, the CPA reports, and the title pages, prose and tables
                  alike. Computed figures stay linked and update themselves. Save changes
                  as the firm default for every HOA, or just for this one.
                </p>
                <Button
                  type="button"
                  className="mt-3"
                  onClick={() => setPackageLanguageOpen(true)}
                >
                  Edit disclosure package
                </Button>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="appendices" className="space-y-6">
            <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg">
              <AppendixManifestEditor hoaId={hoa.id} />
            </div>
          </TabsContent>

          <TabsContent value="packages" className="space-y-6">
            <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg">
              <AnnualPackagesPanel
                hoaId={hoa.id}
                liveAssessmentMode={hoa.assessment_mode}
                defaultPackageYear={
                  hoa.portfolio_year ??
                  (Number(hoaConfig.packageYear) || undefined)
                }
              />
            </div>
          </TabsContent>

          <TabsContent value="dre" className="space-y-6">
            <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg">
              <DREPanel hoaId={hoa.id} />
            </div>
          </TabsContent>
            </div>
          </div>
        </Tabs>
      </main>

      <BoilerplateWorkbench
        hoaId={hoa.id}
        hoaName={hoa.name}
        packageYear={
          hoa.portfolio_year ?? (Number(hoaConfig.packageYear) || undefined)
        }
        open={packageLanguageOpen}
        onClose={() => {
          setPackageLanguageOpen(false);
          // Closing the workbench explicitly ends the “return here on Back” loop.
          setResumePackageLanguage(false);
        }}
        onEditSetting={revealSettingFieldFromPackageLanguage}
      />
    </div>
  );
}
