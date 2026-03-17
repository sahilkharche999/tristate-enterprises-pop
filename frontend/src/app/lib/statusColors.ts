export function getStatusColor(status: string): string {
  switch (status) {
    case 'Completed':
      return 'bg-[#d1fae5] text-[#065f46] border-[#a7f3d0]';
    case 'In Progress':
      return 'bg-[#dbeafe] text-[#1e40af] border-[#bfdbfe]';
    case 'Not Started':
      return 'bg-[#f5f5f5] text-[#525252] border-[#e5e5e5]';
    default:
      return 'bg-[#f5f5f5] text-[#525252] border-[#e5e5e5]';
  }
}
