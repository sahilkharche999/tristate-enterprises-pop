import { useState, useEffect, useRef } from 'react';
import { useParams, Link } from 'react-router';
import { ArrowLeft, Settings, Upload, Download, FileText } from 'lucide-react';
import { Button } from './ui/button';
import { hoaList, type LineItem, type AISuggestion, type AISuggestionResponse } from '../data/mockData';
import { EnrichedView } from './EnrichedView';
import { BudgetView } from './BudgetView';
import { AISuggestionMode } from './AISuggestionMode';
import { toast } from 'sonner';
import { generateBudget, toNum } from '../api/macros';
import type { SheetTable } from '../api/macros';
import { formatTimestamp } from '../lib/budget';
import { exportEnrichedBudget } from '../lib/exportBudget';
import { computeTimingInputs, parseMonth } from '../lib/fiscalYear';

// ─── Utility ─────────────────────────────────────────────────────────────────

export function parseEnrichedResponse(enriched: SheetTable): LineItem[] {
  const items: LineItem[] = [];
  let currentCategory: LineItem['category'] = 'operating';
  let idCounter = 0;

  for (const row of enriched.rows) {
    const colA = row[0];
    const colB = row[1];

    // Skip rows where both col A and col B are empty
    if ((colA == null || colA === '') && (colB == null || colB === '')) continue;

    // Section header: col A non-empty, col B empty
    if (colA != null && colA !== '' && (colB == null || colB === '')) {
      const header = String(colA).toLowerCase();
      if (header.includes('income')) currentCategory = 'income';
      else if (header.includes('reserve')) currentCategory = 'reserve';
      else currentCategory = 'operating';
      continue;
    }

    // Line item: col B non-empty
    if (colB != null && colB !== '') {
      // Backend blanks AK:AZ (col AL = index 37) to null for reserve-study and
      // reserve-labeled rows — those are excluded from the board-adjustable flow.
      // null = excluded (show read-only); 0 = valid item whose YTD happens to be zero.
      const excluded = row[37] == null;

      const colBStr = String(colB);
      const labelParts = colBStr.split(' - ');
      const parsedCode = labelParts.length >= 2 ? parseInt(labelParts[0].trim(), 10) : NaN;

      items.push({
        id: `item-${++idCounter}`,
        category: currentCategory,
        name: colBStr,
        ytdActual: toNum(row[19]),           // col T  (index 19)
        annualBudget: toNum(row[32]),        // col AG (index 32)
        percentChange: toNum(row[38]) * 100, // col AM (index 38), decimal → display %
        projection: excluded ? undefined : toNum(row[37]), // col AL (index 37)
        readOnly: excluded || undefined,
        accountCode: !isNaN(parsedCode) ? parsedCode : undefined,
        label: colBStr,
      });
    }
  }

  return items;
}

// ─── Component ────────────────────────────────────────────────────────────────

interface BudgetScreenProps {
  lineItems: LineItem[];
  onLineItemsUpdate: (lineItems: LineItem[]) => void;
  onGenerateBudget: () => void;
  onFileUploaded?: (file: File) => void;
  budgetGenerated: boolean;
  isGenerating?: boolean;
  initialView?: 'enriched' | 'budget' | 'ai';
  fileAlreadyUploaded?: boolean;
  savedAiResponse?: AISuggestionResponse | null;
  onAiResponseChange?: (response: AISuggestionResponse | null) => void;
}

export function BudgetScreen({
  lineItems,
  onLineItemsUpdate,
  onGenerateBudget,
  onFileUploaded,
  budgetGenerated,
  isGenerating = false,
  initialView = 'enriched',
  fileAlreadyUploaded = false,
  savedAiResponse = null,
  onAiResponseChange,
}: BudgetScreenProps) {
  const { id } = useParams<{ id: string }>();
  const hoa = hoaList.find((h) => h.id === id);

  const [uploadState, setUploadState] = useState<'initial' | 'uploading' | 'complete'>(fileAlreadyUploaded ? 'complete' : 'initial');
  const [currentView, setCurrentView] = useState<'enriched' | 'budget' | 'ai'>(initialView);
  const [globalNote, setGlobalNote] = useState('');
  const [lastSaved, setLastSaved] = useState(new Date());
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [aiResponse, _setAiResponse] = useState<AISuggestionResponse | null>(savedAiResponse);
  const setAiResponse = (r: AISuggestionResponse | null) => {
    _setAiResponse(r);
    onAiResponseChange?.(r);
  };
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [statementMonth, setStatementMonth] = useState<number | null>(null);
  const [uploadedGrowthFactor, setUploadedGrowthFactor] = useState<number | null>(null);

  // Auto-save effect
  useEffect(() => {
    const timer = setTimeout(() => {
      setLastSaved(new Date());
    }, 1000);
    return () => clearTimeout(timer);
  }, [lineItems, globalNote]);

  const handleSelectFile = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    // Reset input so the same file can be re-selected if needed
    e.target.value = '';

    setUploadState('uploading');
    try {
      const result = await generateBudget({
        file,
        enrichOnly: true,
        fiscalYearStartMonth: hoa ? parseMonth(hoa.fiscalYearStart) : 1,
      });
      const parsed = parseEnrichedResponse(result.enriched);
      onLineItemsUpdate(parsed);
      if (result.statement_month) setStatementMonth(result.statement_month);
      setUploadedGrowthFactor(result.growth_factor ?? null);
      onFileUploaded?.(file);
      setUploadState('complete');
      toast.success('Income statement parsed successfully');
    } catch (err: unknown) {
      const apiErr = err as { status?: number; message?: string };
      toast.error(apiErr?.message || 'Upload failed. Please try again.');
      setUploadState('initial');
    }
  };

  const handlePercentChange = (itemId: string, newPercent: number) => {
    onLineItemsUpdate(
      lineItems.map((item) =>
        item.id === itemId ? { ...item, percentChange: newPercent } : item
      )
    );
  };

  const handleNoteUpdate = (itemId: string, title: string, body: string) => {
    onLineItemsUpdate(
      lineItems.map((item) =>
        item.id === itemId ? { ...item, note: { title, body } } : item
      )
    );
  };

  const handleFetchAISuggestions = async () => {
    if (!hoa) return;
    if (aiLoading) return;
    setAiLoading(true);
    setAiError(null);
    try {
      const { getAISuggestions } = await import('../api/macros');
      const totalBudget = lineItems.reduce((s, i) => s + (i.annualBudget || 0), 0);
      const totalYtd = lineItems.reduce((s, i) => s + (i.ytdActual || 0), 0);
      const timing = computeTimingInputs(hoa, statementMonth ?? undefined);
      const result = await getAISuggestions({
        lineItems,
        propertyName: hoa.name || 'HOA',
        totalAnnualBudget: totalBudget,
        totalYtdActuals: totalYtd,
        ...timing,
        growthFactor: uploadedGrowthFactor ?? timing.growthFactor,
        fiscalYear: hoa.year,
      });
      setAiResponse(result);
    } catch (err: unknown) {
      const apiErr = err as { message?: string };
      setAiError(apiErr?.message || 'AI service unavailable');
    } finally {
      setAiLoading(false);
    }
  };

  const handleApplyAISuggestions = (selectedSuggestions: AISuggestion[]) => {
    onLineItemsUpdate(
      lineItems.map((item) => {
        const suggestion = selectedSuggestions.find((s) => s.lineItemId === item.id);
        if (suggestion) {
          return { ...item, percentChange: suggestion.suggestedPercent };
        }
        return item;
      })
    );
    setCurrentView('enriched');
    toast.success(`Applied ${selectedSuggestions.length} AI suggestions`);
  };

  if (!hoa) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <p className="text-[#666666]">HOA not found</p>
      </div>
    );
  }

  // Upload State
  if (uploadState === 'initial' || uploadState === 'uploading') {
    return (
      <div className="min-h-screen bg-[#fafafa]">
        {/* Header */}
        <header className="border-b border-[#e5e5e5] bg-white sticky top-0 z-10 shadow-sm">
          <div className="px-8 py-6 flex items-center justify-between">
            <div className="flex items-center gap-6">
              <Link to="/workspace" className="p-2 hover:bg-[#f5f5f5] rounded-lg transition-colors">
                <ArrowLeft className="w-5 h-5 text-[#525252]" />
              </Link>
              <div>
                <h1 className="text-xl font-semibold text-[#111111]">{hoa.name}</h1>
                <p className="text-sm text-[#737373]">Fiscal Year: {hoa.fiscalYear}</p>
              </div>
            </div>
            <Link to={`/hoa/${id}/settings`}>
              <Button variant="ghost" size="icon" className="hover:bg-[#f5f5f5]">
                <Settings className="w-5 h-5 text-[#525252]" />
              </Button>
            </Link>
          </div>
        </header>

        {/* Upload Card */}
        <main className="max-w-3xl mx-auto px-8 py-16">
          <div className="bg-white border-2 border-dashed border-[#d4d4d4] rounded-xl p-16 text-center shadow-sm">
            <div className="flex flex-col items-center gap-6">
              <div className="w-16 h-16 bg-[#f5f5f5] rounded-full flex items-center justify-center">
                <Upload className="w-8 h-8 text-[#525252]" />
              </div>
              <div>
                <h2 className="text-xl font-semibold text-[#111111] mb-2">Upload Income Statement</h2>
                <p className="text-sm text-[#737373]">
                  {uploadState === 'uploading' ? 'Parsing document...' : 'Upload your Excel or CSV file to begin'}
                </p>
              </div>
              {/* Hidden file input */}
              <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.xls"
                className="hidden"
                onChange={handleFileChange}
              />
              <Button
                onClick={handleSelectFile}
                disabled={uploadState === 'uploading'}
                className="bg-[#111111] text-white hover:bg-[#262626] px-6 py-2.5 shadow-sm"
              >
                {uploadState === 'uploading' ? 'Processing...' : 'Select File'}
              </Button>
            </div>
          </div>
        </main>
      </div>
    );
  }

  // Main Budget Screen
  return (
    <div className="min-h-screen bg-[#fafafa]">
      {/* Sticky Header */}
      <header className="border-b border-[#e5e5e5] bg-white sticky top-0 z-10 shadow-sm">
        <div className="px-8 py-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-6">
              <Link to="/workspace" className="p-2 hover:bg-[#f5f5f5] rounded-lg transition-colors">
                <ArrowLeft className="w-5 h-5 text-[#525252]" />
              </Link>
              <div>
                <h1 className="text-xl font-semibold text-[#111111]">{hoa.name}</h1>
                <div className="flex items-center gap-3 mt-1">
                  <p className="text-sm text-[#737373]">Fiscal Year: {hoa.fiscalYear}</p>
                  <span className="text-[#d4d4d4]">•</span>
                  <p className="text-xs text-[#737373]">Macro v1.2</p>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="text-right">
                <span className="text-xs text-[#a3a3a3]">Auto-saved</span>
                <p className="text-sm text-[#525252] font-medium">{formatTimestamp(lastSaved)}</p>
              </div>
              <div className="h-8 w-px bg-[#e5e5e5]"></div>
              <Link to={`/hoa/${id}/sync-history`}>
                <Button variant="outline" size="sm" className="border-[#d4d4d4] text-[#111111] hover:bg-[#f5f5f5] hover:border-[#a3a3a3] font-medium px-4">
                  View Past Sync
                </Button>
              </Link>
              <Link to={`/hoa/${id}/settings`}>
                <Button variant="ghost" size="icon" className="hover:bg-[#f5f5f5]">
                  <Settings className="w-5 h-5 text-[#525252]" />
                </Button>
              </Link>
            </div>
          </div>

          {/* Global Context Note */}
          <div className="border-t border-[#e5e5e5] pt-4">
            <details className="group">
              <summary className="cursor-pointer text-sm font-medium text-[#111111] flex items-center gap-2 hover:text-[#525252] transition-colors">
                <FileText className="w-4 h-4" />
                Context Note
                <span className="text-[#737373] text-xs ml-2 font-normal">
                  (Strategic notes, board decisions, inflation assumptions)
                </span>
              </summary>
              <div className="mt-4">
                <textarea
                  value={globalNote}
                  onChange={(e) => setGlobalNote(e.target.value)}
                  placeholder="Add strategic fiscal year notes, board decisions, inflation assumptions..."
                  className="w-full min-h-24 p-4 bg-white border border-[#e5e5e5] rounded-lg text-sm text-[#111111] placeholder:text-[#a3a3a3] resize-y focus:border-[#737373] focus:ring-1 focus:ring-[#737373] shadow-sm"
                />
              </div>
            </details>
          </div>
        </div>
      </header>

      {/* Action Bar */}
      <div className="border-b border-[#e5e5e5] bg-white sticky top-[140px] z-20 shadow-md">
        <div className="px-8 py-5 flex items-center justify-between backdrop-blur-sm bg-white/95">
          <div className="flex items-center gap-2">
            <Button
              variant={currentView === 'enriched' ? 'default' : 'outline'}
              onClick={() => setCurrentView('enriched')}
              className={
                currentView === 'enriched'
                  ? 'bg-[#111111] text-white hover:bg-[#262626] shadow-sm'
                  : 'border-[#e5e5e5] text-[#525252] hover:bg-[#f5f5f5] hover:border-[#737373]'
              }
            >
              View Enriched
            </Button>
            <Button
              variant={currentView === 'budget' ? 'default' : 'outline'}
              onClick={() => setCurrentView('budget')}
              className={
                currentView === 'budget'
                  ? 'bg-[#111111] text-white hover:bg-[#262626] shadow-sm'
                  : 'border-[#e5e5e5] text-[#525252] hover:bg-[#f5f5f5] hover:border-[#737373]'
              }
            >
              View Budget
            </Button>
            <Button
              variant={currentView === 'ai' ? 'default' : 'outline'}
              onClick={() => { setCurrentView('ai'); if (!aiResponse && !aiLoading) handleFetchAISuggestions(); }}
              className={
                currentView === 'ai'
                  ? 'bg-[#111111] text-white hover:bg-[#262626] shadow-sm'
                  : 'border-[#e5e5e5] text-[#525252] hover:bg-[#f5f5f5] hover:border-[#737373]'
              }
            >
              AI Suggested % Change
            </Button>
            <Button
              variant="outline"
              className="border-[#e5e5e5] text-[#525252] hover:bg-[#f5f5f5] hover:border-[#737373]"
              onClick={() => exportEnrichedBudget(lineItems, hoa?.name ?? 'Budget')}
            >
              <Download className="w-4 h-4 mr-2" />
              Download Enriched
            </Button>
          </div>
          {currentView === 'enriched' && (
            <Button
              onClick={onGenerateBudget}
              disabled={isGenerating}
              className="bg-[#111111] text-white hover:bg-[#262626] shadow-sm"
            >
              {isGenerating ? 'Generating...' : budgetGenerated ? 'Regenerate Budget' : 'Generate Budget'}
            </Button>
          )}
        </div>
      </div>

      {/* Main Content */}
      <main className="px-8 py-8">
        {currentView === 'enriched' && (
          <EnrichedView
            lineItems={lineItems}
            onPercentChange={handlePercentChange}
            onNoteUpdate={handleNoteUpdate}
            units={hoa.units}
          />
        )}
        {currentView === 'budget' && <BudgetView lineItems={lineItems} units={hoa.units} />}
        {currentView === 'ai' && (
          <AISuggestionMode
            aiResponse={aiResponse}
            lineItems={lineItems}
            loading={aiLoading}
            error={aiError}
            onApply={handleApplyAISuggestions}
            onRefetch={handleFetchAISuggestions}
          />
        )}
      </main>
    </div>
  );
}
