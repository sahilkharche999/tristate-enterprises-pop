export interface LineItem {
  id: string;
  category: 'income' | 'operating' | 'reserve';
  name: string;
  ytdActual: number;
  annualBudget: number;
  percentChange: number;
  note?: {
    title: string;
    body: string;
  };
}

export interface HOA {
  id: string;
  name: string;
  fiscalYear: string;
  status: 'Not Started' | 'In Progress' | 'Completed';
  units: number;
  taxId: string;
  fiscalYearStart: string;
  fiscalYearEnd: string;
  year: number;
  city: string;
}

export const hoaList: HOA[] = [
  { id: '1', name: 'Esprit park', fiscalYear: 'April–March', status: 'Not Started', units: 120, taxId: '12-3456789', fiscalYearStart: 'April', fiscalYearEnd: 'March', year: 2025, city: 'San Francisco' },
  { id: '2', name: 'July Heights HOA', fiscalYear: 'July–June', status: 'In Progress', units: 85, taxId: '98-7654321', fiscalYearStart: 'July', fiscalYearEnd: 'June', year: 2025, city: 'Oakland' },
  { id: '3', name: 'October Ridge HOA', fiscalYear: 'Oct–Sept', status: 'Completed', units: 200, taxId: '45-6789012', fiscalYearStart: 'October', fiscalYearEnd: 'September', year: 2024, city: 'Berkeley' },
  { id: '4', name: '131 Missouri', fiscalYear: 'Jan–Dec', status: 'In Progress', units: 64, taxId: '78-9012345', fiscalYearStart: 'January', fiscalYearEnd: 'December', year: 2025, city: 'San Francisco' },
  { id: '5', name: '450 Sutter', fiscalYear: 'Jan–Dec', status: 'Not Started', units: 150, taxId: '23-4567890', fiscalYearStart: 'January', fiscalYearEnd: 'December', year: 2026, city: 'San Francisco' },
  { id: '6', name: '880 Market', fiscalYear: 'Jan–Dec', status: 'Completed', units: 180, taxId: '34-5678901', fiscalYearStart: 'January', fiscalYearEnd: 'December', year: 2024, city: 'San Francisco' },
  { id: '7', name: '22 Fremont', fiscalYear: 'Jan–Dec', status: 'In Progress', units: 95, taxId: '56-7890123', fiscalYearStart: 'January', fiscalYearEnd: 'December', year: 2025, city: 'San Jose' },
  { id: '8', name: '555 Mission', fiscalYear: 'Jan–Dec', status: 'Not Started', units: 110, taxId: '67-8901234', fiscalYearStart: 'January', fiscalYearEnd: 'December', year: 2026, city: 'San Francisco' },
  { id: '9', name: '401 HOA', fiscalYear: 'Jan–Dec 2025', status: 'In Progress', units: 48, taxId: '89-0123456', fiscalYearStart: 'January', fiscalYearEnd: 'December', year: 2025, city: 'Palo Alto' },
];

export const initialLineItems: LineItem[] = [
  // Income Section
  { id: 'inc-1', category: 'income', name: 'Regular Assessments', ytdActual: 285000, annualBudget: 360000, percentChange: 0 },
  { id: 'inc-2', category: 'income', name: 'Late Fees', ytdActual: 3200, annualBudget: 4800, percentChange: 0 },
  { id: 'inc-3', category: 'income', name: 'Interest Income', ytdActual: 1850, annualBudget: 2400, percentChange: 0 },
  { id: 'inc-4', category: 'income', name: 'Miscellaneous Income', ytdActual: 2100, annualBudget: 3000, percentChange: 0 },
  
  // Operating Expenses
  { id: 'op-1', category: 'operating', name: 'Insurance', ytdActual: 24500, annualBudget: 32000, percentChange: 0 },
  { id: 'op-2', category: 'operating', name: 'Landscaping', ytdActual: 18200, annualBudget: 24000, percentChange: 0 },
  { id: 'op-3', category: 'operating', name: 'Utilities – Electric', ytdActual: 15600, annualBudget: 21000, percentChange: 0 },
  { id: 'op-4', category: 'operating', name: 'Utilities – Water', ytdActual: 12800, annualBudget: 18000, percentChange: 0 },
  { id: 'op-5', category: 'operating', name: 'Utilities – Gas', ytdActual: 8400, annualBudget: 11500, percentChange: 0 },
  { id: 'op-6', category: 'operating', name: 'Management Fees', ytdActual: 21000, annualBudget: 28000, percentChange: 0 },
  { id: 'op-7', category: 'operating', name: 'Printing & Postage', ytdActual: 3200, annualBudget: 4500, percentChange: 0 },
  { id: 'op-8', category: 'operating', name: 'Repairs & Maintenance', ytdActual: 28500, annualBudget: 40000, percentChange: 0 },
  { id: 'op-9', category: 'operating', name: 'Janitorial', ytdActual: 16800, annualBudget: 22000, percentChange: 0 },
  { id: 'op-10', category: 'operating', name: 'Elevator Maintenance', ytdActual: 9200, annualBudget: 12500, percentChange: 0 },
  { id: 'op-11', category: 'operating', name: 'HVAC Service', ytdActual: 7800, annualBudget: 10500, percentChange: 0 },
  { id: 'op-12', category: 'operating', name: 'Legal & Accounting', ytdActual: 12500, annualBudget: 16000, percentChange: 0 },
  { id: 'op-13', category: 'operating', name: 'Taxes – Federal', ytdActual: 5600, annualBudget: 7500, percentChange: 0 },
  { id: 'op-14', category: 'operating', name: 'Taxes – State', ytdActual: 3200, annualBudget: 4200, percentChange: 0 },
  { id: 'op-15', category: 'operating', name: 'Licenses & Permits', ytdActual: 2400, annualBudget: 3200, percentChange: 0 },
  
  // Reserve Contributions
  { id: 'res-1', category: 'reserve', name: 'Roof Replacement Reserve', ytdActual: 18000, annualBudget: 24000, percentChange: 0 },
  { id: 'res-2', category: 'reserve', name: 'Elevator Reserve', ytdActual: 15000, annualBudget: 20000, percentChange: 0 },
  { id: 'res-3', category: 'reserve', name: 'Exterior Paint Reserve', ytdActual: 12000, annualBudget: 16000, percentChange: 0 },
  { id: 'res-4', category: 'reserve', name: 'Plumbing Reserve', ytdActual: 9000, annualBudget: 12000, percentChange: 0 },
  { id: 'res-5', category: 'reserve', name: 'Contingency Reserve', ytdActual: 7500, annualBudget: 10000, percentChange: 0 },
];

export interface AISuggestion {
  lineItemId: string;
  lineItemName: string;
  currentPercent: number;
  suggestedPercent: number;
  confidence: number;
  reason: string;
}

export const mockAISuggestions: AISuggestion[] = [
  { lineItemId: 'op-1', lineItemName: 'Insurance', currentPercent: 0, suggestedPercent: 8.5, confidence: 92, reason: 'Industry average insurance premium increase. Market analysis shows 7-10% increases across commercial property insurance.' },
  { lineItemId: 'op-3', lineItemName: 'Utilities – Electric', currentPercent: 0, suggestedPercent: 6.2, confidence: 88, reason: 'Local utility rate adjustment scheduled for Q1 2025. Rate increase confirmed by utility provider.' },
  { lineItemId: 'op-4', lineItemName: 'Utilities – Water', currentPercent: 0, suggestedPercent: 5.5, confidence: 85, reason: 'Municipal water rate increase approved. Historical average increase of 5-6% annually.' },
  { lineItemId: 'op-6', lineItemName: 'Management Fees', currentPercent: 0, suggestedPercent: 3.2, confidence: 95, reason: 'Contractual CPI adjustment clause. Based on confirmed CPI data for previous 12 months.' },
  { lineItemId: 'op-8', lineItemName: 'Repairs & Maintenance', currentPercent: 0, suggestedPercent: 12.5, confidence: 78, reason: 'Building age factor and deferred maintenance backlog. Historical trend shows increasing maintenance needs.' },
  { lineItemId: 'op-12', lineItemName: 'Legal & Accounting', currentPercent: 0, suggestedPercent: 7.0, confidence: 82, reason: 'Professional service fee escalation. Market rates for HOA legal and accounting services increasing.' },
  { lineItemId: 'res-1', lineItemName: 'Roof Replacement Reserve', currentPercent: 0, suggestedPercent: 15.0, confidence: 90, reason: 'Reserve study recommendation. Current funding level below recommended targets for anticipated replacement timeline.' },
  { lineItemId: 'op-2', lineItemName: 'Landscaping', currentPercent: 0, suggestedPercent: 4.8, confidence: 86, reason: 'Labor cost increases in landscaping industry. Drought conditions may increase water and replacement plant costs.' },
];

export interface KnowledgeBaseFolder {
  id: string;
  name: string;
  files: KnowledgeBaseFile[];
}

export interface KnowledgeBaseFile {
  id: string;
  name: string;
  year: string;
  status?: string;
}

// Knowledge Base is now HOA-specific
export const getKnowledgeBaseFolders = (hoaId: string): KnowledgeBaseFolder[] => {
  const hoa = hoaList.find((h) => h.id === hoaId);
  const hoaName = hoa?.name || 'HOA';
  
  return [
    {
      id: 'templates',
      name: 'HOA Templates',
      files: [
        { id: 'f1', name: 'Standard Budget Template.xlsx', year: '2025', status: 'Active' },
        { id: 'f2', name: 'Reserve Study Template.xlsx', year: '2025', status: 'Active' },
        { id: 'f3', name: 'Assessment Notice Template.docx', year: '2025', status: 'Active' },
      ],
    },
    {
      id: 'previous',
      name: 'Previous Years',
      files: [
        { id: 'f4', name: `${hoaName} - 2024 Final Budget.xlsx`, year: '2024', status: 'Archived' },
        { id: 'f5', name: `${hoaName} - 2023 Final Budget.xlsx`, year: '2023', status: 'Archived' },
        { id: 'f6', name: `${hoaName} - 2022 Final Budget.xlsx`, year: '2022', status: 'Archived' },
      ],
    },
    {
      id: 'approved',
      name: 'Approved Budgets',
      files: [
        { id: 'f7', name: `${hoaName} - 2025 Approved Budget.pdf`, year: '2025', status: 'Approved' },
        { id: 'f8', name: 'Board Resolution 2025-01.pdf', year: '2025', status: 'Approved' },
      ],
    },
    {
      id: 'rejected',
      name: 'Rejected Budgets',
      files: [
        { id: 'f9', name: `${hoaName} - 2025 Draft v1.xlsx`, year: '2025', status: 'Rejected' },
      ],
    },
    {
      id: 'board',
      name: 'Board Notes',
      files: [
        { id: 'f10', name: 'Budget Meeting Minutes Jan 2025.pdf', year: '2025', status: 'Final' },
        { id: 'f11', name: 'Budget Discussion Notes.docx', year: '2025', status: 'Draft' },
      ],
    },
    {
      id: 'audit',
      name: 'Audit Feedback',
      files: [
        { id: 'f12', name: '2024 Audit Report.pdf', year: '2024', status: 'Final' },
        { id: 'f13', name: 'Auditor Recommendations.docx', year: '2024', status: 'Final' },
      ],
    },
    {
      id: 'compliance',
      name: 'Compliance Documents',
      files: [
        { id: 'f14', name: 'HOA Governing Documents.pdf', year: '2025', status: 'Current' },
        { id: 'f15', name: 'State Compliance Checklist.pdf', year: '2025', status: 'Current' },
      ],
    },
    {
      id: 'reserve',
      name: 'Reserve Studies',
      files: [
        { id: 'f16', name: `${hoaName} Reserve Study 2024.pdf`, year: '2024', status: 'Current' },
        { id: 'f17', name: 'Capital Expenditure Plan.xlsx', year: '2025', status: 'Draft' },
      ],
    },
  ];
};

export const knowledgeBaseFolders: KnowledgeBaseFolder[] = [
  {
    id: 'templates',
    name: 'HOA Templates',
    files: [
      { id: 'f1', name: 'Standard Budget Template.xlsx', year: '2025', status: 'Active' },
      { id: 'f2', name: 'Reserve Study Template.xlsx', year: '2025', status: 'Active' },
      { id: 'f3', name: 'Assessment Notice Template.docx', year: '2025', status: 'Active' },
    ],
  },
  {
    id: 'previous',
    name: 'Previous Years',
    files: [
      { id: 'f4', name: '401 HOA - 2024 Final Budget.xlsx', year: '2024', status: 'Archived' },
      { id: 'f5', name: '401 HOA - 2023 Final Budget.xlsx', year: '2023', status: 'Archived' },
      { id: 'f6', name: '401 HOA - 2022 Final Budget.xlsx', year: '2022', status: 'Archived' },
    ],
  },
  {
    id: 'approved',
    name: 'Approved Budgets',
    files: [
      { id: 'f7', name: '401 HOA - 2025 Approved Budget.pdf', year: '2025', status: 'Approved' },
      { id: 'f8', name: 'Board Resolution 2025-01.pdf', year: '2025', status: 'Approved' },
    ],
  },
  {
    id: 'rejected',
    name: 'Rejected Budgets',
    files: [
      { id: 'f9', name: '401 HOA - 2025 Draft v1.xlsx', year: '2025', status: 'Rejected' },
    ],
  },
  {
    id: 'board',
    name: 'Board Notes',
    files: [
      { id: 'f10', name: 'Budget Meeting Minutes Jan 2025.pdf', year: '2025', status: 'Final' },
      { id: 'f11', name: 'Budget Discussion Notes.docx', year: '2025', status: 'Draft' },
    ],
  },
  {
    id: 'audit',
    name: 'Audit Feedback',
    files: [
      { id: 'f12', name: '2024 Audit Report.pdf', year: '2024', status: 'Final' },
      { id: 'f13', name: 'Auditor Recommendations.docx', year: '2024', status: 'Final' },
    ],
  },
  {
    id: 'compliance',
    name: 'Compliance Documents',
    files: [
      { id: 'f14', name: 'HOA Governing Documents.pdf', year: '2025', status: 'Current' },
      { id: 'f15', name: 'State Compliance Checklist.pdf', year: '2025', status: 'Current' },
    ],
  },
  {
    id: 'reserve',
    name: 'Reserve Studies',
    files: [
      { id: 'f16', name: '401 HOA Reserve Study 2024.pdf', year: '2024', status: 'Current' },
      { id: 'f17', name: 'Capital Expenditure Plan.xlsx', year: '2025', status: 'Draft' },
    ],
  },
];