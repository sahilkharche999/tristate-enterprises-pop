import type { CCRExtractedDetailView } from '../lib/ccrReviewWorkflow';

type Props = {
  detail: CCRExtractedDetailView;
  jumpToPage: (page: number) => void;
};

function PageLinks({
  pages,
  jumpToPage,
}: {
  pages: number[];
  jumpToPage: (page: number) => void;
}) {
  if (pages.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {pages.map((page) => (
        <button
          key={page}
          type="button"
          onClick={() => jumpToPage(page)}
          className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          View PDF page {page}
        </button>
      ))}
    </div>
  );
}

function Purpose({ children }: { children: string }) {
  return <p className="mt-1 text-sm text-slate-600">{children}</p>;
}

export function CCRExtractedDetail({ detail, jumpToPage }: Props) {
  return (
    <section className="space-y-4" aria-labelledby="ccr-extracted-heading">
      <div>
        <h2 id="ccr-extracted-heading" className="text-lg font-semibold text-slate-900">
          What this document already says
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          These are the charging rules already found in the governing document.
          Dollar totals usually come from the yearly budget, not from this file.
        </p>
      </div>

      <section className="rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="font-semibold text-slate-900">What this HOA is</h3>
        <Purpose>{detail.hoa.purpose}</Purpose>
        <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
          {detail.hoa.associationName ? (
            <div>
              <dt className="font-medium text-slate-800">Association</dt>
              <dd className="mt-0.5 text-slate-700">{detail.hoa.associationName}</dd>
            </div>
          ) : null}
          {detail.hoa.documentTitle ? (
            <div>
              <dt className="font-medium text-slate-800">Document</dt>
              <dd className="mt-0.5 text-slate-700">{detail.hoa.documentTitle}</dd>
            </div>
          ) : null}
          {detail.hoa.documentDate ? (
            <div>
              <dt className="font-medium text-slate-800">Dated</dt>
              <dd className="mt-0.5 text-slate-700">{detail.hoa.documentDate}</dd>
            </div>
          ) : null}
          {detail.hoa.unitCount ? (
            <div>
              <dt className="font-medium text-slate-800">Homes</dt>
              <dd className="mt-0.5 text-slate-700">{detail.hoa.unitCount}</dd>
            </div>
          ) : null}
        </dl>
        <PageLinks pages={detail.hoa.sourcePages} jumpToPage={jumpToPage} />
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="font-semibold text-slate-900">How charges are divided</h3>
        <Purpose>{detail.division.purpose}</Purpose>
        {detail.division.summary ? (
          <p className="mt-3 text-sm text-slate-800">{detail.division.summary}</p>
        ) : (
          <p className="mt-3 text-sm text-slate-600">
            The document’s allocation summary will appear here after extraction.
          </p>
        )}
        {detail.division.needsExternalBudget ? (
          <p className="mt-2 text-sm font-medium text-slate-800">
            Needs the yearly budget / DRE schedule for dollar amounts.
          </p>
        ) : null}
      </section>

      {detail.categories.length > 0 ? (
        <section className="space-y-3">
          <div>
            <h3 className="font-semibold text-slate-900">Charge categories</h3>
            <Purpose>These are the charge rules found in the document.</Purpose>
          </div>
          <div className="grid gap-3">
            {detail.categories.map((category, index) => (
              <article
                key={`${category.name}-${index}`}
                className="rounded-xl border border-slate-200 bg-white p-4"
              >
                <h4 className="font-semibold text-slate-950">{category.name}</h4>
                <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
                  <div>
                    <dt className="font-medium text-slate-800">What it covers</dt>
                    <dd className="mt-0.5 text-slate-700">{category.covers}</dd>
                  </div>
                  <div>
                    <dt className="font-medium text-slate-800">Who pays</dt>
                    <dd className="mt-0.5 text-slate-700">{category.whoPays}</dd>
                  </div>
                  <div>
                    <dt className="font-medium text-slate-800">How it is divided</dt>
                    <dd className="mt-0.5 text-slate-700">{category.howDivided}</dd>
                  </div>
                  <div>
                    <dt className="font-medium text-slate-800">How it is billed</dt>
                    <dd className="mt-0.5 text-slate-700">
                      {category.billedWith} · {category.cadence}
                    </dd>
                  </div>
                  <div>
                    <dt className="font-medium text-slate-800">Amount</dt>
                    <dd className="mt-0.5 text-slate-700">{category.amount}</dd>
                  </div>
                  <div>
                    <dt className="font-medium text-slate-800">Where the dollars come from</dt>
                    <dd className="mt-0.5 text-slate-700">{category.amountSource}</dd>
                  </div>
                </dl>
                <PageLinks pages={category.sourcePages} jumpToPage={jumpToPage} />
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {detail.homes.length > 0 ? (
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="font-semibold text-slate-900">Homes in this document</h3>
          <Purpose>These homes are who the rules apply to.</Purpose>
          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="py-2 pr-4 font-medium">Home</th>
                  <th className="py-2 pr-4 font-medium">Square feet</th>
                  <th className="py-2 font-medium">Ownership share</th>
                </tr>
              </thead>
              <tbody>
                {detail.homes.map((home) => (
                  <tr key={home.unitNumber} className="border-b border-slate-100 last:border-b-0">
                    <td className="py-2 pr-4 text-slate-800">{home.unitNumber}</td>
                    <td className="py-2 pr-4 text-slate-700">{home.squareFeet || '—'}</td>
                    <td className="py-2 text-slate-700">{home.ownershipPercent || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {detail.pages.length > 0 ? (
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="font-semibold text-slate-900">Pages we used</h3>
          <Purpose>These are the pages the extraction actually read.</Purpose>
          <ul className="mt-3 divide-y divide-slate-100 text-sm">
            {detail.pages.map((page) => (
              <li key={page.pageNumber} className="flex flex-wrap items-start justify-between gap-3 py-2">
                <div>
                  <p className="font-medium text-slate-800">
                    Page {page.pageNumber} · {page.pageType}
                  </p>
                  {page.notes ? <p className="mt-0.5 text-slate-600">{page.notes}</p> : null}
                </div>
                <button
                  type="button"
                  onClick={() => jumpToPage(page.pageNumber)}
                  className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                >
                  View PDF page {page.pageNumber}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {detail.questions.length > 0 ? (
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="font-semibold text-slate-900">Questions from the document</h3>
          <Purpose>
            These are follow-ups the document raises, such as a DRE schedule or parking assignments.
          </Purpose>
          <ul className="mt-3 space-y-3 text-sm">
            {detail.questions.map((question, index) => (
              <li key={`${question.question}-${index}`}>
                <p className="font-medium text-slate-900">{question.question}</p>
                {question.reason ? (
                  <p className="mt-0.5 text-slate-600">{question.reason}</p>
                ) : null}
                <PageLinks pages={question.sourcePages} jumpToPage={jumpToPage} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </section>
  );
}
