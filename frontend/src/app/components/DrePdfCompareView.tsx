import { useEffect, useState } from 'react';
import { X } from 'lucide-react';

import { authHeaders } from '../api/http';
import { Button } from './ui/button';
import {
  comparePdfPaneMessage,
  type ComparePdfPaneStatus,
} from './drePdfCompareViewStyles.ts';
import { pdfPageAnchorUrl } from './pdfPageAnchor.ts';

interface DrePdfCompareViewProps {
  fileUrl: string;
  targetPage?: number;
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
  onClose,
  children,
}: DrePdfCompareViewProps) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<ComparePdfPaneStatus>('loading');

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    setStatus('loading');
    void fetch(fileUrl, { headers: authHeaders() })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('Failed to load source PDF'))))
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

  const message = comparePdfPaneMessage(status);

  return (
    <div className="fixed inset-0 z-50 flex bg-black/40" role="dialog" aria-modal="true">
      <div className="flex min-h-0 flex-1 flex-col overflow-auto bg-white">{children}</div>
      <div className="flex min-h-0 flex-1 flex-col border-l border-[#e5e5e5] bg-[#fafafa]">
        <div className="flex items-center justify-end border-b border-[#e5e5e5] bg-white px-3 py-2">
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close compare view">
            <X className="h-5 w-5 text-[#525252]" />
          </Button>
        </div>
        <div className="flex min-h-0 flex-1 items-center justify-center">
          {message ? (
            <p className="text-sm text-[#737373]">{message}</p>
          ) : (
            <iframe
              // Remounting on every targetPage change (not just updating src)
              // is required — see pdfPageAnchor.ts / design.md Decision 1a.
              key={targetPage ?? 0}
              title="Source PDF"
              src={pdfPageAnchorUrl(objectUrl ?? '', targetPage)}
              className="h-full w-full border-0"
            />
          )}
        </div>
      </div>
    </div>
  );
}
