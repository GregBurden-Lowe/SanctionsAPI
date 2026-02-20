/**
 * Generate a landscape PDF for screening outcomes.
 * The visual body is rendered as an image; entity key is added as real text so it can be copied.
 */
import { jsPDF } from 'jspdf'
import html2canvas from 'html2canvas'
import type { OpCheckResponse, TopMatch } from '@/types/api'

const UK_PATTERNS = ['uk', 'hmt', 'ofsi', 'hm treasury', 'uk fcdo', 'uk financial sanctions']

function isUKSource(item: string): boolean {
  const lower = item.toLowerCase()
  return UK_PATTERNS.some((p) => lower.includes(p))
}

function parseSources(source: string | undefined): {
  list: string[]
  hasUK: boolean
  otherCount: number
  summaryLines: string[]
} {
  const raw = (source ?? '').trim()
  if (!raw) return { list: [], hasUK: false, otherCount: 0, summaryLines: ['—'] }
  const list = raw
    .split(/[;,\n]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  const items = list.length > 0 ? list : [raw]
  const hasUK = items.some(isUKSource)
  const otherCount = items.filter((i) => !isUKSource(i)).length
  const summaryLines: string[] = []
  summaryLines.push(hasUK ? 'UK sanctions: Yes' : 'UK sanctions: No')
  if (otherCount > 0) summaryLines.push(`Other sanctions lists: ${otherCount}`)
  return { list: items, hasUK, otherCount, summaryLines }
}

function sourceBadgeMeta(item: string): { label: string; cls: string; mark: string } {
  const lower = item.toLowerCase()
  if (lower.includes('united nations') || lower === 'un' || lower.includes(' un ')) {
    return { label: 'UN', cls: 'src-un', mark: 'UN' }
  }
  if (lower.includes('eu') || lower.includes('european union') || lower.includes('eu council')) {
    return { label: 'EU', cls: 'src-eu', mark: 'EU' }
  }
  if (lower.includes('ofac') || lower.includes('u.s.') || lower.includes('us treasury')) {
    return { label: 'OFAC', cls: 'src-ofac', mark: 'US' }
  }
  if (lower.includes('hm treasury') || lower.includes('hmt') || lower.includes('ofsi') || lower.includes('uk')) {
    return { label: 'HM Treasury', cls: 'src-hmt', mark: 'UK' }
  }
  return { label: item, cls: 'src-other', mark: '•' }
}

function expandSourceBadgesForDisplay(sourceList: string[]): string[] {
  const cleaned = sourceList.map((s) => s.trim()).filter(Boolean)
  if (cleaned.length === 0) return []
  const defaults = ['United Nations', 'EU Council', 'OFAC', 'HM Treasury']
  const isGeneric = (v: string): boolean =>
    v === 'opensanctions' || v.includes('open sanctions') || v.includes('postgres watchlist')

  const normalized = cleaned.map((s) => s.toLowerCase())
  const hasGeneric = normalized.some(isGeneric)
  if (!hasGeneric) return cleaned

  const withoutGeneric = cleaned.filter((s) => !isGeneric(s.toLowerCase()))
  const merged = [...defaults, ...withoutGeneric]
  return [...new Set(merged)]
}

function formatTopMatch(m: TopMatch): { name: string; score: number } {
  if (Array.isArray(m) && m.length >= 2) return { name: m[0], score: m[1] }
  if (m && typeof m === 'object' && 'name' in m) {
    return { name: (m as { name: string }).name, score: (m as { score: number }).score ?? 0 }
  }
  return { name: String(m), score: 0 }
}

function escapeHtml(input: string): string {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function formatEntityTypeLabel(entityType: string): string {
  return entityType === 'Organization' ? 'Organisation' : entityType
}

function getGuidanceText(result: OpCheckResponse): string {
  if (result['Is Sanctioned']) {
    return 'Potential sanctions match. Stop and escalate for enhanced review before proceeding.'
  }
  if (result['Is PEP']) {
    return 'PEP indicator found. Continue with enhanced due diligence and record rationale.'
  }
  return 'No sanctions or PEP match found under current rules.'
}

function statusTone(result: OpCheckResponse): string {
  if (result['Is Sanctioned']) return '#ef4444'
  if (result['Is PEP']) return '#0284c7'
  return '#16a34a'
}

function verificationHash(input: string): string {
  let hash = 2166136261
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i)
    hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24)
  }
  return (hash >>> 0).toString(16).padStart(8, '0').toUpperCase()
}

function buildSnapshotHtml(result: OpCheckResponse, search: SearchDetails): string {
  const summary = result['Check Summary']
  const { list: sourceList, hasUK, otherCount, summaryLines } = parseSources(summary?.Source)
  const topMatches = (result['Top Matches'] ?? []).map(formatTopMatch).slice(0, 5)
  const tone = statusTone(result)
  const checkedAt = summary?.Date || '—'
  const sourceSummary = `${hasUK ? 'UK sanctions: Yes' : 'UK sanctions: No'}${otherCount > 0 ? ` · Other lists: ${otherCount}` : ''}`
  const matchedName = result['Sanctions Name'] || '—'
  const matchedDob = result['Birth Date'] || '—'
  const matchedRegime = result.Regime || '—'
  const backendLabel = search.searchBackend === 'postgres_beta' ? 'Postgres (Default)' : 'Original (Parquet fallback)'
  const hasMatchedSubject = Boolean(result['Match Found'] && (result['Sanctions Name'] || result.Regime || result['Birth Date']))

  const docIdSeed = [
    result.entity_key || '',
    search.searchName || '',
    search.requestor || '',
    checkedAt || '',
    String(result.Score ?? ''),
  ].join('|')
  const docId = `SCR-${verificationHash(docIdSeed)}-${verificationHash(`${docIdSeed}|A`)}`
  const docFingerprint = verificationHash(`${docIdSeed}|FINGERPRINT|${summary?.Status || ''}|${result['Risk Level'] || ''}`)
  const generatedUtc = new Date().toISOString()

  const topMatchRows = topMatches.length
    ? topMatches
        .map(
          (m) => `\n<tr><td>${escapeHtml(m.name)}</td><td class="score-col">${m.score}</td></tr>`,
        )
        .join('')
    : '<tr><td colspan="2" class="muted">No similarity suggestions.</td></tr>'

  const sourceBadgeItems = expandSourceBadgesForDisplay(sourceList)
  const sourceBadges = sourceBadgeItems.length
    ? sourceBadgeItems
        .slice(0, 12)
        .map((s) => {
          const meta = sourceBadgeMeta(s)
          return `<span class="src-badge ${meta.cls}" title="${escapeHtml(s)}"><span class="mark">${escapeHtml(meta.mark)}</span>${escapeHtml(meta.label)}</span>`
        })
        .join('')
    : '<div class="muted">No source list details provided.</div>'

  return `
  <div class="page">
    <div class="watermark">SYSTEM GENERATED • AUDIT RECORD • SYSTEM GENERATED • AUDIT RECORD</div>

    <header class="header">
      <div>
        <p class="eyebrow">Compliance Screening Record</p>
        <h1>Sanctions &amp; PEP Screening Report</h1>
      </div>
      <div class="doc-id">Document ID: ${escapeHtml(docId)}</div>
    </header>

    <section class="summary-band">
      <div class="summary-cell status" style="--tone:${tone}">
        <span class="label">Status</span>
        <strong>${escapeHtml(summary?.Status || 'Unknown')}</strong>
        <div class="outcome">${escapeHtml(getGuidanceText(result))}</div>
      </div>
      <div class="summary-cell">
        <span class="label">Risk Level</span>
        <strong>${escapeHtml(result['Risk Level'] || '—')}</strong>
      </div>
      <div class="summary-cell">
        <span class="label">Confidence</span>
        <strong>${escapeHtml(result.Confidence || '—')}</strong>
      </div>
      <div class="summary-cell">
        <span class="label">Score</span>
        <strong>${escapeHtml(String(result.Score ?? '—'))}</strong>
      </div>
    </section>

    <main class="content">
      <section class="panel">
        <h2>Screening Request</h2>
        <table class="kv-table">
          <tr><th>Name / Organisation</th><td>${escapeHtml(search.searchName || '—')}</td></tr>
          <tr><th>Entity Type</th><td>${escapeHtml(formatEntityTypeLabel(search.entityType || '—'))}</td></tr>
          <tr><th>Date of Birth Input</th><td>${escapeHtml(search.searchDob?.trim() ? search.searchDob : 'Not provided')}</td></tr>
          <tr><th>Requested By</th><td>${escapeHtml(search.requestor || '—')}</td></tr>
        </table>
      </section>

      <section class="panel">
        <h2>Decision &amp; Actions</h2>
        <table class="kv-table">
          <tr><th>Checked At</th><td>${escapeHtml(checkedAt)}</td></tr>
          <tr><th>Sanctions Match</th><td>${result['Is Sanctioned'] ? 'Yes' : 'No'}</td></tr>
          <tr><th>PEP Indicator</th><td>${result['Is PEP'] ? 'Yes' : 'No'}</td></tr>
          <tr><th>Source Summary</th><td>${escapeHtml(sourceSummary || '—')}</td></tr>
        </table>
      </section>

      <section class="panel">
        <h2>Matched Subject</h2>
        ${
          hasMatchedSubject
            ? `<table class="kv-table">
          <tr><th>Name</th><td>${escapeHtml(matchedName)}</td></tr>
          <tr><th>Date of Birth</th><td>${escapeHtml(matchedDob)}</td></tr>
          <tr><th>Regime</th><td>${escapeHtml(matchedRegime)}</td></tr>
        </table>`
            : '<div class="muted compact">No matched subject identified for this check.</div>'
        }
      </section>

      <section class="panel">
        <h2>Sources Reviewed</h2>
        <div class="source-badges">${sourceBadges}</div>
      </section>

      <section class="panel span2">
        <h2>Name Similarity Suggestions</h2>
        <table class="data-table">
          <thead><tr><th>Candidate Name</th><th class="score-col">Score</th></tr></thead>
          <tbody>${topMatchRows}</tbody>
        </table>
      </section>

      <section class="panel span2">
        <h2>Audit Metadata</h2>
        <table class="kv-table audit">
          <tr><th>Document ID</th><td>${escapeHtml(docId)}</td></tr>
          <tr><th>Verification Fingerprint</th><td>${escapeHtml(docFingerprint)}</td></tr>
          <tr><th>Entity Key Reference</th><td>${escapeHtml(result.entity_key || 'Not available')}</td></tr>
          <tr><th>Search Backend</th><td>${escapeHtml(backendLabel)}</td></tr>
          <tr><th>Generated (UTC)</th><td>${escapeHtml(generatedUtc)}</td></tr>
          <tr><th>Coverage Notes</th><td>${escapeHtml(summaryLines.join(' · '))}</td></tr>
        </table>
      </section>
    </main>

    <div class="footer-note">System-generated report. Validate using Document ID and Entity Key reference.</div>
  </div>
  <style>
    * { box-sizing: border-box; font-family: MediumLL, Inter, system-ui, sans-serif; }
    .page {
      width: 1400px;
      min-height: 860px;
      padding: 18px;
      color: #0f172a;
      background:
        repeating-linear-gradient(135deg, rgba(2,132,199,.02), rgba(2,132,199,.02) 8px, rgba(14,165,233,.02) 8px, rgba(14,165,233,.02) 16px),
        #eef3f7;
      position: relative;
      overflow: hidden;
    }
    .watermark {
      position: absolute;
      top: 46%;
      left: -180px;
      transform: rotate(-24deg);
      font-size: 28px;
      letter-spacing: .08em;
      font-weight: 700;
      color: rgba(2,132,199,.07);
      white-space: nowrap;
      pointer-events: none;
      user-select: none;
    }
    .header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid rgba(148,163,184,.34);
      background: #fff;
      position: relative;
      z-index: 2;
    }
    .eyebrow { margin: 0; font-size: 11px; letter-spacing: .12em; text-transform: uppercase; color: #475569; font-weight: 700; }
    h1 { margin: 2px 0 0 0; font-size: 24px; line-height: 1.2; }
    .doc-id {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .06em;
      text-transform: uppercase;
      border: 1px solid rgba(148,163,184,.34);
      background: #f8fafc;
      border-radius: 999px;
      padding: 7px 10px;
      white-space: nowrap;
    }
    .summary-band {
      margin-top: 10px;
      display: grid;
      grid-template-columns: 2fr 1fr 1fr 1fr;
      gap: 8px;
      position: relative;
      z-index: 2;
    }
    .summary-cell {
      border: 1px solid rgba(148,163,184,.32);
      background: #fff;
      border-radius: 10px;
      padding: 8px 10px;
    }
    .summary-cell.status { border-left: 4px solid var(--tone); }
    .summary-cell .label {
      display: block;
      font-size: 11px;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: #64748b;
      margin-bottom: 2px;
      font-weight: 700;
    }
    .summary-cell strong { font-size: 18px; line-height: 1.2; }
    .summary-cell.status strong { font-size: 28px; line-height: 1.05; display: block; }
    .outcome {
      margin-top: 6px;
      font-size: 11px;
      line-height: 1.35;
      color: #334155;
      font-weight: 600;
    }
    .content {
      margin-top: 10px;
      display: grid;
      grid-template-columns: 1.25fr 1fr;
      gap: 8px;
      position: relative;
      z-index: 2;
    }
    .panel {
      border: 1px solid rgba(148,163,184,.3);
      border-radius: 10px;
      background: #fff;
      padding: 10px;
    }
    .panel.span2 { grid-column: span 2; }
    h2 {
      margin: 0 0 8px 0;
      font-size: 12px;
      letter-spacing: .1em;
      text-transform: uppercase;
      color: #475569;
      font-weight: 700;
    }
    .kv-table, .data-table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-size: 12px;
    }
    .kv-table th, .kv-table td {
      padding: 6px 6px;
      border-bottom: 1px solid rgba(148,163,184,.24);
      vertical-align: top;
    }
    .kv-table th {
      width: 34%;
      text-align: left;
      color: #475569;
      font-weight: 700;
    }
    .kv-table td { color: #0f172a; font-weight: 600; word-break: break-word; }
    .kv-table tr:last-child th, .kv-table tr:last-child td { border-bottom: 0; }
    .data-table th, .data-table td {
      padding: 6px;
      border-bottom: 1px solid rgba(148,163,184,.24);
      text-align: left;
      vertical-align: top;
    }
    .data-table thead th {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .08em;
      color: #475569;
      font-weight: 700;
      background: #f8fafc;
    }
    .data-table tbody td { font-size: 12px; color: #0f172a; }
    .data-table tr:last-child td { border-bottom: 0; }
    .score-col { width: 70px; text-align: right; font-weight: 700; }
    .n-col { width: 34px; text-align: center; color: #64748b; font-weight: 700; }
    .source-badges { display: flex; flex-wrap: wrap; gap: 8px; }
    .src-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      border: 1px solid rgba(148,163,184,.34);
      background: #f8fafc;
      color: #0f172a;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .04em;
      padding: 5px 10px;
      text-transform: uppercase;
    }
    .src-badge .mark {
      width: 18px;
      height: 18px;
      border-radius: 999px;
      display: inline-grid;
      place-items: center;
      text-align: center;
      font-size: 8px;
      font-weight: 800;
      line-height: 18px;
      letter-spacing: .03em;
      border: 1px solid rgba(148,163,184,.45);
      background: #e2e8f0;
      color: #0f172a;
      padding: 0;
      vertical-align: middle;
    }
    .src-badge.src-un .mark { background: #d8f5fb; border-color: #67e8f9; color: #0c4a6e; }
    .src-badge.src-eu .mark { background: #dbeafe; border-color: #93c5fd; color: #1e3a8a; }
    .src-badge.src-ofac .mark { background: #fee2e2; border-color: #fca5a5; color: #7f1d1d; }
    .src-badge.src-hmt .mark { background: #dbeafe; border-color: #93c5fd; color: #1e3a8a; }
    .src-badge.src-other .mark { background: #e2e8f0; border-color: #cbd5e1; color: #475569; }
    .muted { color: #64748b; font-style: italic; }
    .muted.compact { padding: 4px 2px; font-size: 12px; }
    .audit td { font-family: DmMono, ui-monospace, monospace; font-size: 11px; font-weight: 600; }
    .footer-note {
      margin-top: 8px;
      font-size: 9px;
      color: #64748b;
      letter-spacing: .04em;
      text-transform: uppercase;
      text-align: right;
      position: relative;
      z-index: 2;
    }
  </style>
  `
}

export interface SearchDetails {
  searchName: string
  entityType: string
  searchDob: string
  requestor: string
  searchBackend?: string
}

/**
 * Generate and download the screening result PDF.
 * Landscape page; content rendered as image to mirror UI cards.
 * Entity key is added as selectable text for later copy/verification.
 */
export async function generateScreeningPdf(result: OpCheckResponse, search: SearchDetails): Promise<void> {
  const wrapper = document.createElement('div')
  wrapper.style.position = 'fixed'
  wrapper.style.left = '-10000px'
  wrapper.style.top = '0'
  wrapper.style.width = '1400px'
  wrapper.style.zIndex = '-1'
  wrapper.innerHTML = buildSnapshotHtml(result, search)
  document.body.appendChild(wrapper)

  try {
    const canvas = await html2canvas(wrapper.firstElementChild as HTMLElement, {
      backgroundColor: '#eef3f7',
      scale: 2,
      useCORS: true,
      logging: false,
    })

    const imgData = canvas.toDataURL('image/png')
    const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4', compress: true })

    const pageW = doc.internal.pageSize.getWidth()
    const pageH = doc.internal.pageSize.getHeight()
    const margin = 8
    const footerBand = 14
    const maxW = pageW - margin * 2
    const maxH = pageH - margin * 2 - footerBand

    const imgW = canvas.width
    const imgH = canvas.height
    const ratio = Math.min(maxW / imgW, maxH / imgH)
    const renderW = imgW * ratio
    const renderH = imgH * ratio
    const x = (pageW - renderW) / 2
    const y = margin

    doc.addImage(imgData, 'PNG', x, y, renderW, renderH, undefined, 'FAST')

    const keyText = result.entity_key?.trim() ? `Entity key: ${result.entity_key}` : 'Entity key: not available'
    const checkedAt = result['Check Summary']?.Date || '—'
    doc.setFont('helvetica', 'normal')
    doc.setFontSize(9)
    doc.setTextColor(60, 60, 60)
    doc.text(`Checked at: ${checkedAt}`, margin, pageH - 8)
    doc.text(keyText, pageW / 2, pageH - 8, { align: 'center' })
    doc.text(`Generated: ${new Date().toISOString()}`, pageW - margin, pageH - 8, { align: 'right' })
    doc.setTextColor(0, 0, 0)

    const safeName = search.searchName.replace(/[^a-zA-Z0-9\s-]/g, '').slice(0, 40) || 'screening'
    const filename = `screening-result-${safeName.replace(/\s+/g, '-')}-${Date.now()}.pdf`
    doc.save(filename)
  } finally {
    document.body.removeChild(wrapper)
  }
}
