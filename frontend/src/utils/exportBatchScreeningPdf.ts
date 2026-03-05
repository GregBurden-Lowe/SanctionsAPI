import { jsPDF } from 'jspdf'
import type { ScreenedEntity } from '@/types/api'

interface BatchMeta {
  businessReference: string
}

function clean(text: unknown): string {
  if (text == null) return '—'
  const s = String(text).trim()
  return s || '—'
}

function decision(row: ScreenedEntity): string {
  return clean(row.result_json?.['Check Summary']?.Status || row.status)
}

export function generateBatchScreeningPdf(rows: ScreenedEntity[], meta: BatchMeta): void {
  const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4', compress: true })
  const pageW = doc.internal.pageSize.getWidth()
  const pageH = doc.internal.pageSize.getHeight()
  const margin = 10
  const contentW = pageW - margin * 2

  const createdAt = new Date().toISOString()
  const total = rows.length
  const sanctions = rows.filter((r) => Boolean(r.result_json?.['Is Sanctioned'])).length
  const peps = rows.filter((r) => Boolean(r.result_json?.['Is PEP'])).length
  const cleared = rows.filter((r) => !r.result_json?.['Is Sanctioned'] && !r.result_json?.['Is PEP']).length

  doc.setFont('helvetica', 'bold')
  doc.setFontSize(16)
  doc.text('Sanctions & PEP Batch Screening Report', margin, margin + 2)
  doc.setFont('helvetica', 'normal')
  doc.setFontSize(10)
  doc.text(`Business reference: ${meta.businessReference}`, margin, margin + 10)
  doc.text(`Generated (UTC): ${createdAt}`, margin, margin + 16)
  doc.text(`Total checks: ${total}  |  Cleared: ${cleared}  |  Sanctions: ${sanctions}  |  PEP: ${peps}`, margin, margin + 22)
  doc.setDrawColor(180)
  doc.line(margin, margin + 25, pageW - margin, margin + 25)

  let y = margin + 32
  const boxH = 26
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i]
    if (y + boxH > pageH - margin) {
      doc.addPage()
      y = margin
    }
    doc.setDrawColor(210)
    doc.roundedRect(margin, y, contentW, boxH, 2, 2)

    const risk = clean(row.result_json?.['Risk Level'])
    const conf = clean(row.result_json?.Confidence)
    const score = clean(row.result_json?.Score)
    const matched = clean(row.result_json?.['Sanctions Name'])
    const reqBy = clean(row.last_requestor)
    const checkedAt = clean(row.result_json?.['Check Summary']?.Date || row.last_screened_at)

    doc.setFont('helvetica', 'bold')
    doc.setFontSize(10)
    doc.text(`${i + 1}. ${clean(row.display_name)} (${clean(row.entity_type)})`, margin + 2, y + 5)
    doc.setFont('helvetica', 'normal')
    doc.setFontSize(9)
    doc.text(`Decision: ${decision(row)} | Risk: ${risk} | Confidence: ${conf} | Score: ${score}`, margin + 2, y + 10)
    doc.text(`Requested by: ${reqBy} | Checked: ${checkedAt}`, margin + 2, y + 15)
    doc.text(`Entity key: ${clean(row.entity_key)}`, margin + 2, y + 20)
    doc.text(`Match: ${matched}`, margin + 2, y + 24)
    y += boxH + 3
  }

  const fileRef = meta.businessReference.replace(/[^a-zA-Z0-9-_]/g, '_').slice(0, 64) || 'batch'
  doc.save(`batch-screening-${fileRef}-${Date.now()}.pdf`)
}

