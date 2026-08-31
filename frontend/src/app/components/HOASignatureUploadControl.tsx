import { useEffect, useState } from 'react';
import {
  deleteHOASignature,
  hoaSignatureUrl,
  uploadHOASignature,
} from '../api/hoaSettings';
import { authHeaders } from '../api/http';
import { getErrorMessage } from '../lib/errors';
import { FileDropzone } from './fileDropzone';
import { Button } from './ui/button';

export function HOASignatureUploadControl({
  hoaId,
  hasSignature,
  onChanged,
}: {
  hoaId: number;
  hasSignature: boolean;
  onChanged: (hasSignature: boolean) => void;
}) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    if (hasSignature) {
      void fetch(hoaSignatureUrl(hoaId), { headers: authHeaders() })
        .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('Failed to load signature'))))
        .then((blob) => {
          if (cancelled) return;
          objectUrl = URL.createObjectURL(blob);
          setPreviewUrl(objectUrl);
        })
        .catch(() => setPreviewUrl(null));
    } else {
      setPreviewUrl(null);
    }
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [hoaId, hasSignature]);

  return (
    <div className="space-y-2 border border-[#e5e5e5] rounded p-3">
      <div>
        <h4 className="text-sm font-semibold text-[#111]">Cover letter signature</h4>
        <p className="text-xs text-[#737373]">
          Optional override of the firm signature image for this HOA. PNG or JPEG.
        </p>
      </div>
      <div className="flex items-start gap-3">
        {previewUrl ? (
          <img
            src={previewUrl}
            alt="HOA signature preview"
            className="h-12 max-w-[8rem] object-contain border border-[#e5e5e5] rounded bg-white"
          />
        ) : (
          <div className="h-12 w-20 shrink-0 flex items-center justify-center border border-dashed border-[#d4d4d4] rounded text-[10px] text-[#a3a3a3]">
            Firm default
          </div>
        )}
        <div className="min-w-0 flex-1 space-y-2">
          <FileDropzone
            title="Signature image"
            helper="PNG or JPEG."
            accept=".png,.jpg,.jpeg"
            fileName={hasSignature ? 'Current signature' : null}
            disabled={busy}
            status={error ? 'error' : hasSignature ? 'selected' : 'idle'}
            statusMessage={busy ? 'Uploading…' : error ?? undefined}
            actionLabel="Choose signature"
            onFilesSelected={(files) => {
              const file = files?.[0];
              if (!file) return;
              setBusy(true);
              setError(null);
              void uploadHOASignature(hoaId, file)
                .then((updated) => onChanged(Boolean(updated.has_signature)))
                .catch((e) => setError(e instanceof Error ? e.message : getErrorMessage(e, 'Upload failed')))
                .finally(() => setBusy(false));
            }}
          />
          {hasSignature ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                setError(null);
                void deleteHOASignature(hoaId)
                  .then((updated) => onChanged(Boolean(updated.has_signature)))
                  .catch((e) => setError(e instanceof Error ? e.message : getErrorMessage(e, 'Remove failed')))
                  .finally(() => setBusy(false));
              }}
              className="h-8 px-2 text-xs text-[#b91c1c] hover:bg-[#fef2f2]"
            >
              Use firm signature
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
