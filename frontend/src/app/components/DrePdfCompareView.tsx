import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';

import { authHeaders } from '../api/http';
import { Button } from './ui/button';
import {
  comparePdfPaneMessage,
  type ComparePdfPaneStatus,
} from './drePdfCompareViewStyles.ts';
import { clampSplitPercent } from './resizableSplitPane.ts';
import { pdfPageAnchorUrl } from './pdfPageAnchor.ts';

interface DrePdfCompareViewProps {
  fileUrl: string;
  targetPage?: number;
  /** Label used in the header/iframe-title/status text ("Compare with source
   * {sourceLabel}", etc). Defaults to 'PDF' so existing PDF-only call sites
   * (Reserve Study, DRE) are unaffected; the income-statement Excel case
   * passes 'workbook' since the rendered content isn't a PDF. */
  sourceLabel?: string;
  onClose: () => void;
  children: React.ReactNode;
}

/** Full-screen split view: review panel on one half, original source PDF on
 * the other (add-dre-review-pdf-compare-view, generalized for reuse by
 * add-reserve-study-pdf-compare-view). Fetches the PDF as an authenticated
 * blob (same recipe as HOALogoUploadControl.tsx) since the file endpoint
 * requires auth and a bare <iframe src> can't attach headers. Callers supply
 * the fully-built, document-type-specific file URL (e.g. dreDocumentFileUrl
 * or reserveStudyFileUrl) rather than this component constructing one itself. */
export function DrePdfCompareView({
  fileUrl,
  targetPage,
  sourceLabel = 'PDF',
  onClose,
  children,
}: DrePdfCompareViewProps) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<ComparePdfPaneStatus>('loading');
  const [leftWidthPercent, setLeftWidthPercent] = useState(50);
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    setStatus('loading');
    void fetch(fileUrl, { headers: authHeaders() })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('Failed to load source file'))))
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setObjectUrl(url);
        setStatus('ready');
      })
      .catch(() => {
        if (!cancelled) setStatus('error');
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [fileUrl]);

  const handleDividerPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    setIsDragging(true);
  };

  const handleDividerPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!isDragging || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const percent = ((event.clientX - rect.left) / rect.width) * 100;
    setLeftWidthPercent(clampSplitPercent(percent));
  };

  const stopDragging = (event: React.PointerEvent<HTMLDivElement>) => {
    event.currentTarget.releasePointerCapture(event.pointerId);
    setIsDragging(false);
  };

  const message = comparePdfPaneMessage(status, sourceLabel);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black/40" role="dialog" aria-modal="true">
      <div className="flex items-center justify-between border-b border-[#e5e5e5] bg-white px-4 py-2.5">
        <p className="text-sm font-medium text-[#111111]">Compare with source {sourceLabel}</p>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close compare view">
          <X className="h-5 w-5 text-[#525252]" />
        </Button>
      </div>
      <div ref={containerRef} className="flex min-h-0 flex-1">
        <div
          className="flex min-h-0 shrink-0 flex-col overflow-auto bg-white"
          style={{ width: `${leftWidthPercent}%` }}
        >
          {children}
        </div>
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize compare panels"
          tabIndex={0}
          onPointerDown={handleDividerPointerDown}
          onPointerMove={handleDividerPointerMove}
          onPointerUp={stopDragging}
          onPointerCancel={stopDragging}
          className={`group relative w-2 shrink-0 cursor-col-resize bg-[#e5e5e5] transition-colors hover:bg-[#a3a3a3] ${
            isDragging ? 'bg-[#737373]' : ''
          }`}
        >
          <div className="absolute inset-y-0 left-1/2 w-4 -translate-x-1/2" />
          <div
            className={`pointer-events-none absolute left-1/2 top-1/2 h-10 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white/70 transition-opacity ${
              isDragging ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
            }`}
          />
        </div>
        <div className="flex min-h-0 flex-1 flex-col bg-[#fafafa]">
          <div className="flex min-h-0 flex-1 items-center justify-center">
            {message ? (
              <p className="text-sm text-[#737373]">{message}</p>
            ) : (
              <iframe
                // Remounting on every targetPage change (not just updating src)
                // is required — see pdfPageAnchor.ts / design.md Decision 1a.
                key={targetPage ?? 0}
                title={`Source ${sourceLabel}`}
                src={pdfPageAnchorUrl(objectUrl ?? '', targetPage)}
                className="h-full w-full border-0"
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
