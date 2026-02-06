/**
 * Generate an audit PDF from a screening result and search details.
 * Layout and structure signal system-generated compliance artefact.
 * Frontend-only; uses existing result data. No backend calls.
 */
import { jsPDF } from 'jspdf'
import type { OpCheckResponse } from '@/types/api'

const MARGIN = 20
const MARGIN_LEFT_APPENDIX = 26
const PAGE_WIDTH = 210
const PAGE_HEIGHT = 297
const MAX_WIDTH = PAGE_WIDTH - MARGIN * 2
const LABEL_WIDTH = 48
const VALUE_X = MARGIN + LABEL_WIDTH
const VALUE_MAX_W = PAGE_WIDTH - VALUE_X - MARGIN
const LINE_SMALL = 4.5
const LINE_BODY = 5
const LINE_LARGE = 6
const GAP_IN_SECTION = 4
const GAP_BETWEEN_SECTIONS = 14
const GAP_AFTER_OUTCOME = 18
const FONT_DOC_TITLE = 14
const FONT_OUTCOME = 12
const FONT_SECTION_TITLE = 10
const FONT_BODY = 10
const FONT_LABEL = 9
const FONT_APPENDIX_TITLE = 9
const FONT_APPENDIX_BODY = 8
const FONT_FOOTER = 7
const FOOTER_Y = PAGE_HEIGHT - 14

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

function getGuidanceText(result: OpCheckResponse): string {
  if (result['Is Sanctioned'])
    return 'This result indicates a sanctions match. Further verification is required before proceeding.'
  if (result['Is PEP'])
    return 'This individual is identified as a Politically Exposed Person. This does not prevent proceeding, but should be recorded for audit purposes.'
  return 'No match was found against sanctions or PEP lists. No further action required.'
}

/** Stable ref ID from check data (deterministic per check). */
function refId(searchName: string, requestor: string, checkDate: string): string {
  const s = `${searchName}|${requestor}|${checkDate}`
  let h = 0
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0
  const hex = Math.abs(h).toString(16).slice(0, 6)
  return `ref-${(checkDate || '').replace(/\s/g, '')}-${hex}`
}

export interface SearchDetails {
  searchName: string
  entityType: string
  searchDob: string
  requestor: string
}

function writeWrapped(doc: jsPDF, text: string, x: number, y: number, maxW: number, lineH: number): number {
  const lines = doc.splitTextToSize(text, maxW)
  doc.text(lines, x, y)
  return y + lines.length * lineH
}

function ensureSpace(doc: jsPDF, y: number, need: number): number {
  if (y + need > FOOTER_Y - 10) {
    doc.addPage()
    return MARGIN
  }
  return y
}

/** Key/value row: label left, value right (wrapped). Returns new y. */
function rowKV(
  doc: jsPDF,
  label: string,
  value: string,
  x: number,
  y: number
): number {
  doc.setFontSize(FONT_LABEL)
  doc.setFont('helvetica', 'normal')
  doc.text(label, x, y)
  doc.setFontSize(FONT_BODY)
  return writeWrapped(doc, value, VALUE_X, y, VALUE_MAX_W, LINE_BODY)
}

/**
 * Generate and download the screening result PDF. Throws on error.
 */
export function generateScreeningPdf(result: OpCheckResponse, search: SearchDetails): void {
  const doc = new jsPDF({ unit: 'mm' })
  const summary = result['Check Summary']
  const checkDate = summary?.Date ?? ''
  const generatedAt = new Date().toISOString()
  const docRef = refId(search.searchName, search.requestor, checkDate)
  let y = MARGIN

  // —— 1. HEADER (minimal, no heavy title)
  doc.setFontSize(FONT_DOC_TITLE)
  doc.setFont('helvetica', 'normal')
  doc.text('Sanctions & PEP Screening Result', MARGIN, y)
  y += LINE_LARGE + 2
  doc.setFontSize(FONT_LABEL)
  doc.text(`Check date / time: ${checkDate}`, MARGIN, y)
  y += GAP_BETWEEN_SECTIONS

  // —— 2. SEARCH DETAILS (key/value grid)
  y = ensureSpace(doc, y, 32)
  doc.setFontSize(FONT_SECTION_TITLE)
  doc.setFont('helvetica', 'normal')
  doc.text('Search details', MARGIN, y)
  y += LINE_BODY + GAP_IN_SECTION
  y = rowKV(doc, 'Name searched', search.searchName || '—', MARGIN, y) + GAP_IN_SECTION
  y = rowKV(doc, 'Entity type', search.entityType || '—', MARGIN, y) + GAP_IN_SECTION
  y =
    rowKV(
      doc,
      'Date of birth',
      search.searchDob?.trim() ? search.searchDob : 'Not provided',
      MARGIN,
      y
    ) + GAP_IN_SECTION
  y = rowKV(doc, 'Requestor', search.requestor || '—', MARGIN, y) + GAP_IN_SECTION
  y += GAP_BETWEEN_SECTIONS

  // —— 3. SCREENING OUTCOME (isolated block, clear hierarchy)
  y = ensureSpace(doc, y, 40)
  const outcome = summary?.Status ?? 'Unknown'
  y += 6
  doc.setFontSize(FONT_OUTCOME)
  doc.setFont('helvetica', 'normal')
  doc.text('Outcome', MARGIN, y)
  y += LINE_LARGE + 2
  doc.text(outcome, MARGIN, y)
  y += LINE_LARGE + GAP_AFTER_OUTCOME

  // —— 4. RESULT METRICS (key/value grid, subordinate to outcome)
  doc.setFontSize(FONT_SECTION_TITLE)
  doc.text('Result metrics', MARGIN, y)
  y += LINE_BODY + GAP_IN_SECTION
  y = rowKV(doc, 'Risk level', result['Risk Level'], MARGIN, y) + GAP_IN_SECTION
  y = rowKV(doc, 'Confidence', result.Confidence, MARGIN, y) + GAP_IN_SECTION
  y = rowKV(doc, 'Score', String(result.Score), MARGIN, y) + GAP_IN_SECTION
  y += GAP_BETWEEN_SECTIONS

  // —— 5. INTERPRETATION (linear text only)
  y = ensureSpace(doc, y, 22)
  doc.setFontSize(FONT_SECTION_TITLE)
  doc.text('Interpretation', MARGIN, y)
  y += LINE_BODY + GAP_IN_SECTION
  doc.setFontSize(FONT_BODY)
  y = writeWrapped(doc, getGuidanceText(result), MARGIN, y, MAX_WIDTH, LINE_BODY) + GAP_BETWEEN_SECTIONS

  // —— 6. MATCHED SUBJECT (if applicable, key/value grid)
  if (result['Match Found']) {
    y = ensureSpace(doc, y, 28)
    doc.setFontSize(FONT_SECTION_TITLE)
    doc.text('Matched subject details', MARGIN, y)
    y += LINE_BODY + GAP_IN_SECTION
    if (result['Sanctions Name']) {
      y = rowKV(doc, 'Matched name', result['Sanctions Name'], MARGIN, y) + GAP_IN_SECTION
    }
    if (result.Regime) {
      y = rowKV(doc, 'Regime', result.Regime, MARGIN, y) + GAP_IN_SECTION
    }
    if (result['Birth Date']) {
      y = rowKV(doc, 'Date of birth', result['Birth Date'], MARGIN, y) + GAP_IN_SECTION
    }
    y += GAP_BETWEEN_SECTIONS
  }

  // —— 7. SANCTIONS SOURCES (prioritised summary)
  const { list: sourceList, hasUK, otherCount } = parseSources(summary?.Source)
  if (sourceList.length > 0) {
    y = ensureSpace(doc, y, 24)
    doc.setFontSize(FONT_SECTION_TITLE)
    doc.text('Sanctions sources', MARGIN, y)
    y += LINE_BODY + GAP_IN_SECTION
    doc.setFontSize(FONT_BODY)
    y = rowKV(doc, 'UK sanctions', hasUK ? 'Yes' : 'No', MARGIN, y) + GAP_IN_SECTION
    if (otherCount > 0) {
      y =
        rowKV(
          doc,
          'Other lists',
          otherCount === 1 ? '1*' : `${otherCount}*`,
          MARGIN,
          y
        ) + 2
      doc.setFontSize(FONT_LABEL)
      doc.text('* Listed in Reference below.', MARGIN, y)
      y += LINE_BODY
    }
    y += GAP_BETWEEN_SECTIONS
  }

  // —— 8. REFERENCE (appendix: separated, smaller, indented)
  if (sourceList.length > 0) {
    y = ensureSpace(doc, y, 20)
    y += 8
  }
  if (sourceList.length > 0) {
    y = ensureSpace(doc, y, 15)
    doc.setDrawColor(0, 0, 0)
    doc.setLineWidth(0.2)
    doc.line(MARGIN, y - 2, PAGE_WIDTH - MARGIN, y - 2)
    y += 6
    doc.setFontSize(FONT_APPENDIX_TITLE)
    doc.setFont('helvetica', 'normal')
    doc.text('Reference — sanction list sources', MARGIN_LEFT_APPENDIX, y)
    y += LINE_SMALL + 4
    doc.setFontSize(FONT_APPENDIX_BODY)
    for (const item of sourceList) {
      y = ensureSpace(doc, y, LINE_SMALL + 1)
      y = writeWrapped(doc, item, MARGIN_LEFT_APPENDIX, y, PAGE_WIDTH - MARGIN_LEFT_APPENDIX - MARGIN, LINE_SMALL) + 1
    }
  }

  // —— 9. SYSTEM METADATA FOOTER (every page)
  const addFooter = (pageY: number) => {
    doc.setFontSize(FONT_FOOTER)
    doc.setFont('helvetica', 'normal')
    doc.setTextColor(80, 80, 80)
    doc.text(`Generated: ${generatedAt}`, MARGIN, pageY)
    doc.text(`Ref: ${docRef}`, MARGIN, pageY + LINE_SMALL)
    doc.setTextColor(0, 0, 0)
  }
  const pageCount = doc.getNumberOfPages()
  for (let p = 1; p <= pageCount; p++) {
    doc.setPage(p)
    addFooter(FOOTER_Y)
  }

  const safeName = search.searchName.replace(/[^a-zA-Z0-9\s-]/g, '').slice(0, 40) || 'screening'
  const filename = `screening-result-${safeName.replace(/\s+/g, '-')}-${Date.now()}.pdf`
  doc.save(filename)
}
