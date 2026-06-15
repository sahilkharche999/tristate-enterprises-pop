import { cn } from './ui/utils.ts';

export type FileDropzoneStatus = 'idle' | 'selected' | 'attention' | 'error';

export function fileDropzoneClassName(options: {
  disabled: boolean;
  dragActive: boolean;
  fileName: string | null;
  status: FileDropzoneStatus;
}) {
  const base =
    'group relative flex w-full cursor-pointer flex-col gap-4 rounded-2xl border-2 border-dashed p-5 text-left shadow-sm transition-[border-color,background-color,box-shadow,transform] duration-200 focus-within:ring-4 focus-within:ring-[#111111]/10';
  if (options.disabled) {
    return cn(base, 'cursor-not-allowed border-[#e5e5e5] bg-[#fafafa] text-[#a3a3a3] shadow-none');
  }
  if (options.status === 'error') {
    return cn(base, 'border-[#fca5a5] bg-[#fff7f7] text-[#7f1d1d] hover:border-[#f87171] hover:bg-white');
  }
  if (options.status === 'attention') {
    return cn(base, 'border-[#f59e0b] bg-[#fff7ed] text-[#111111] hover:border-[#d97706] hover:bg-white');
  }
  if (options.status === 'selected' || options.fileName) {
    return cn(base, 'border-[#111111] bg-white text-[#111111] hover:-translate-y-0.5 hover:shadow-md');
  }
  if (options.dragActive) {
    return cn(base, 'border-[#111111] bg-white text-[#111111] ring-4 ring-[#111111]/10');
  }
  return cn(
    base,
    'border-[#d4d4d4] bg-[#fafafa] text-[#111111] hover:border-[#a3a3a3] hover:bg-white hover:shadow-md',
  );
}

export function fileDropzoneHelperCopy(options: {
  dragActive: boolean;
  fileName: string | null;
}) {
  if (options.dragActive) return 'Drop file here';
  if (options.fileName) return 'Change file or clear it';
  return 'Click or drop file here';
}
