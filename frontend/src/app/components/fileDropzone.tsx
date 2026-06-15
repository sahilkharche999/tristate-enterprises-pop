import { useId, useMemo, useRef, useState } from 'react';
import { Check, FileText, Upload, X } from 'lucide-react';

import { Button } from './ui/button';
import {
  fileDropzoneClassName,
  fileDropzoneHelperCopy,
  type FileDropzoneStatus,
} from './fileDropzoneStyles';

type FileDropzoneProps = {
  title: string;
  helper: string;
  accept: string;
  fileName?: string | null;
  multiple?: boolean;
  disabled?: boolean;
  required?: boolean;
  status?: FileDropzoneStatus;
  statusMessage?: string;
  actionLabel?: string;
  onFilesSelected(files: FileList | null): void;
  onClear?(): void;
};

export function FileDropzone({
  title,
  helper,
  accept,
  fileName = null,
  multiple = false,
  disabled = false,
  required = false,
  status = 'idle',
  statusMessage,
  actionLabel = 'Select file',
  onFilesSelected,
  onClear,
}: FileDropzoneProps) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragActive, setDragActive] = useState(false);

  const className = useMemo(
    () =>
      fileDropzoneClassName({
        disabled,
        dragActive,
        fileName,
        status,
      }),
    [disabled, dragActive, fileName, status],
  );

  const helperText = fileDropzoneHelperCopy({ dragActive, fileName });

  const openPicker = () => {
    if (disabled) return;
    inputRef.current?.click();
  };

  const handleFiles = (files: FileList | null) => {
    if (disabled) return;
    onFilesSelected(files);
    if (inputRef.current) {
      inputRef.current.value = '';
    }
  };

  return (
    <div
      className="space-y-2"
      onDragEnter={(event) => {
        event.preventDefault();
        if (!disabled) setDragActive(true);
      }}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setDragActive(true);
      }}
      onDragLeave={(event) => {
        event.preventDefault();
        setDragActive(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragActive(false);
        handleFiles(event.dataTransfer.files);
      }}
    >
      <label htmlFor={inputId} className="sr-only">
        {title}
      </label>
      <input
        id={inputId}
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        required={required}
        disabled={disabled}
        className="sr-only"
        onChange={(event) => handleFiles(event.target.files)}
        aria-label={title}
      />
      <button
        type="button"
        className={className}
        onClick={openPicker}
        disabled={disabled}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 space-y-2">
            <div className="flex items-center gap-2">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#e5e5e5] bg-white text-[#111111]">
                <Upload className="h-4 w-4" />
              </span>
              <div className="min-w-0">
                <p className="text-sm font-semibold text-inherit">{title}</p>
                <p className="text-xs text-[#737373]">{helper}</p>
              </div>
            </div>
            <p className="text-sm font-medium text-[#111111]">
              {fileName ? (
                <span className="inline-flex items-center gap-2">
                  <FileText className="h-4 w-4 shrink-0" />
                  <span className="truncate">{fileName}</span>
                </span>
              ) : (
                helperText
              )}
            </p>
            {statusMessage ? (
              <p className="text-xs font-medium text-[#525252]">{statusMessage}</p>
            ) : null}
          </div>
          <span className="rounded-lg border border-[#d4d4d4] bg-white px-3 py-2 text-xs font-medium text-[#111111] shadow-sm">
            {fileName ? 'Change file' : actionLabel}
          </span>
        </div>
      </button>
      <div className="flex items-center justify-end">
        {fileName && onClear ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled}
            onClick={onClear}
            className="h-8 px-2 text-xs text-[#737373] hover:bg-[#f5f5f5] hover:text-[#111111]"
          >
            <X className="h-3.5 w-3.5" />
            Clear
          </Button>
        ) : null}
      </div>
      {status === 'selected' && fileName ? (
        <div className="flex items-center gap-2 text-xs text-[#525252]">
          <Check className="h-3.5 w-3.5 text-emerald-600" />
          Ready to upload
        </div>
      ) : null}
    </div>
  );
}
