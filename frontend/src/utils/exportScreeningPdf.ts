/**
 * Generate a landscape PDF that visually mirrors the results-page card layout.
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

function parseSources(source: string | undefined): { list: string[]; hasUK: boolean; otherCount: number } {
  const raw = (source ?? '').trim()
  if (!raw) return { list: [], hasUK: false, otherCount: 0 }
  const list = raw
    .split(/[;,\n]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  const items = list.length > 0 ? list : [raw]
  const hasUK = items.some(isUKSource)
  const otherCount = items.filter((i) => !isUKSource(i)).length
  return { list: items, hasUK, otherCount }
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

function getGuidanceText(result: OpCheckResponse): string {
  if (result['Is Sanctioned']) {
    return 'Potential sanctions match. Additional verification is required before proceeding.'
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

function buildSnapshotHtml(result: OpCheckResponse, search: SearchDetails): string {
  const summary = result['Check Summary']
  const { list: sourceList, hasUK, otherCount } = parseSources(summary?.Source)
  const topMatches = (result['Top Matches'] ?? []).map(formatTopMatch).slice(0, 8)
  const tone = statusTone(result)
  const checkedAt = summary?.Date || '—'
  const sourceSummary = `${hasUK ? 'UK sanctions: Yes' : 'UK sanctions: No'}${otherCount > 0 ? ` · Other lists: ${otherCount}` : ''}`

  const matchesHtml = topMatches.length
    ? topMatches
        .map(
          (m) => `
            <div class="row">
              <div class="row-title">${escapeHtml(m.name)}</div>
              <div class="chip">Score ${m.score}</div>
            </div>
          `,
        )
        .join('')
    : `<div class="muted">No similarity suggestions.</div>`

  const sourceListHtml = sourceList.length
    ? `<ul class="source-list">${sourceList.map((s) => `<li>${escapeHtml(s)}</li>`).join('')}</ul>`
    : `<div class="muted">No source list details provided.</div>`

  return `
  <div class="page">
    <div class="hero">
      <div class="hero-left">
        <div class="overline">SCREENING RESULT</div>
        <div class="headline">${escapeHtml(summary?.Status || 'Unknown')}</div>
        <div class="sub">${escapeHtml(getGuidanceText(result))}</div>
      </div>
      <div class="hero-right">
        <div class="pill tone">Risk ${escapeHtml(result['Risk Level'] || '—')}</div>
        <div class="pill">Confidence ${escapeHtml(result.Confidence || '—')}</div>
        <div class="pill">Score ${escapeHtml(String(result.Score ?? '—'))}</div>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Original search details</h3>
        <div class="kv"><span>Name or organization</span><b>${escapeHtml(search.searchName || '—')}</b></div>
        <div class="kv"><span>Entity type</span><b>${escapeHtml(search.entityType || '—')}</b></div>
        <div class="kv"><span>Date of birth</span><b>${escapeHtml(search.searchDob?.trim() ? search.searchDob : 'Not provided')}</b></div>
        <div class="kv"><span>Requestor</span><b>${escapeHtml(search.requestor || '—')}</b></div>
      </div>

      <div class="card">
        <h3>Decision summary</h3>
        <div class="kv"><span>Checked at</span><b>${escapeHtml(checkedAt)}</b></div>
        <div class="kv"><span>Source summary</span><b>${escapeHtml(sourceSummary || '—')}</b></div>
        <div class="kv"><span>Sanctioned</span><b>${result['Is Sanctioned'] ? 'Yes' : 'No'}</b></div>
        <div class="kv"><span>PEP</span><b>${result['Is PEP'] ? 'Yes' : 'No'}</b></div>
      </div>

      <div class="card span2">
        <h3>Name similarity suggestions</h3>
        <div class="rows">${matchesHtml}</div>
      </div>

      <div class="card span2">
        <h3>Sources</h3>
        ${sourceListHtml}
      </div>
    </div>
  </div>
  <style>
    * { box-sizing: border-box; font-family: MediumLL, Inter, system-ui, sans-serif; }
    .page { width: 1400px; padding: 28px; background: #eef3f7; color: #0f172a; }
    .hero {
      display: flex; justify-content: space-between; gap: 20px;
      background: #ffffff; border: 1px solid rgba(148,163,184,.3); border-radius: 14px;
      box-shadow: 0 14px 50px rgba(2, 6, 23, .08); padding: 22px;
      border-left: 6px solid ${tone};
    }
    .overline { font-size: 11px; letter-spacing: .1em; color: #475569; font-weight: 700; }
    .headline { font-size: 34px; line-height: 1.1; font-weight: 700; margin-top: 4px; }
    .sub { margin-top: 8px; font-size: 14px; color: #475569; max-width: 760px; }
    .hero-right { display: flex; flex-wrap: wrap; gap: 8px; align-content: flex-start; justify-content: flex-end; }
    .pill { font-size: 12px; padding: 8px 10px; border-radius: 10px; background: #f1f5f9; border: 1px solid rgba(148,163,184,.35); font-weight: 600; }
    .pill.tone { background: ${tone}22; border-color: ${tone}66; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 16px; margin-top: 16px; }
    .card {
      background: #ffffff; border: 1px solid rgba(148,163,184,.3); border-radius: 14px;
      box-shadow: 0 10px 30px rgba(2, 6, 23, .05); padding: 16px;
    }
    .card.span2 { grid-column: span 2; }
    h3 { margin: 0 0 12px 0; font-size: 14px; letter-spacing: .08em; color: #475569; text-transform: uppercase; }
    .kv {
      display: flex; justify-content: space-between; gap: 14px; padding: 8px 0; border-bottom: 1px solid rgba(148,163,184,.25);
      font-size: 14px;
    }
    .kv:last-child { border-bottom: 0; }
    .kv > span { color: #475569; }
    .kv > b { color: #0f172a; text-align: right; font-weight: 600; }
    .rows { display: grid; gap: 8px; }
    .row {
      display: flex; justify-content: space-between; gap: 12px; align-items: center;
      border: 1px solid rgba(148,163,184,.28); border-radius: 10px; padding: 10px;
      background: #f8fafc;
    }
    .row-title { font-size: 14px; font-weight: 500; color: #0f172a; }
    .chip { font-size: 12px; color: #0f172a; padding: 4px 8px; border: 1px solid rgba(148,163,184,.32); border-radius: 999px; background: #fff; }
    .source-list { margin: 0; padding-left: 18px; display: grid; gap: 6px; color: #0f172a; font-size: 13px; }
    .muted { color: #475569; font-size: 13px; }
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
