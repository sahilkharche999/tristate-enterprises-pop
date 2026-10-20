export function getErrorMessage(error: unknown, fallback: string): string {
  // A blank message (e.g. a 502/504 over HTTP/2 has no statusText) would be a blank toast.
  if (
    error &&
    typeof error === 'object' &&
    'message' in error &&
    typeof error.message === 'string' &&
    error.message.trim()
  ) {
    return error.message;
  }
  return fallback;
}
