/**
 * Generate a portrait A4 PDF for screening outcomes, styled as a numbered-section
 * compliance document (matching the Sanctions Screening Report sample format).
 */
import { jsPDF } from 'jspdf'
import html2canvas from 'html2canvas'
import type { OpCheckResponse } from '@/types/api'

// ─── Colours ────────────────────────────────────────────────────────────────
const C = {
  navyDark: '#0c2d3e',
  navyMid:  '#1a4a60',
  tealAccent: '#047481',
  tealLight: '#e6f4f6',
  rowAlt: '#f5f9fa',
  border: '#c8dde4',
  text: '#111827',
  muted: '#4b5563',
  clearBg: '#d1fae5',
  clearText: '#065f46',
  clearBorder: '#6ee7b7',
  fpBg: '#fff7ed',
  fpText: '#9a3412',
  fpBorder: '#fed7aa',
  matchBg: '#fee2e2',
  matchText: '#991b1b',
  matchBorder: '#fca5a5',
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function escapeHtml(input: string): string {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function safe(v: unknown): string {
  if (v == null || v === '') return '—'
  return escapeHtml(String(v))
}

function formatEntityTypeLabel(t: string): string {
  return t === 'Organization' ? 'Organisation' : t
}


function verificationHash(input: string): string {
  let hash = 2166136261
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i)
    hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24)
  }
  return (hash >>> 0).toString(16).padStart(8, '0').toUpperCase()
}

// ─── Outcome logic ────────────────────────────────────────────────────────────

interface OutcomeMeta {
  label: string       // e.g. "POTENTIAL MATCH, CLEARED AFTER REVIEW"
  detail: string      // one-liner below the label
  bg: string
  text: string
  border: string
}

function outcomeMetaFor(result: OpCheckResponse): OutcomeMeta {
  const status = result['Check Summary']?.Status ?? ''
  const hasFP = status.includes('False Positive')
  const hasReview = !!result['Review Decision']

  if (hasFP || (hasReview && result['Review Decision']!.outcome?.toLowerCase().includes('false positive'))) {
    return {
      label: 'POTENTIAL MATCH, CLEARED AFTER REVIEW',
      detail: '1 alert raised, discounted as false positive. No true matches. Record may proceed.',
      bg: C.fpBg, text: C.fpText, border: C.fpBorder,
    }
  }
  if (result['Is Sanctioned']) {
    return {
      label: 'POTENTIAL MATCH — UNDER REVIEW',
      detail: 'Potential sanctions match identified. Stop and escalate for enhanced review before proceeding.',
      bg: C.matchBg, text: C.matchText, border: C.matchBorder,
    }
  }
  return {
    label: 'NO MATCH IDENTIFIED — CLEAR TO PROCEED',
    detail: 'No potential sanctions match identified under current rules. This is not proof of identity.',
    bg: C.clearBg, text: C.clearText, border: C.clearBorder,
  }
}

// ─── Reusable HTML snippets ───────────────────────────────────────────────────

function sectionHeader(num: number, title: string): string {
  return `<div class="sec-head"><span class="sec-num">${num}</span>${escapeHtml(title)}</div>`
}

function kvTable(rows: Array<[string, string]>): string {
  return `<table class="kv">
    ${rows.map(([k, v], i) => `<tr class="${i % 2 === 1 ? 'alt' : ''}"><th>${escapeHtml(k)}</th><td>${v}</td></tr>`).join('')}
  </table>`
}

function dataTable(headers: string[], rows: string[][], colWidths?: string[]): string {
  const colGroup = colWidths
    ? `<colgroup>${colWidths.map((w) => `<col style="width:${w}">`).join('')}</colgroup>`
    : ''
  return `<table class="dt">
    ${colGroup}
    <thead><tr>${headers.map((h) => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((r, i) => `<tr class="${i % 2 === 1 ? 'alt' : ''}">${r.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>
  </table>`
}

// ─── Main HTML builder ────────────────────────────────────────────────────────

export interface SearchDetails {
  searchName: string
  entityType: string
  searchDob: string
  searchCountry?: string
  businessReference: string
  reasonForCheck: string
  requestor: string
  searchBackend?: string
}

function buildReportHtml(result: OpCheckResponse, search: SearchDetails): string {
  const summary = result['Check Summary']
  const checkedAt = summary?.Date || result.screened_at || '—'
  const generatedUtc = new Date().toUTCString()
  const outcome = outcomeMetaFor(result)
  const backendLabel = search.searchBackend === 'postgres_beta' ? 'Postgres (default)' : 'Parquet (fallback)'

  const docIdSeed = [result.entity_key || '', search.searchName || '', search.requestor || '', checkedAt, String(result.Score ?? '')].join('|')
  const docId = `SCR-${verificationHash(docIdSeed)}-${verificationHash(`${docIdSeed}|A`)}`

  const matchedName = result['Sanctions Name']
  const matchedDob = result['Birth Date']
  const matchedRegime = result.Regime
  const hasAlert = !!(matchedName || matchedDob || matchedRegime || result['Is Sanctioned'])
  const reviewDecision = result['Review Decision']
  const engineResult = result['Engine Result']
  const candidateEvidence = (result['Candidate Evidence'] ?? []).slice(0, 10)
  const sourceCoverage = result['Source Coverage']

  // ── Section 1: Report details ──
  const sec1 = sectionHeader(1, 'Report details') + kvTable([
    ['Report ID', safe(docId)],
    ['Screening trigger', safe(search.reasonForCheck || 'Not provided')],
    ['Screened at', safe(checkedAt)],
    ['Requested by', safe(search.requestor)],
    ['Engine / backend', `ScreenCheck v7.3 (${escapeHtml(backendLabel)})`],
    ['Business reference', safe(search.businessReference || 'Not provided')],
  ])

  // ── Section 2: Subject screened ──
  const sec2 = sectionHeader(2, 'Subject screened') + kvTable([
    ['Subject type', safe(formatEntityTypeLabel(search.entityType || '—'))],
    ['Full name', safe(search.searchName)],
    ['Date of birth', safe(search.searchDob?.trim() ? search.searchDob : 'Not provided')],
    ['Country', safe(search.searchCountry?.trim() ? search.searchCountry : 'Not provided')],
  ])

  // ── Section 3: Lists and parameters ──
  const coveredLists = sourceCoverage?.required_lists ?? ['UK', 'UN', 'EU', 'OFAC']
  const listTableRows: string[][] = [
    ['UK Sanctions List', 'FCDO / HM Treasury', coveredLists.includes('UK') ? '✓' : '—'],
    ['UN Security Council Consolidated List', 'United Nations', coveredLists.includes('UN') ? '✓' : '—'],
    ['EU Consolidated Financial Sanctions', 'European Commission', coveredLists.includes('EU') ? '✓' : '—'],
    ['Specially Designated Nationals (SDN)', 'US Treasury OFAC', coveredLists.includes('OFAC') ? '✓' : '—'],
  ]
  const listIngested = sourceCoverage?.ingested_at
    ? `Lists ingested: ${escapeHtml(sourceCoverage.ingested_at)}`
    : ''
  const sec3 = sectionHeader(3, 'Lists and parameters')
    + dataTable(['List', 'Source', 'Included'], listTableRows, ['55%', '35%', '10%'])
    + kvTable([
        ['Matching method', 'Fuzzy name match with phonetic and transliteration variants'],
        ['Alert threshold', 'Name similarity score ≥ 70'],
        ['Secondary identifiers', 'Date of birth, country of origin'],
        ...(listIngested ? [['Data freshness', listIngested] as [string, string]] : []),
        ...(sourceCoverage?.status ? [['Coverage status', safe(sourceCoverage.status)] as [string, string]] : []),
      ])

  // ── Section 4: Alerts ──
  let sec4: string
  if (hasAlert) {
    const personCheck = result['Entity Type Checks']?.Person
    const orgCheck = result['Entity Type Checks']?.Organization
    const alertRows: string[][] = []
    if (matchedName || result['Is Sanctioned']) {
      const sources = (summary?.Source || '').split(/[;,\n]+/).filter(Boolean).join(', ') || '—'
      const disposition = summary?.Status?.includes('False Positive') ? '<span class="fp">False positive</span>'
        : result['Is Sanctioned'] ? '<span class="match">Potential match</span>'
        : '—'
      const topCandidate = candidateEvidence.find(c => c.disposition === 'potential_match')
      const topAliasLabel = topCandidate?.matched_alias && topCandidate.matched_name
        ? `<br><small>Alias: ${safe(topCandidate.matched_name)}</small>` : ''
      alertRows.push([
        '1',
        `${safe(matchedName || search.searchName)}${topAliasLabel}<br><small>${safe(matchedDob || '')} ${safe(matchedRegime || '')}</small>`,
        safe(sources),
        safe(String(result.Score ?? '—')),
        safe(result.Confidence || '—'),
        disposition,
      ])
    }
    sec4 = sectionHeader(4, 'Alerts')
      + (alertRows.length
        ? dataTable(['#', 'Matched entry', 'List / source', 'Score', 'Confidence band', 'Disposition'], alertRows, ['4%', '28%', '28%', '8%', '16%', '16%'])
        : `<p class="none-msg">No alerts raised.</p>`)
    if (personCheck || orgCheck) {
      sec4 += kvTable([
        ...(personCheck ? [['Person type check', `${personCheck.is_match === true ? 'Match' : personCheck.is_match === false ? 'Clear' : 'Unknown'} — ${escapeHtml(personCheck.status || '—')}`] as [string, string]] : []),
        ...(orgCheck ? [['Organisation type check', `${orgCheck.is_match === true ? 'Match' : orgCheck.is_match === false ? 'Clear' : 'Unknown'} — ${escapeHtml(orgCheck.status || '—')}`] as [string, string]] : []),
      ])
    }
  } else {
    sec4 = sectionHeader(4, 'Alerts') + `<p class="none-msg">No alerts raised. No name similarity match reached the alert threshold.</p>`
  }

  // ── Section 5: Alert review / Candidate evidence ──
  let sec5 = ''
  const sec5Rows: string[][] = []

  if (candidateEvidence.length > 0) {
    for (const c of candidateEvidence) {
      const dobMatch = c.dob_assessment === 'exact' ? '✓ Exact'
        : c.dob_assessment === 'compatible' ? '~ Compatible'
        : c.dob_assessment === 'conflicting' ? '<span class="fp">✗ Conflicting</span>'
        : '— Unknown'
      const ctryMatch = c.country_assessment === 'compatible' ? '~ Compatible'
        : c.country_assessment === 'conflicting' ? '<span class="fp">✗ Conflicting</span>'
        : '— Unknown'
      const disp = c.disposition === 'potential_match'
        ? '<span class="match">Potential match</span>'
        : '<span class="clear">Excluded</span>'
      const aliasLabel = c.matched_alias && c.matched_name
        ? `<br><small>Alias: ${safe(c.matched_name)}</small>` : ''
      const aliasCountLabel = c.alias_count && c.alias_count > 1
        ? `<br><small>${c.alias_count} aliases listed</small>` : ''
      const aliasQualityLabel = c.matched_alias_metadata?.map(a => `<br><small>Alias quality: ${safe(a.quality || 'Not specified')}${a.type ? ` (${safe(a.type)})` : ''}</small>`).join('') || ''
      const showDob = (value: string) => /^unparsed birthdate:/i.test(value) || /<[^>]+>/.test(value) ? 'Uninterpretable source entry' : value
      const dobDisplay = c.all_birth_dates && c.all_birth_dates.length > 1
        ? safe(c.all_birth_dates.map(showDob).join('; '))
        : safe(c.birth_date ? showDob(c.birth_date) : '—')
      const linked = c.linked_listing_evidence
        ? `<br><small>Linked records: ${safe(c.linked_listing_evidence.record_ids.join(', '))}; review: ${safe(c.linked_listing_evidence.review_reference)} (${safe(c.linked_listing_evidence.reviewed_by)})<br>${Object.entries(c.linked_listing_evidence.birth_dates_by_record).map(([id, dates]) => `${safe(id)}: ${safe(dates || 'No DOB recorded')}`).join('<br>')}</small>`
        : ''
      sec5Rows.push([
        `${safe(c.name)}${aliasLabel}${aliasCountLabel}${aliasQualityLabel}<br><small>${safe(c.dataset)}</small>${c.birth_place ? `<br><small>Birthplace: ${safe(c.birth_place)}</small>` : ''}${linked}`,
        safe(String(c.name_score) + '/100'),
        dobDisplay + `<br><small>${dobMatch}</small>` + (c.birth_notes?.map(n => `<br><small>DOB source note (${safe(n.date)}): ${safe(n.note)}</small>`).join('') || ''),
        safe(c.country || '—') + `<br><small>${ctryMatch}</small>`,
        disp,
      ])
    }
    sec5 = sectionHeader(5, 'Alert review — candidate comparison')
      + `<p class="note">Similarity scores do not confirm identity. Missing details do not establish a mismatch.</p>`
      + dataTable(['Candidate / source', 'Name score', 'Date of birth', 'Country', 'Disposition'], sec5Rows, ['28%', '12%', '22%', '22%', '16%'])
  } else if (reviewDecision) {
    const engineStatus = engineResult?.['Check Summary']?.Status || '—'
    sec5 = sectionHeader(5, 'Alert review')
      + kvTable([
          ['Outcome', safe(reviewDecision.outcome)],
          ['Reviewed by', safe(reviewDecision.actor)],
          ['Reviewed at', safe(reviewDecision.decided_at)],
          ['Rationale', safe(reviewDecision.notes)],
          ['Original engine outcome', safe(engineStatus)],
          ...(reviewDecision.stale ? [['Note', 'This decision may require reassessment.'] as [string, string]] : []),
        ])
  }

  // ── Section 6: Audit trail ──
  const auditRows: string[][] = []
  const addAudit = (ts: string, event: string, actor: string) =>
    auditRows.push([safe(ts), safe(event), safe(actor)])

  addAudit(checkedAt, 'Screening request received', safe(search.requestor || 'System'))
  addAudit(checkedAt, `Screening completed${hasAlert ? ', 1 alert raised' : ' — no alerts'}`, 'ScreenCheck')
  if (reviewDecision) {
    addAudit(reviewDecision.decided_at, 'Alert reviewed', safe(reviewDecision.actor))
    addAudit(reviewDecision.decided_at, `Decision: ${escapeHtml(reviewDecision.outcome)}`, safe(reviewDecision.actor))
  }
  addAudit(generatedUtc, 'Report generated', 'ScreenCheck')

  const sec6 = sectionHeader(6, 'Audit trail')
    + dataTable(['Timestamp', 'Event', 'User / system'], auditRows, ['35%', '40%', '25%'])

  // ── Section 7: Ongoing monitoring ──
  const monitorChange = result['Monitoring Change']
  const monitorRows: Array<[string, string]> = [
    ['Status', 'Enrolled in periodic re-screening against list updates'],
    ['Last screened', safe(checkedAt)],
    ['Retention', 'Record retained per applicable data retention policy'],
  ]
  if (monitorChange?.message) {
    monitorRows.push(['Monitoring note', safe(monitorChange.message)])
  }
  const sec7 = sectionHeader(7, 'Ongoing monitoring') + kvTable(monitorRows)

  // ── Section 8: Technical record ──
  const sec8 = sectionHeader(8, 'Technical record') + kvTable([
    ['Document ID', safe(docId)],
    ['Request ID', safe(result.request_id || 'Not available')],
    ['Run ID', safe(result.run_id || 'Not available')],
    ['Entity key', safe(result.entity_key || 'Not available')],
    ['Search backend', safe(backendLabel)],
    ['Generated (UTC)', safe(generatedUtc)],
  ])

  // ── Disclaimer ──
  const disclaimer = `<div class="disclaimer">This report records the result of automated screening${reviewDecision ? ' and analyst review' : ''} at the time shown. It reflects list versions available at that time and is not a guarantee that the subject is not, or will not become, subject to sanctions. If a true match is confirmed, funds must be frozen and the matter escalated to the MLRO for reporting to the relevant authority. Do not disclose screening outcomes to the subject.</div>`

  return `
<div class="report">
  <div class="page-header">
    <div class="company">Compliance Screening System &nbsp;|&nbsp; Financial Crime Compliance</div>
    <div class="conf">CONFIDENTIAL</div>
  </div>
  <div class="sub-header">
    Report ID ${escapeHtml(docId)}&nbsp; | &nbsp;Generated ${escapeHtml(generatedUtc)}
  </div>

  <div class="title-block">
    <h1>Sanctions Screening Report</h1>
    <p class="subtitle">${escapeHtml(formatEntityTypeLabel(search.entityType || 'Entity'))} screening</p>
  </div>

  <div class="outcome-box" style="background:${outcome.bg};color:${outcome.text};border:2px solid ${outcome.border}">
    <div class="outcome-label">OUTCOME:</div>
    <div class="outcome-main">${escapeHtml(outcome.label)}</div>
    <div class="outcome-detail">${escapeHtml(outcome.detail)}</div>
  </div>

  <div class="sections">
    <div class="section">${sec1}</div>
    <div class="section">${sec2}</div>
    <div class="section">${sec3}</div>
    <div class="section">${sec4}</div>
    ${sec5 ? `<div class="section">${sec5}</div>` : ''}
    <div class="section">${sec6}</div>
    <div class="section">${sec7}</div>
    <div class="section">${sec8}</div>
    ${disclaimer}
  </div>
</div>

<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #fff; }
.report {
  width: 794px;
  font-family: Arial, Helvetica, sans-serif;
  font-size: 12px;
  color: ${C.text};
  background: #fff;
  padding: 24px 28px 32px;
  line-height: 1.45;
  word-spacing: normal;
  letter-spacing: normal;
}

/* Page header */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 8px;
  border-bottom: 2px solid ${C.navyDark};
  margin-bottom: 4px;
}
.company { font-size: 11px; font-weight: 600; color: ${C.navyDark}; }
.conf { font-size: 11px; font-weight: 800; color: #c0392b; letter-spacing: 1px; word-spacing: 2px; }
.sub-header {
  font-size: 9.5px;
  color: ${C.muted};
  padding: 3px 0 12px;
  border-bottom: 1px solid ${C.border};
  margin-bottom: 14px;
}

/* Title */
.title-block { margin-bottom: 14px; }
.title-block h1 { font-size: 22px; font-weight: 800; color: ${C.navyDark}; line-height: 1.2; }
.title-block .subtitle { font-size: 12px; color: ${C.muted}; margin-top: 2px; }

/* Outcome box */
.outcome-box {
  padding: 12px 16px;
  border-radius: 6px;
  margin-bottom: 18px;
}
.outcome-label {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .1em;
  opacity: .75;
  margin-bottom: 2px;
}
.outcome-main {
  font-size: 16px;
  font-weight: 800;
  line-height: 1.2;
  margin-bottom: 4px;
}
.outcome-detail { font-size: 11px; font-weight: 600; opacity: .85; }

/* Sections */
.section { margin-bottom: 14px; }

.sec-head {
  display: flex;
  align-items: center;
  gap: 8px;
  background: ${C.navyDark};
  color: #fff;
  font-size: 11.5px;
  font-weight: 700;
  padding: 5px 10px;
  border-radius: 4px 4px 0 0;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  word-spacing: 2px;
}
.sec-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: rgba(255,255,255,.2);
  font-size: 10px;
  font-weight: 800;
  flex-shrink: 0;
}

/* KV table */
.kv {
  width: 100%;
  border-collapse: collapse;
  font-size: 11.5px;
  border: 1px solid ${C.border};
  border-top: none;
}
.kv th {
  width: 34%;
  text-align: left;
  padding: 5px 8px;
  font-weight: 700;
  color: ${C.muted};
  background: ${C.tealLight};
  border-bottom: 1px solid ${C.border};
  vertical-align: top;
}
.kv td {
  padding: 5px 8px;
  border-bottom: 1px solid ${C.border};
  font-weight: 500;
  vertical-align: top;
  word-break: break-word;
}
.kv tr.alt th, .kv tr.alt td { background: ${C.rowAlt}; }
.kv tr:last-child th, .kv tr:last-child td { border-bottom: none; }

/* Data table */
.dt {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
  border: 1px solid ${C.border};
  border-top: none;
}
.dt thead tr { background: ${C.navyMid}; }
.dt thead th {
  padding: 5px 8px;
  text-align: left;
  color: #fff;
  font-weight: 700;
  font-size: 10.5px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  word-spacing: 2px;
  border-right: 1px solid rgba(255,255,255,.15);
}
.dt thead th:last-child { border-right: none; }
.dt tbody td {
  padding: 5px 8px;
  border-bottom: 1px solid ${C.border};
  vertical-align: top;
  border-right: 1px solid ${C.border};
}
.dt tbody td:last-child { border-right: none; }
.dt tbody tr.alt td { background: ${C.rowAlt}; }
.dt tbody tr:last-child td { border-bottom: none; }
.dt small { color: ${C.muted}; font-size: 10px; }

/* Status chips */
.fp { color: ${C.fpText}; font-weight: 700; }
.match { color: ${C.matchText}; font-weight: 700; }
.clear { color: ${C.clearText}; font-weight: 700; }


/* Notes and empty states */
.none-msg {
  padding: 8px 10px;
  font-size: 11.5px;
  color: ${C.muted};
  font-style: italic;
  border: 1px solid ${C.border};
  border-top: none;
  background: ${C.tealLight};
}
.note {
  padding: 5px 10px;
  font-size: 10.5px;
  color: ${C.muted};
  border: 1px solid ${C.border};
  border-top: none;
  background: #fffbeb;
}

/* Disclaimer */
.disclaimer {
  margin-top: 18px;
  padding: 10px 12px;
  border-top: 2px solid ${C.navyDark};
  font-size: 10px;
  color: ${C.muted};
  line-height: 1.55;
}
</style>
`
}

// ─── PDF generator ────────────────────────────────────────────────────────────

/**
 * Generate and download the screening result PDF.
 * Portrait A4; content rendered as a numbered-section compliance document.
 */
export async function generateScreeningPdf(result: OpCheckResponse, search: SearchDetails): Promise<void> {
  const wrapper = document.createElement('div')
  wrapper.style.cssText = 'position:fixed;left:-10000px;top:0;width:794px;z-index:-1;background:#fff'
  wrapper.innerHTML = buildReportHtml(result, search)
  document.body.appendChild(wrapper)

  try {
    const canvas = await html2canvas(wrapper.firstElementChild as HTMLElement, {
      backgroundColor: '#ffffff',
      scale: 2,
      useCORS: true,
      logging: false,
      width: 794,
    })

    // A4 portrait
    const MARGIN_MM = 10
    const FOOTER_H_MM = 8

    const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4', compress: true })

    const pageW = doc.internal.pageSize.getWidth()   // 210
    const pageH = doc.internal.pageSize.getHeight()  // 297

    const printW = pageW - MARGIN_MM * 2
    const printH = pageH - MARGIN_MM * 2 - FOOTER_H_MM

    // Scale the full canvas width to fit the printable area
    const scale = printW / canvas.width  // mm per canvas pixel

    // How many canvas pixels fit in one page's printable height
    const canvasPixelsPerPage = Math.floor(printH / scale)
    const totalPages = Math.ceil(canvas.height / canvasPixelsPerPage)

    const safeName = search.searchName.replace(/[^a-zA-Z0-9\s-]/g, '').slice(0, 40) || 'screening'
    const filename = `screening-result-${safeName.replace(/\s+/g, '-')}-${Date.now()}.pdf`
    const checkedAt = result['Check Summary']?.Date || '—'

    for (let page = 0; page < totalPages; page++) {
      if (page > 0) doc.addPage()

      const srcY = page * canvasPixelsPerPage
      const srcH = Math.min(canvasPixelsPerPage, canvas.height - srcY)

      // Slice this page's portion of the canvas
      const slice = document.createElement('canvas')
      slice.width = canvas.width
      slice.height = srcH
      const ctx = slice.getContext('2d')!
      ctx.drawImage(canvas, 0, srcY, canvas.width, srcH, 0, 0, canvas.width, srcH)

      const imgData = slice.toDataURL('image/png')
      const renderH = srcH * scale

      doc.addImage(imgData, 'PNG', MARGIN_MM, MARGIN_MM, printW, renderH, undefined, 'FAST')

      // Footer
      doc.setFont('helvetica', 'normal')
      doc.setFontSize(8)
      doc.setTextColor(100, 100, 100)
      doc.text(`Checked at: ${checkedAt}`, MARGIN_MM, pageH - 4)
      doc.text(`Page ${page + 1} of ${totalPages}`, pageW / 2, pageH - 4, { align: 'center' })
      doc.text(`Generated: ${new Date().toISOString()}`, pageW - MARGIN_MM, pageH - 4, { align: 'right' })
    }

    doc.save(filename)
  } finally {
    document.body.removeChild(wrapper)
  }
}
