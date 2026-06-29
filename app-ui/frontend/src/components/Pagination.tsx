interface PaginationProps {
  page: number;
  pageSize: number;
  totalItems: number;
  onPageChange: (page: number) => void;
}

export function Pagination({ page, pageSize, totalItems, onPageChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const safePage = Math.min(page, totalPages);
  const start = totalItems === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const end = Math.min(safePage * pageSize, totalItems);

  return (
    <div className="pagination-row">
      <span className="pagination-info">
        {totalItems === 0 ? "No records" : `${start}–${end} of ${totalItems} records`}
      </span>
      <div className="pagination-controls">
        <button className="page-btn" onClick={() => onPageChange(1)} disabled={safePage === 1} title="First">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="m11 17-5-5 5-5M18 17l-5-5 5-5"/></svg>
        </button>
        <button className="page-btn" onClick={() => onPageChange(safePage - 1)} disabled={safePage === 1} title="Prev">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="m15 18-6-6 6-6"/></svg>
        </button>
        <span className="page-pill">{safePage} / {totalPages}</span>
        <button className="page-btn" onClick={() => onPageChange(safePage + 1)} disabled={safePage === totalPages} title="Next">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="m9 18 6-6-6-6"/></svg>
        </button>
        <button className="page-btn" onClick={() => onPageChange(totalPages)} disabled={safePage === totalPages} title="Last">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="m6 17 5-5-5-5M13 17l5-5-5-5"/></svg>
        </button>
      </div>
    </div>
  );
}
