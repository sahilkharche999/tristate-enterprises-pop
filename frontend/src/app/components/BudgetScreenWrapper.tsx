import { useState } from 'react';
import { useSearchParams } from 'react-router';
import { BudgetScreen } from './BudgetScreen';
import { GeneratedBudgetScreen } from './GeneratedBudgetScreen';
import { initialLineItems, type LineItem } from '../data/mockData';
import { toast } from 'sonner';
import { generateBudget } from '../api/macros';
import type { SheetTable } from '../api/macros';

function buildPercentChangesMap(lineItems: LineItem[]): Record<string, number> {
  const map: Record<string, number> = {};
  for (const item of lineItems) {
    map[item.name] = item.percentChange / 100;
  }
  return map;
}

export function BudgetScreenWrapper() {
  const [searchParams, setSearchParams] = useSearchParams();
  const showGeneratedBudget = searchParams.get('generated') === 'true';
  const initialView = searchParams.get('view') || 'enriched';

  const [lineItems, setLineItems] = useState<LineItem[]>(initialLineItems);
  const [budgetVersion, setBudgetVersion] = useState(0);
  const [budgetGeneratedAt, setBudgetGeneratedAt] = useState<Date>(new Date());
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [budgetPreview, setBudgetPreview] = useState<SheetTable | null>(null);
  const [growthFactor, setGrowthFactor] = useState<number | undefined>(undefined);
  const [growthFactorNote, setGrowthFactorNote] = useState<string | undefined>(undefined);
  const [isGenerating, setIsGenerating] = useState(false);

  const runGenerateBudget = async (navigateToPreview: boolean) => {
    if (!uploadedFile) {
      toast.error('No file uploaded. Please upload an income statement first.');
      return;
    }
    setIsGenerating(true);
    try {
      const result = await generateBudget({
        file: uploadedFile,
        enrichOnly: false,
        percentChanges: buildPercentChangesMap(lineItems),
      });
      const newVersion = budgetVersion + 1;
      setBudgetVersion(newVersion);
      setBudgetGeneratedAt(new Date());
      setBudgetPreview(result.budget_preview);
      setGrowthFactor(result.growth_factor);
      setGrowthFactorNote(result.growth_factor_note);
      toast.success(`Budget Version ${newVersion} Generated Successfully`, { duration: 2000 });
      if (navigateToPreview) setSearchParams({ generated: 'true' });
    } catch (err: unknown) {
      const apiErr = err as { status?: number; message?: string };
      toast.error(apiErr?.message || 'Failed to generate budget. Please try again.');
    } finally {
      setIsGenerating(false);
    }
  };

  const handleGenerateBudget = () => runGenerateBudget(true);
  const handleRegenerateSnapshot = () => runGenerateBudget(false);

  const handleLineItemsUpdate = (updatedLineItems: LineItem[]) => {
    setLineItems(updatedLineItems);
  };

  const handleFileUploaded = (file: File) => {
    setUploadedFile(file);
  };

  // If generated budget view is active, show GeneratedBudgetScreen
  if (showGeneratedBudget && budgetVersion > 0) {
    return (
      <GeneratedBudgetScreen
        lineItems={lineItems}
        version={budgetVersion}
        generatedAt={budgetGeneratedAt}
        onRegenerateSnapshot={handleRegenerateSnapshot}
        budgetPreview={budgetPreview}
        growthFactor={growthFactor}
        growthFactorNote={growthFactorNote}
        isRegenerating={isGenerating}
      />
    );
  }

  // Otherwise show BudgetScreen
  return (
    <BudgetScreen
      lineItems={lineItems}
      onLineItemsUpdate={handleLineItemsUpdate}
      onGenerateBudget={handleGenerateBudget}
      onFileUploaded={handleFileUploaded}
      budgetGenerated={budgetVersion > 0}
      isGenerating={isGenerating}
      initialView={initialView as 'enriched' | 'budget' | 'ai'}
    />
  );
}
