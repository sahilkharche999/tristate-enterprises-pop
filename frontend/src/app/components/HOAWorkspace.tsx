import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { Search, Settings, LayoutList, LayoutGrid, Filter, X, LogOut, Plus } from 'lucide-react';
import { toast } from 'sonner';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './ui/alert-dialog';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { Label } from './ui/label';
import { getAppSettings, updateAppSettings } from '../api/appSettings';
import { createHOA, listHOAs } from '../api/hoa';
import { useAuth } from '../context/AuthContext';
import { getErrorMessage } from '../lib/errors';
import { getStatusColor } from '../lib/statusColors';
import { MONTH_NAMES, monthNameToNumber, toHOAViewModel, type HOAViewModel } from '../lib/hoa';

// Units are intentionally NOT collected at create time — they are populated
// from the DRE extraction once the operator approves the run. See
// dre_approval_service.approve_extraction_run for the write-back path.
interface CreateHoaFormState {
  name: string;
  fiscalYearStart: string;
  packageYear: string;
  city: string;
}

const DEFAULT_CREATE_FORM: CreateHoaFormState = {
  name: '',
  fiscalYearStart: MONTH_NAMES[0],
  packageYear: String(new Date().getFullYear()),
  city: '',
};

function formatReserveInflationRate(rate: number): string {
  const normalizedRate = Number.isFinite(rate) ? rate : 0;
  return (normalizedRate * 100).toFixed(1);
}

function parseReserveInflationRate(value: string): number | null {
  const normalizedValue = Number(value);
  if (!Number.isFinite(normalizedValue) || normalizedValue < 0) {
    return null;
  }
  return normalizedValue / 100;
}

export function HOAWorkspace() {
  const navigate = useNavigate();
  const auth = useAuth();
  const [searchQuery, setSearchQuery] = useState('');
  const [viewMode, setViewMode] = useState<'list' | 'card'>('list');
  const [filterYear, setFilterYear] = useState<string>('all');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [filterUnits, setFilterUnits] = useState<string>('all');
  const [filterCity, setFilterCity] = useState<string>('all');
  const [sortMode, setSortMode] = useState<'recent' | 'year' | 'readiness'>('recent');
  const [hoas, setHoas] = useState<HOAViewModel[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<CreateHoaFormState>(DEFAULT_CREATE_FORM);
  const [createError, setCreateError] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [globalReserveInflationRate, setGlobalReserveInflationRate] = useState('0.0');
  const [globalReserveInflationError, setGlobalReserveInflationError] = useState<string | null>(null);
  const [isSavingGlobalReserveInflation, setIsSavingGlobalReserveInflation] = useState(false);
  const [isGlobalReserveInflationOpen, setIsGlobalReserveInflationOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadPortfolio() {
      setIsLoading(true);
      setLoadError(null);
      try {
        const [response, appSettings] = await Promise.all([listHOAs(), getAppSettings()]);
        if (!cancelled) {
          setHoas(response.map(toHOAViewModel));
          setGlobalReserveInflationRate(
            formatReserveInflationRate(appSettings.global_reserve_inflation_rate),
          );
        }
      } catch (error) {
        if (!cancelled) {
          setLoadError(getErrorMessage(error, 'Failed to load the HOA portfolio.'));
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    loadPortfolio();
    return () => {
      cancelled = true;
    };
  }, []);

  const uniqueYears = Array.from(new Set(hoas.map((hoa) => hoa.year))).sort((a, b) => b - a);
  const uniqueCities = Array.from(new Set(hoas.map((hoa) => hoa.city))).sort();

  const filteredHOAs = hoas
    .filter((hoa) => {
      const matchesSearch =
        hoa.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        hoa.fiscalYear.toLowerCase().includes(searchQuery.toLowerCase()) ||
        hoa.units.toString().includes(searchQuery) ||
        hoa.year.toString().includes(searchQuery) ||
        hoa.city.toLowerCase().includes(searchQuery.toLowerCase());

      const matchesYear = filterYear === 'all' || hoa.year.toString() === filterYear;
      const matchesStatus = filterStatus === 'all' || hoa.status === filterStatus;

      let matchesUnits = true;
      if (filterUnits === 'small') matchesUnits = hoa.units < 100;
      else if (filterUnits === 'medium') matchesUnits = hoa.units >= 100 && hoa.units < 150;
      else if (filterUnits === 'large') matchesUnits = hoa.units >= 150;

      const matchesCity = filterCity === 'all' || hoa.city === filterCity;

      return matchesSearch && matchesYear && matchesStatus && matchesUnits && matchesCity;
    })
    .slice()
    .sort((a, b) => {
      if (sortMode === 'year') {
        return b.year - a.year || a.name.localeCompare(b.name);
      }
      if (sortMode === 'readiness') {
        return a.readinessPct - b.readinessPct || a.name.localeCompare(b.name);
      }
      // recently worked — nulls last
      const at = a.lastWorkedAt ? Date.parse(a.lastWorkedAt) : 0;
      const bt = b.lastWorkedAt ? Date.parse(b.lastWorkedAt) : 0;
      if (bt !== at) return bt - at;
      return a.name.localeCompare(b.name);
    });

  const activeFiltersCount = [
    filterYear !== 'all',
    filterStatus !== 'all',
    filterUnits !== 'all',
    filterCity !== 'all',
  ].filter(Boolean).length;

  const clearAllFilters = () => {
    setFilterYear('all');
    setFilterStatus('all');
    setFilterUnits('all');
    setFilterCity('all');
  };

  const handleCreateFieldChange = (field: keyof CreateHoaFormState, value: string) => {
    setCreateForm((current) => ({ ...current, [field]: value }));
    setCreateError(null);
  };

  const handleGlobalReserveInflationSave = async (): Promise<boolean> => {
    const parsedRate = parseReserveInflationRate(globalReserveInflationRate);
    if (parsedRate == null) {
      setGlobalReserveInflationError('Reserve inflation must be a non-negative number.');
      return false;
    }

    setIsSavingGlobalReserveInflation(true);
    setGlobalReserveInflationError(null);
    try {
      const response = await updateAppSettings({
        global_reserve_inflation_rate: parsedRate,
      });
      setGlobalReserveInflationRate(
        formatReserveInflationRate(response.global_reserve_inflation_rate),
      );
      toast.success('Global reserve inflation updated.');
      return true;
    } catch (error) {
      setGlobalReserveInflationError(getErrorMessage(error, 'Failed to save global reserve inflation.'));
      return false;
    } finally {
      setIsSavingGlobalReserveInflation(false);
    }
  };

  const closeCreateModal = () => {
    if (isCreating) {
      return;
    }
    setIsCreateOpen(false);
    setCreateForm(DEFAULT_CREATE_FORM);
    setCreateError(null);
  };

  const handleCreateHoa = async () => {
    const trimmedName = createForm.name.trim();
    if (!trimmedName) {
      setCreateError('HOA name is required.');
      return;
    }

    setIsCreating(true);
    setCreateError(null);
    try {
      const packageYear = Number(createForm.packageYear);
      if (
        !Number.isInteger(packageYear) ||
        packageYear < 1990 ||
        packageYear > 2100
      ) {
        setCreateError('Package year must be a whole year between 1990 and 2100.');
        setIsCreating(false);
        return;
      }
      const created = await createHOA({
        name: trimmedName,
        fiscal_year_start_month: monthNameToNumber(createForm.fiscalYearStart),
        city: createForm.city.trim() || undefined,
        portfolio_year: packageYear,
      });
      setHoas((current) => [toHOAViewModel(created), ...current]);
      setIsCreateOpen(false);
      setCreateForm(DEFAULT_CREATE_FORM);
      navigate(`/hoa/${created.id}`);
    } catch (error) {
      setCreateError(getErrorMessage(error, 'Failed to create the HOA.'));
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#fafafa]">
      <header className="border-b border-[#e5e5e5] bg-white shadow-sm sticky top-0 z-10">
        <div className="px-8 py-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-[#111111] tracking-tight">Tri-State Enterprises</h1>
            <p className="text-sm text-[#737373] mt-0.5">HOA Budget Management System</p>
          </div>
          <div className="flex items-center gap-3">
            {auth.user && (
              <span className="text-sm text-[#737373]">{auth.user.name}</span>
            )}
            <Button
              variant="outline"
              className="border-[#e5e5e5] text-[#525252] hover:bg-[#f5f5f5] hover:text-[#111111]"
              onClick={() => {
                setGlobalReserveInflationError(null);
                setIsGlobalReserveInflationOpen(true);
              }}
            >
              Reserve {globalReserveInflationRate}%
            </Button>
            <Link to="/settings">
              <Button variant="ghost" size="icon" className="hover:bg-[#f5f5f5]">
                <Settings className="w-5 h-5 text-[#525252]" />
              </Button>
            </Link>
            <Button
              variant="ghost"
              size="icon"
              className="hover:bg-[#f5f5f5]"
              onClick={async () => { await auth.logout(); navigate('/'); }}
            >
              <LogOut className="w-5 h-5 text-[#525252]" />
            </Button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-8 py-12">
        <div className="mb-6 flex flex-wrap items-end gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-[#a3a3a3]" />
            <Input
              type="text"
              placeholder="Search by name, city, year, units, or fiscal year..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-12 h-12 bg-white border-[#e5e5e5] text-[#111111] placeholder:text-[#a3a3a3] focus:border-[#737373] focus:ring-1 focus:ring-[#737373] shadow-sm"
            />
          </div>
          <div className="flex items-center gap-1 bg-white border border-[#e5e5e5] rounded-lg p-1 shadow-sm">
            <Button
              variant={viewMode === 'list' ? 'default' : 'ghost'}
              size="sm"
              onClick={() => setViewMode('list')}
              className={
                viewMode === 'list'
                  ? 'bg-[#111111] text-white hover:bg-[#262626]'
                  : 'text-[#525252] hover:bg-[#f5f5f5] hover:text-[#111111]'
              }
            >
              <LayoutList className="w-4 h-4" />
            </Button>
            <Button
              variant={viewMode === 'card' ? 'default' : 'ghost'}
              size="sm"
              onClick={() => setViewMode('card')}
              className={
                viewMode === 'card'
                  ? 'bg-[#111111] text-white hover:bg-[#262626]'
                  : 'text-[#525252] hover:bg-[#f5f5f5] hover:text-[#111111]'
              }
            >
              <LayoutGrid className="w-4 h-4" />
            </Button>
          </div>
          <Button
            onClick={() => setIsCreateOpen(true)}
            className="bg-[#111111] text-white hover:bg-[#262626] shadow-sm"
          >
            <Plus className="w-4 h-4 mr-2" />
            New HOA
          </Button>
        </div>

        <div className="mb-6 flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2 text-sm text-[#525252]">
            <Filter className="w-4 h-4" />
            <span className="font-medium">Filters:</span>
          </div>

          <Select value={filterYear} onValueChange={setFilterYear}>
            <SelectTrigger className="w-[140px] h-9 bg-white border-[#e5e5e5]">
              <SelectValue placeholder="Year" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Years</SelectItem>
              {uniqueYears.map((year) => (
                <SelectItem key={year} value={year.toString()}>
                  {year}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={filterStatus} onValueChange={setFilterStatus}>
            <SelectTrigger className="w-[180px] h-9 bg-white border-[#e5e5e5]">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Status</SelectItem>
              <SelectItem value="Not Started">Not Started</SelectItem>
              <SelectItem value="In Progress">In Progress</SelectItem>
              <SelectItem value="Ready for package">Ready for package</SelectItem>
            </SelectContent>
          </Select>

          <Select value={sortMode} onValueChange={(v) => setSortMode(v as typeof sortMode)}>
            <SelectTrigger className="w-[180px] h-9 bg-white border-[#e5e5e5]">
              <SelectValue placeholder="Sort" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="recent">Recently worked</SelectItem>
              <SelectItem value="year">Package year</SelectItem>
              <SelectItem value="readiness">Readiness % (low first)</SelectItem>
            </SelectContent>
          </Select>

          <Select value={filterUnits} onValueChange={setFilterUnits}>
            <SelectTrigger className="w-[160px] h-9 bg-white border-[#e5e5e5]">
              <SelectValue placeholder="Units" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Units</SelectItem>
              <SelectItem value="small">Small (&lt; 100)</SelectItem>
              <SelectItem value="medium">Medium (100-149)</SelectItem>
              <SelectItem value="large">Large (≥ 150)</SelectItem>
            </SelectContent>
          </Select>

          <Select value={filterCity} onValueChange={setFilterCity}>
            <SelectTrigger className="w-[160px] h-9 bg-white border-[#e5e5e5]">
              <SelectValue placeholder="City" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Cities</SelectItem>
              {uniqueCities.map((city) => (
                <SelectItem key={city} value={city}>
                  {city}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {activeFiltersCount > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={clearAllFilters}
              className="h-9 text-[#525252] hover:text-[#111111] hover:bg-[#f5f5f5]"
            >
              <X className="w-4 h-4 mr-1" />
              Clear ({activeFiltersCount})
            </Button>
          )}

          <div className="ml-auto text-sm text-[#737373]">
            {filteredHOAs.length} {filteredHOAs.length === 1 ? 'HOA' : 'HOAs'}
          </div>
        </div>

        {isLoading ? (
          <div className="bg-white border border-[#e5e5e5] rounded-lg p-8 text-sm text-[#737373] shadow-sm">
            Loading HOA portfolio...
          </div>
        ) : loadError ? (
          <div className="bg-white border border-[#e5e5e5] rounded-lg p-8 text-sm text-[#b91c1c] shadow-sm">
            {loadError}
          </div>
        ) : viewMode === 'list' ? (
          <div className="space-y-3">
            {filteredHOAs.map((hoa) => (
              <Link
                key={hoa.id}
                to={`/hoa/${hoa.id}`}
                className="block bg-white border border-[#e5e5e5] rounded-lg p-6 hover:border-[#737373] hover:shadow-md transition-all duration-200"
              >
                <div className="flex items-center justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <h3 className="text-lg font-semibold text-[#111111] mb-1.5">{hoa.name}</h3>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-[#737373]">
                      <span>{hoa.city}</span>
                      <span className="text-[#d4d4d4]">•</span>
                      <span>Year: {hoa.year}</span>
                      <span className="text-[#d4d4d4]">•</span>
                      <span>Fiscal: {hoa.fiscalYear}</span>
                      <span className="text-[#d4d4d4]">•</span>
                      <span>{hoa.units > 0 ? `${hoa.units} Units` : 'Pending DRE'}</span>
                      <span className="text-[#d4d4d4]">•</span>
                      <span>
                        Ready {hoa.readinessPct}%
                        {hoa.readinessTotal > 0
                          ? ` (${hoa.readinessDone}/${hoa.readinessTotal})`
                          : ''}
                      </span>
                    </div>
                    {hoa.nextActionLabel ? (
                      <p className="mt-2 text-sm font-medium text-[#111111]">
                        {hoa.nextActionLabel}
                      </p>
                    ) : null}
                  </div>
                  <div className={`shrink-0 px-3 py-1.5 rounded-md text-xs font-medium border ${getStatusColor(hoa.status)}`}>
                    {hoa.status}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredHOAs.map((hoa) => (
              <Link
                key={hoa.id}
                to={`/hoa/${hoa.id}`}
                className="block bg-white border border-[#e5e5e5] rounded-lg p-6 hover:border-[#737373] hover:shadow-lg transition-all duration-200"
              >
                <div className="space-y-4">
                  <div>
                    <h3 className="text-lg font-semibold text-[#111111] mb-2 line-clamp-2">{hoa.name}</h3>
                    <div className={`inline-flex px-3 py-1.5 rounded-md text-xs font-medium border ${getStatusColor(hoa.status)}`}>
                      {hoa.status}
                    </div>
                    {hoa.nextActionLabel ? (
                      <p className="mt-2 text-sm font-medium text-[#111111] line-clamp-2">
                        {hoa.nextActionLabel}
                      </p>
                    ) : null}
                  </div>
                  <div className="pt-4 border-t border-[#e5e5e5] space-y-2">
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-[#737373]">City</span>
                      <span className="font-medium text-[#111111]">{hoa.city}</span>
                    </div>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-[#737373]">Year</span>
                      <span className="font-medium text-[#111111]">{hoa.year}</span>
                    </div>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-[#737373]">Readiness</span>
                      <span className="font-medium text-[#111111]">
                        {hoa.readinessPct}%
                        {hoa.readinessTotal > 0
                          ? ` (${hoa.readinessDone}/${hoa.readinessTotal})`
                          : ''}
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-[#737373]">Total Units</span>
                      <span className="font-medium text-[#111111]">
                        {hoa.units > 0 ? hoa.units : 'Pending DRE'}
                      </span>
                    </div>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </main>

      <AlertDialog open={isGlobalReserveInflationOpen} onOpenChange={setIsGlobalReserveInflationOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Global Reserve Inflation Rate</AlertDialogTitle>
            <AlertDialogDescription>
              This percentage applies across all HOAs in the workspace.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <Label htmlFor="workspaceReserveInflation">Reserve Inflation (%)</Label>
            <Input
              id="workspaceReserveInflation"
              type="number"
              step="0.1"
              min="0"
              value={globalReserveInflationRate}
              onChange={(e) => {
                setGlobalReserveInflationRate(e.target.value);
                setGlobalReserveInflationError(null);
              }}
              className="bg-white border-[#E5E5E5]"
            />
            {globalReserveInflationError ? (
              <p className="text-sm text-[#b91c1c]">{globalReserveInflationError}</p>
            ) : null}
          </div>
          <AlertDialogFooter>
            <Button
              variant="outline"
              className="border-[#e5e5e5]"
              onClick={() => {
                setGlobalReserveInflationError(null);
                setIsGlobalReserveInflationOpen(false);
              }}
              disabled={isSavingGlobalReserveInflation}
            >
              Cancel
            </Button>
            <Button
              onClick={async () => {
                const saved = await handleGlobalReserveInflationSave();
                if (saved) {
                  setIsGlobalReserveInflationOpen(false);
                }
              }}
              disabled={isSavingGlobalReserveInflation}
              className="bg-[#111111] text-white hover:bg-[#262626]"
            >
              {isSavingGlobalReserveInflation ? 'Saving...' : 'Save'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {isCreateOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-6">
          <div className="w-full max-w-2xl rounded-2xl border border-[#e5e5e5] bg-white shadow-2xl">
            <div className="flex items-start justify-between border-b border-[#e5e5e5] px-8 py-6">
              <div>
                <h2 className="text-xl font-semibold text-[#111111]">Create New HOA</h2>
                <p className="mt-1 text-sm text-[#666666]">
                  Add the HOA with the minimum setup needed to start the upload workflow.
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="hover:bg-[#f5f5f5]"
                onClick={closeCreateModal}
                disabled={isCreating}
              >
                <X className="w-5 h-5 text-[#525252]" />
              </Button>
            </div>

            <div className="space-y-6 px-8 py-6">
              <div className="space-y-2">
                <Label htmlFor="createHoaName">HOA Name</Label>
                <Input
                  id="createHoaName"
                  value={createForm.name}
                  onChange={(e) => handleCreateFieldChange('name', e.target.value)}
                  className="bg-white border-[#E5E5E5]"
                  placeholder="North Harbor HOA"
                  disabled={isCreating}
                />
              </div>

              <div className="rounded-md border border-dashed border-[#E5E5E5] bg-[#FAFAFA] px-3 py-2 text-xs text-[#737373]">
                Units, groups, and assessment math populate automatically when
                you upload + approve a DRE for this HOA. No need to type them
                in now.
              </div>

              <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="createHoaCity">City</Label>
                  <Input
                    id="createHoaCity"
                    value={createForm.city}
                    onChange={(e) => handleCreateFieldChange('city', e.target.value)}
                    className="bg-white border-[#E5E5E5]"
                    placeholder="San Francisco"
                    disabled={isCreating}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="createHoaFiscalStart">Fiscal Year Start</Label>
                  <Select
                    value={createForm.fiscalYearStart}
                    onValueChange={(value) => handleCreateFieldChange('fiscalYearStart', value)}
                    disabled={isCreating}
                  >
                    <SelectTrigger id="createHoaFiscalStart" className="bg-white border-[#E5E5E5]">
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
                </div>

                <div className="space-y-2">
                  <Label htmlFor="createHoaPackageYear">Package year</Label>
                  <Input
                    id="createHoaPackageYear"
                    type="number"
                    min={1990}
                    max={2100}
                    step={1}
                    value={createForm.packageYear}
                    onChange={(e) =>
                      handleCreateFieldChange('packageYear', e.target.value)
                    }
                    className="bg-white border-[#E5E5E5]"
                    disabled={isCreating}
                  />
                  <p className="text-xs text-[#737373]">
                    Year on disclosure PDFs (e.g. 2026).
                  </p>
                </div>

              </div>

              {createError ? (
                <div className="rounded-lg border border-[#fecaca] bg-[#fef2f2] px-4 py-3 text-sm text-[#b91c1c]">
                  {createError}
                </div>
              ) : null}
            </div>

            <div className="flex items-center justify-end gap-3 border-t border-[#e5e5e5] px-8 py-6">
              <Button
                variant="outline"
                className="border-[#e5e5e5]"
                onClick={closeCreateModal}
                disabled={isCreating}
              >
                Cancel
              </Button>
              <Button
                onClick={handleCreateHoa}
                className="bg-[#111111] text-white hover:bg-[#262626] shadow-sm disabled:opacity-60"
                disabled={isCreating}
              >
                {isCreating ? 'Creating...' : 'Create HOA'}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
