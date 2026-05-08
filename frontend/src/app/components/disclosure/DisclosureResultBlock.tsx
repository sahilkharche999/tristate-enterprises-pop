// Result block (UI-SPEC §6.1, §7.4, §9.5).
//
// Renders only when the panel is in the `completed` state. The Download PDF
// button is the second of two primary CTAs in the entire disclosure flow
// (UI-SPEC §4 — accent reserved for "Generate Disclosure Package" and
// "Download PDF" only).
//
// Phase 11 metadata: only `Generated` (timestamp) is sourced from the job
// record returned by the status endpoint. Page count, file size, and SHA-256
// checksum are deferred to Phase 12 / a metadata extension; the corresponding
// UI rows are intentionally omitted rather than rendered with placeholder
// values per project no-stub discipline.

import { useState } from 'react';
import { Download } from 'lucide-react';

import type { DisclosurePackageJob } from '../../api/disclosurePackage';
import { downloadDisclosurePackagePdf } from '../../api/disclosurePackage';
import { Button } from '../ui/button';

export interface DisclosureResultBlockProps {
  job: DisclosurePackageJob;
  onViewAudit?: () => void;
  onRegenerate?: () => void;
}

function formatTimestamp(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function DisclosureResultBlock({
  job,
  onViewAudit,
  onRegenerate,
}: DisclosureResultBlockProps) {
  const generated = formatTimestamp(job.completed_at);
  const filename = `old-mill-${job.fiscal_year}-disclosure-package.pdf`;
  const [downloading, setDownloading] = useState(false);

  async function handleDownload() {
    if (downloading) return;
    setDownloading(true);
    try {
      const blob = await downloadDisclosurePackagePdf(job.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="space-y-4 transition-opacity duration-200">
      <div className="space-y-1">
        <p className="text-xs font-medium uppercase tracking-[0.2em] text-[#737373]">
          Result
        </p>
        <h3 className="text-lg font-semibold text-[#111111]">
          Disclosure package ready
        </h3>
      </div>
      {generated ? (
        <dl className="grid grid-cols-1 gap-y-1 text-sm sm:grid-cols-2 sm:gap-x-4">
          <dt className="text-[#737373]">Generated</dt>
          <dd className="text-[#111111]">{generated}</dd>
        </dl>
      ) : null}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {onViewAudit ? (
          <Button variant="ghost" onClick={onViewAudit}>
            View audit log
          </Button>
        ) : null}
        {onRegenerate ? (
          <Button
            variant="outline"
            onClick={onRegenerate}
            className="border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
          >
            Regenerate
          </Button>
        ) : null}
        <Button
          onClick={handleDownload}
          disabled={downloading}
          className="bg-[#111111] text-white shadow-sm hover:bg-[#262626]"
        >
          <Download className="mr-2 h-4 w-4" />
          {downloading ? 'Downloading…' : 'Download PDF'}
        </Button>
      </div>
    </div>
  );
}
