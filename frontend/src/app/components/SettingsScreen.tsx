import { useEffect, useRef, useState } from 'react';
import { useParams, Link, useSearchParams } from 'react-router';
import {
  ArrowLeft,
  Archive,
  BookOpen,
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
import { getKnowledgeBaseFolders } from '../data/mockData';
import { toast } from 'sonner';
import { exportData } from '../api/macros';
import { getHOA, updateHOA, type HOARecord } from '../api/hoa';
import { getErrorMessage } from '../lib/errors';
import { MONTH_NAMES, monthNameToNumber, monthNumberToName } from '../lib/hoa';
import { HOADisclosureSettingsForm, type HOADisclosureSettingsFormHandle } from './HOADisclosureSettingsForm';
import { AppendixManifestEditor } from './AppendixManifestEditor';
import { AnnualPackagesPanel } from './AnnualPackagesPanel';
import { DREPanel } from './DREPanel';

interface SettingsFormState {
  name: string;
  hoaId: string;
  fiscalYearStart: string;
  taxId: string;
  units: string;
  city: string;
  allocationType: string;
  driveFolderPath: string;
}

type ValidationField = 'name' | 'units' | 'fiscalYearStart';
type ValidationErrors = Partial<Record<ValidationField, string>>;
type SettingsSection = 'database' | 'disclosure' | 'appendices' | 'dre' | 'packages' | 'knowledge' | 'data';

const SETTINGS_SECTIONS: SettingsSection[] = [
  'database',
  'disclosure',
  'appendices',
  'dre',
  'packages',
  'knowledge',
  'data',
];

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
        helper: 'Identity, fiscal year, units',
        icon: Database,
      },
      {
        value: 'disclosure',
        label: 'Disclosure Defaults',
        helper: 'Package language and contacts',
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
        value: 'knowledge',
        label: 'Knowledge Base',
        helper: 'Reference documents',
        icon: BookOpen,
      },
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
  return errors;
}

export function SettingsScreen() {
  const { id } = useParams<{ id: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [hoa, setHoa] = useState<HOARecord | null>(null);
  const [hoaConfig, setHoaConfig] = useState<SettingsFormState>(DEFAULT_FORM);
  const [validationErrors, setValidationErrors] = useState<ValidationErrors>({});
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const disclosureFormRef = useRef<HOADisclosureSettingsFormHandle>(null);
  const requestedSection = searchParams.get('section');
  const selectedSection = SETTINGS_SECTIONS.includes(requestedSection as SettingsSection)
    ? (requestedSection as SettingsSection)
    : 'database';

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
          setHoaConfig((current) => buildFormState(response, current));
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

  const knowledgeBaseFolders = id ? getKnowledgeBaseFolders(id) : [];

  const handleSectionChange = (value: string) => {
    const nextSection = SETTINGS_SECTIONS.includes(value as SettingsSection)
      ? (value as SettingsSection)
      : 'database';
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (nextSection === 'database') {
        next.delete('section');
      } else {
        next.set('section', nextSection);
      }
      return next;
    });
  };

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
      await disclosureFormRef.current?.save();
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
        ...(hoaConfig.hoaId.trim() ? { hoa_code: hoaConfig.hoaId.trim() } : {}),
        ...(hoaConfig.taxId.trim() ? { tax_id: hoaConfig.taxId.trim() } : {}),
        ...(hoaConfig.units.trim() ? { units: Number(hoaConfig.units) } : {}),
        city: hoaConfig.city.trim(),
      };
      const savedHoa = await updateHOA(id, payload);
      setHoa(savedHoa);
      setHoaConfig((current) => buildFormState(savedHoa, current));
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
            <Link to={`/hoa/${id}`} className="p-2 hover:bg-[#f5f5f5] rounded-lg transition-colors">
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
                <div className="space-y-2">
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
                <div className="space-y-2">
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
                  {validationErrors.fiscalYearStart && (
                    <p className="text-xs text-[#b91c1c]">{validationErrors.fiscalYearStart}</p>
                  )}
                </div>
              </div>

              <div className="grid gap-6 md:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="taxId">Tax ID</Label>
                  <Input
                    id="taxId"
                    value={hoaConfig.taxId}
                    onChange={(e) => handleFieldChange('taxId', e.target.value)}
                    className="bg-white border-[#E5E5E5]"
                  />
                </div>
                <div className="space-y-2">
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
                <div className="space-y-2">
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
              <HOADisclosureSettingsForm hoaId={hoa.id} ref={disclosureFormRef} />
            </div>
          </TabsContent>

          <TabsContent value="appendices" className="space-y-6">
            <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg">
              <AppendixManifestEditor hoaId={hoa.id} />
            </div>
          </TabsContent>

          <TabsContent value="packages" className="space-y-6">
            <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg">
              <AnnualPackagesPanel hoaId={hoa.id} />
            </div>
          </TabsContent>

          <TabsContent value="dre" className="space-y-6">
            <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg">
              <DREPanel hoaId={hoa.id} />
            </div>
          </TabsContent>

          <TabsContent value="knowledge" className="space-y-6">
            <div className="grid gap-6 xl:grid-cols-4">
              <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg p-4 xl:col-span-1">
                <h3 className="text-sm font-medium text-[#111111] mb-4">Folders</h3>
                <div className="space-y-2">
                  {knowledgeBaseFolders.map((folder) => (
                    <button
                      key={folder.id}
                      onClick={() => setSelectedFolder(folder.id)}
                      className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                        selectedFolder === folder.id
                          ? 'bg-[#000000] text-white'
                          : 'text-[#111111] hover:bg-white'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <FolderOpen className="w-4 h-4" />
                        <span>{folder.name}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="min-w-0 xl:col-span-3">
                {selectedFolder ? (
                  <div className="overflow-x-auto rounded-lg border border-[#E5E5E5] bg-[#F7F7F7]">
                    <div className="px-6 py-4 border-b border-[#E5E5E5] bg-white">
                      <h3 className="text-lg font-medium text-[#111111]">
                        {knowledgeBaseFolders.find((folder) => folder.id === selectedFolder)?.name}
                      </h3>
                    </div>
                    <table className="w-full">
                      <thead className="bg-[#F7F7F7] border-b border-[#E5E5E5]">
                        <tr>
                          <th className="text-left px-6 py-3 text-sm font-medium text-[#111111]">File Name</th>
                          <th className="text-left px-6 py-3 text-sm font-medium text-[#111111]">Year</th>
                          <th className="text-left px-6 py-3 text-sm font-medium text-[#111111]">Status</th>
                          <th className="text-right px-6 py-3 text-sm font-medium text-[#111111]">Action</th>
                        </tr>
                      </thead>
                      <tbody className="bg-white">
                        {knowledgeBaseFolders
                          .find((folder) => folder.id === selectedFolder)
                          ?.files.map((file) => (
                            <tr key={file.id} className="border-b border-[#E5E5E5] hover:bg-[#FAFAFA]">
                              <td className="px-6 py-4 text-sm text-[#111111]">{file.name}</td>
                              <td className="px-6 py-4 text-sm text-[#666666]">{file.year}</td>
                              <td className="px-6 py-4 text-sm text-[#666666]">{file.status}</td>
                              <td className="px-6 py-4 text-right">
                                <div className="flex items-center justify-end gap-1">
                                  <Button variant="ghost" size="sm" className="text-[#111111] hover:bg-[#F7F7F7]">
                                    <Eye className="w-4 h-4" />
                                  </Button>
                                  <Button variant="ghost" size="sm" className="text-[#111111] hover:bg-[#F7F7F7]">
                                    <Download className="w-4 h-4" />
                                  </Button>
                                </div>
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg h-96 flex items-center justify-center">
                    <p className="text-[#666666]">Select a folder to view files</p>
                  </div>
                )}
              </div>
            </div>
          </TabsContent>
            </div>
          </div>
        </Tabs>
      </main>
    </div>
  );
}
