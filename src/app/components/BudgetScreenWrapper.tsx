import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router';
import { BudgetScreen } from './BudgetScreen';
import { GeneratedBudgetScreen } from './GeneratedBudgetScreen';
import { initialLineItems, type LineItem } from '../data/mockData';
import { toast } from 'sonner';

export function BudgetScreenWrapper() {
  const [searchParams, setSearchParams] = useSearchParams();
  const showGeneratedBudget = searchParams.get('generated') === 'true';
  const initialView = searchParams.get('view') || 'enriched';

  const [lineItems, setLineItems] = useState<LineItem[]>(initialLineItems);
  const [budgetVersion, setBudgetVersion] = useState(0);
  const [budgetGeneratedAt, setBudgetGeneratedAt] = useState<Date>(new Date());
  const [snapshotLineItems, setSnapshotLineItems] = useState<LineItem[]>([]);

  const handleGenerateBudget = () => {
    const newVersion = budgetVersion + 1;
    setBudgetVersion(newVersion);
    setBudgetGeneratedAt(new Date());
    setSnapshotLineItems([...lineItems]); // Store snapshot
    toast.success(`Budget Version ${newVersion} Generated Successfully`, {
      duration: 2000,
    });
    // Navigate to generated budget screen
    setSearchParams({ generated: 'true' });
  };

  const handleRegenerateSnapshot = () => {
    const newVersion = budgetVersion + 1;
    setBudgetVersion(newVersion);
    setBudgetGeneratedAt(new Date());
    setSnapshotLineItems([...lineItems]); // Store new snapshot
    toast.success(`Budget Version ${newVersion} Generated Successfully`, {
      duration: 2000,
    });
  };

  const handleLineItemsUpdate = (updatedLineItems: LineItem[]) => {
    setLineItems(updatedLineItems);
  };

  // If generated budget view is active, show GeneratedBudgetScreen
  if (showGeneratedBudget && budgetVersion > 0) {
    return (
      <GeneratedBudgetScreen
        lineItems={lineItems} // Use current lineItems for live updates
        version={budgetVersion}
        generatedAt={budgetGeneratedAt}
        onRegenerateSnapshot={handleRegenerateSnapshot}
      />
    );
  }

  // Otherwise show BudgetScreen
  return (
    <BudgetScreen
      lineItems={lineItems}
      onLineItemsUpdate={handleLineItemsUpdate}
      onGenerateBudget={handleGenerateBudget}
      budgetGenerated={budgetVersion > 0}
      initialView={initialView as 'enriched' | 'budget' | 'ai'}
    />
  );
}