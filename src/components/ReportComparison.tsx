import { useState } from 'react';
import { VscCheck, VscClose } from 'react-icons/vsc';
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued';
import { computeSetDiff } from '../api/rationalizationClient';
import type { ReportDetail, PairSimilarity } from '../api/rationalizationClient';

interface Props {
  reportA: ReportDetail;
  reportB: ReportDetail;
  similarity: PairSimilarity | null;
  onClose: () => void;
}

export default function ReportComparison({ reportA, reportB, similarity, onClose }: Props) {
  const metricsDiff = computeSetDiff(reportA.metrics, reportB.metrics);
  const tablesDiff = computeSetDiff(reportA.tables, reportB.tables);
  const filtersDiff = computeSetDiff(reportA.filters, reportB.filters);

  const overallScore = similarity?.combined
    ? (similarity.combined * 100).toFixed(0)
    : Math.round(
        (0.4 * metricsDiff.similarity + 0.4 * tablesDiff.similarity + 0.2 * filtersDiff.similarity) *
          100
      );

  const metricScore = (metricsDiff.similarity * 100).toFixed(0);
  const tableScore = (tablesDiff.similarity * 100).toFixed(0);
  const filterScore = (filtersDiff.similarity * 100).toFixed(0);

  const reasons: string[] = [];
  if (metricsDiff.similarity === 1 && reportA.metrics.length > 0)
    reasons.push(`100% metric overlap (${metricsDiff.shared.length} shared)`);
  else if (metricsDiff.similarity >= 0.75 && reportA.metrics.length > 0)
    reasons.push(`${metricScore}% metric overlap (${metricsDiff.shared.length}/${metricsDiff.shared.length + metricsDiff.onlyA.length + metricsDiff.onlyB.length})`);

  if (tablesDiff.similarity === 1 && reportA.tables.length > 0)
    reasons.push(`100% table overlap (${tablesDiff.shared.length} shared)`);
  else if (tablesDiff.similarity >= 0.75 && reportA.tables.length > 0)
    reasons.push(`${tableScore}% table overlap (${tablesDiff.shared.length} shared)`);

  if (filtersDiff.similarity === 1 && reportA.filters.length > 0)
    reasons.push(`100% filter attribute overlap`);
  else if (filtersDiff.similarity >= 0.75 && reportA.filters.length > 0)
    reasons.push(`${filterScore}% filter attribute overlap`);

  if (reportA.familyBase === reportB.familyBase)
    reasons.push(`Same base name: "${reportA.familyBase}"`);

  const astScore = similarity?.ast !== undefined
    ? Math.round(similarity.ast * 100)
    : null;
  if (astScore !== null && astScore >= 90)
    reasons.push(`${astScore}% AST structural match — same query shape`);

  return (
    <div
      className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-[#16213e] rounded-lg max-w-[1400px] w-full max-h-[92vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="p-5 border-b border-[#1e2d50] flex justify-between items-center">
          <div className="flex items-center gap-4">
            <h2 className="text-lg font-bold text-white">Report Comparison</h2>
            <div className="flex items-center gap-2 bg-[#0f0f1a] px-4 py-2 rounded-lg">
              <span className="text-xs text-gray-400">Combined:</span>
              <span
                className={`text-xl font-bold ${
                  parseInt(String(overallScore)) >= 90
                    ? 'text-green-400'
                    : parseInt(String(overallScore)) >= 75
                    ? 'text-yellow-400'
                    : 'text-orange-400'
                }`}
              >
                {overallScore}%
              </span>
            </div>
            {astScore !== null && (
              <div className="flex items-center gap-2 bg-[#0f0f1a] px-4 py-2 rounded-lg border border-purple-900/50">
                <span className="text-xs text-purple-300">AST:</span>
                <span
                  className={`text-xl font-bold ${
                    astScore >= 90
                      ? 'text-green-400'
                      : astScore >= 70
                      ? 'text-yellow-400'
                      : 'text-orange-400'
                  }`}
                >
                  {astScore}%
                </span>
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white text-2xl leading-none"
          >
            ×
          </button>
        </div>

        {/* Why they match */}
        {reasons.length > 0 && (
          <div className="px-5 py-3 bg-green-950/30 border-b border-[#1e2d50]">
            <div className="text-xs font-bold text-green-400 uppercase mb-2">Why these are duplicates</div>
            <div className="flex flex-wrap gap-2">
              {reasons.map((r, i) => (
                <span
                  key={i}
                  className="text-xs bg-green-900/50 text-green-200 px-3 py-1 rounded-full flex items-center gap-1"
                >
                  <VscCheck className="text-green-400" /> {r}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          <div className="grid grid-cols-2 gap-0 border-b border-[#1e2d50]">
            <ReportHeader report={reportA} label="Report A" />
            <ReportHeader report={reportB} label="Report B" borderLeft />
          </div>

          {/* Metrics Diff */}
          <DiffSection
            title="Metrics"
            scoreLabel={`${metricScore}% match`}
            scoreColor={metricsDiff.similarity >= 0.9 ? 'green' : metricsDiff.similarity >= 0.7 ? 'yellow' : 'orange'}
            shared={metricsDiff.shared}
            onlyA={metricsDiff.onlyA}
            onlyB={metricsDiff.onlyB}
          />

          {/* Tables Diff */}
          <DiffSection
            title="Source Tables"
            scoreLabel={`${tableScore}% match`}
            scoreColor={tablesDiff.similarity >= 0.9 ? 'green' : tablesDiff.similarity >= 0.7 ? 'yellow' : 'orange'}
            shared={tablesDiff.shared}
            onlyA={tablesDiff.onlyA}
            onlyB={tablesDiff.onlyB}
          />

          {/* Filter Attributes Diff */}
          <DiffSection
            title="Filter Attributes"
            scoreLabel={`${filterScore}% match`}
            scoreColor={filtersDiff.similarity >= 0.9 ? 'green' : filtersDiff.similarity >= 0.7 ? 'yellow' : 'orange'}
            shared={filtersDiff.shared}
            onlyA={filtersDiff.onlyA}
            onlyB={filtersDiff.onlyB}
          />

          {/* SQL side-by-side */}
          <SqlSection
            sqlA={reportA.sql ?? null}
            sqlB={reportB.sql ?? null}
            errorA={reportA.sqlError ?? null}
            errorB={reportB.sqlError ?? null}
          />
        </div>
      </div>
    </div>
  );
}

function ReportHeader({
  report,
  label,
  borderLeft,
}: {
  report: ReportDetail;
  label: string;
  borderLeft?: boolean;
}) {
  return (
    <div className={`p-5 ${borderLeft ? 'border-l border-[#1e2d50]' : ''}`}>
      <div className="text-xs font-bold text-blue-400 uppercase mb-1">{label}</div>
      <h3 className="text-white font-bold text-base mb-2">{report.name}</h3>
      <div className="text-xs text-gray-400 space-y-1">
        <div><span className="text-gray-500">ID:</span> <span className="font-mono">{report.id}</span></div>
        <div><span className="text-gray-500">Owner:</span> {report.owner || '-'}</div>
        <div><span className="text-gray-500">Path:</span> {report.path || '-'}</div>
        <div className="flex gap-4 pt-1">
          <span><span className="text-gray-500">Executions:</span> <span className="text-white font-mono">{report.executions.toLocaleString()}</span></span>
          <span><span className="text-gray-500">Users:</span> <span className="text-white font-mono">{report.users}</span></span>
        </div>
        <div><span className="text-gray-500">Last Exec:</span> {report.lastExec || '-'}</div>
      </div>
    </div>
  );
}

function SqlSection({
  sqlA,
  sqlB,
  errorA,
  errorB,
}: {
  sqlA: string | null;
  sqlB: string | null;
  errorA: string | null;
  errorB: string | null;
}) {
  const [splitView, setSplitView] = useState(true);
  const [ignoreLiterals, setIgnoreLiterals] = useState(true);

  if (!sqlA && !sqlB) {
    return (
      <div className="border-b border-[#1e2d50] p-5">
        <h3 className="text-white font-bold mb-3">SQL</h3>
        <div className="grid grid-cols-2 gap-4">
          <SqlPane sql={null} error={errorA} />
          <SqlPane sql={null} error={errorB} />
        </div>
      </div>
    );
  }

  if (!sqlA || !sqlB) {
    return (
      <div className="border-b border-[#1e2d50] p-5">
        <h3 className="text-white font-bold mb-3">SQL</h3>
        <div className="grid grid-cols-2 gap-4">
          <SqlPane sql={sqlA} error={errorA} />
          <SqlPane sql={sqlB} error={errorB} />
        </div>
      </div>
    );
  }

  const prettyA = prettySql(sqlA);
  const prettyB = prettySql(sqlB);
  const cmpA = ignoreLiterals ? maskLiterals(prettyA) : prettyA;
  const cmpB = ignoreLiterals ? maskLiterals(prettyB) : prettyB;
  const same = cmpA === cmpB;

  return (
    <div className="border-b border-[#1e2d50] p-5">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <h3 className="text-white font-bold">SQL Diff</h3>
          <span
            className={`text-xs font-bold px-3 py-1 rounded-full ${
              same ? 'text-green-400 bg-green-900/30' : 'text-orange-400 bg-orange-900/30'
            }`}
          >
            {same ? 'Identical' : 'Different'}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <label className="flex items-center gap-1.5 text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              checked={ignoreLiterals}
              onChange={(e) => setIgnoreLiterals(e.target.checked)}
              className="accent-blue-500"
            />
            Ignore literals/dates
          </label>
          <button
            onClick={() => setSplitView((v) => !v)}
            className="px-3 py-1 rounded-full border bg-[#0f0f1a] border-[#1e2d50] text-gray-300 hover:text-white"
          >
            {splitView ? 'Unified view' : 'Split view'}
          </button>
        </div>
      </div>
      <div className="rounded border border-[#1e2d50] overflow-hidden text-[11px]">
        <ReactDiffViewer
          oldValue={cmpA}
          newValue={cmpB}
          splitView={splitView}
          compareMethod={DiffMethod.WORDS}
          useDarkTheme={true}
          leftTitle="Report A"
          rightTitle="Report B"
          styles={{
            variables: {
              dark: {
                diffViewerBackground: '#0f0f1a',
                diffViewerColor: '#e5e7eb',
                addedBackground: '#052e16',
                addedColor: '#bbf7d0',
                removedBackground: '#3f0d0d',
                removedColor: '#fecaca',
                wordAddedBackground: '#166534',
                wordRemovedBackground: '#7f1d1d',
                addedGutterBackground: '#052e16',
                removedGutterBackground: '#3f0d0d',
                gutterBackground: '#0f0f1a',
                gutterBackgroundDark: '#0b0b14',
                highlightBackground: '#1e2d50',
                highlightGutterBackground: '#1e2d50',
                codeFoldGutterBackground: '#0f0f1a',
                codeFoldBackground: '#0b0b14',
                emptyLineBackground: '#0b0b14',
                gutterColor: '#6b7280',
                addedGutterColor: '#bbf7d0',
                removedGutterColor: '#fecaca',
                codeFoldContentColor: '#9ca3af',
                diffViewerTitleBackground: '#16213e',
                diffViewerTitleColor: '#e5e7eb',
                diffViewerTitleBorderColor: '#1e2d50',
              },
            },
            contentText: { fontFamily: 'ui-monospace, monospace', fontSize: '11px' },
          }}
        />
      </div>
    </div>
  );
}

function SqlPane({ sql, error }: { sql: string | null; error?: string | null }) {
  if (!sql) {
    const category = classifyError(error);
    return (
      <div className="bg-[#0f0f1a] border border-red-900/50 rounded p-3">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-[10px] font-bold uppercase tracking-wider bg-red-900/50 text-red-200 px-2 py-0.5 rounded">
            SQL not extracted
          </span>
          {category && (
            <span className="text-[10px] text-red-300/80 italic">{category}</span>
          )}
        </div>
        {error ? (
          <pre className="text-[11px] text-red-200 font-mono whitespace-pre-wrap break-words">
            {error}
          </pre>
        ) : (
          <div className="text-xs text-gray-500 italic">
            No extraction attempt recorded.
          </div>
        )}
      </div>
    );
  }
  return (
    <pre className="text-[11px] leading-snug text-gray-200 bg-[#0f0f1a] border border-[#1e2d50] rounded p-3 overflow-auto max-h-[360px] whitespace-pre-wrap font-mono">
      {prettySql(sql)}
    </pre>
  );
}

function classifyError(err: string | null | undefined): string | null {
  if (!err) return null;
  if (/HTTP 500/i.test(err) && !/prompt/i.test(err)) return 'cube-sourced — sqlView unsupported';
  if (/prompted.*required/i.test(err)) return 'prompt required — needs user answers';
  if (/prompt.*HTTP 500/i.test(err)) return 'prompt endpoint error';
  if (/HTTP 403/i.test(err)) return 'permission denied';
  if (/HTTP 400/i.test(err)) return 'bad request';
  if (/HTTP 406/i.test(err)) return 'not acceptable';
  if (/HTTPSConnectionPool|timed out|timeout/i.test(err)) return 'network error';
  return null;
}

function prettySql(s: string): string {
  return s
    .replace(/\r\n/g, '\n')
    .replace(/\t/g, '  ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function maskLiterals(s: string): string {
  return s
    .replace(/'[^']*'/g, "'?'")
    .replace(/\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)?\b/g, '<DATE>')
    .replace(/\bMSTR=[^;']+;/g, 'MSTR=<NAME>;');
}

function DiffSection({
  title,
  scoreLabel,
  scoreColor,
  shared,
  onlyA,
  onlyB,
}: {
  title: string;
  scoreLabel: string;
  scoreColor: 'green' | 'yellow' | 'orange';
  shared: string[];
  onlyA: string[];
  onlyB: string[];
}) {
  const scoreColorClass =
    scoreColor === 'green'
      ? 'text-green-400 bg-green-900/30'
      : scoreColor === 'yellow'
      ? 'text-yellow-400 bg-yellow-900/30'
      : 'text-orange-400 bg-orange-900/30';

  return (
    <div className="border-b border-[#1e2d50] p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-white font-bold">{title}</h3>
        <span className={`text-xs font-bold px-3 py-1 rounded-full ${scoreColorClass}`}>
          {scoreLabel}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-4">
        {/* Report A column */}
        <div>
          <div className="space-y-1">
            {shared.map((item) => (
              <div
                key={'s-' + item}
                className="flex items-center gap-2 text-xs bg-green-950/40 border border-green-800/50 rounded px-3 py-1.5"
              >
                <VscCheck className="text-green-400 flex-shrink-0" />
                <span className="text-green-200">{item}</span>
              </div>
            ))}
            {onlyA.map((item) => (
              <div
                key={'a-' + item}
                className="flex items-center gap-2 text-xs bg-red-950/40 border border-red-800/50 rounded px-3 py-1.5"
              >
                <VscClose className="text-red-400 flex-shrink-0" />
                <span className="text-red-200">{item}</span>
                <span className="text-xs text-gray-500 ml-auto">only in A</span>
              </div>
            ))}
            {shared.length === 0 && onlyA.length === 0 && (
              <div className="text-xs text-gray-600 italic">None</div>
            )}
          </div>
        </div>

        {/* Report B column */}
        <div>
          <div className="space-y-1">
            {shared.map((item) => (
              <div
                key={'sB-' + item}
                className="flex items-center gap-2 text-xs bg-green-950/40 border border-green-800/50 rounded px-3 py-1.5"
              >
                <VscCheck className="text-green-400 flex-shrink-0" />
                <span className="text-green-200">{item}</span>
              </div>
            ))}
            {onlyB.map((item) => (
              <div
                key={'b-' + item}
                className="flex items-center gap-2 text-xs bg-red-950/40 border border-red-800/50 rounded px-3 py-1.5"
              >
                <VscClose className="text-red-400 flex-shrink-0" />
                <span className="text-red-200">{item}</span>
                <span className="text-xs text-gray-500 ml-auto">only in B</span>
              </div>
            ))}
            {shared.length === 0 && onlyB.length === 0 && (
              <div className="text-xs text-gray-600 italic">None</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
