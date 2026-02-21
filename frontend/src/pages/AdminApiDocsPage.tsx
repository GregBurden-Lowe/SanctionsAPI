import { useEffect, useState } from 'react'
import { Button, Card, CardBody, CardHeader, CardTitle, ErrorBox, SectionHeader } from '@/components'
import { getAdminOpenApiSchema } from '@/api/client'

type OpenApiSchema = {
  openapi?: string
  info?: { title?: string; version?: string }
  paths?: Record<string, Record<string, unknown>>
}

export function AdminApiDocsPage() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [schema, setSchema] = useState<OpenApiSchema | null>(null)
  const [showJson, setShowJson] = useState(false)

  const loadSchema = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await getAdminOpenApiSchema()
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError((data as { detail?: string }).detail ?? 'Failed to load API schema.')
        setSchema(null)
        return
      }
      setSchema(data as OpenApiSchema)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load API schema.')
      setSchema(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadSchema()
  }, [])

  const pathEntries = Object.entries(schema?.paths ?? {})

  return (
    <div className="px-10 pb-10">
      <div className="max-w-5xl space-y-6">
        <SectionHeader title="API Docs" />
        <Card>
          <CardHeader>
            <CardTitle>OpenAPI schema</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <div className="flex items-center gap-2">
              <Button type="button" variant="secondary" onClick={loadSchema} disabled={loading}>
                {loading ? 'Refreshing…' : 'Refresh schema'}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setShowJson((v) => !v)} disabled={!schema}>
                {showJson ? 'Hide raw JSON' : 'Show raw JSON'}
              </Button>
            </div>
            {error && <ErrorBox message={error} />}
            {schema && (
              <div className="space-y-3">
                <p className="text-sm text-text-secondary">
                  <span className="font-medium text-text-primary">{schema.info?.title ?? 'API'}</span>
                  {' · '}
                  OpenAPI {schema.openapi ?? '—'}
                  {' · '}
                  Version {schema.info?.version ?? '—'}
                </p>
                <p className="text-sm text-text-secondary">Endpoints: {pathEntries.length}</p>
                {!showJson && (
                  <div className="rounded-lg border border-border bg-app p-3 max-h-[420px] overflow-auto">
                    <table className="w-full text-sm text-left">
                      <thead>
                        <tr className="border-b border-border/80">
                          <th className="py-2 pr-4 font-medium text-text-primary">Method</th>
                          <th className="py-2 pr-4 font-medium text-text-primary">Path</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pathEntries.flatMap(([path, methods]) =>
                          Object.keys(methods || {}).map((m) => (
                            <tr key={`${m}:${path}`} className="border-b border-border/60">
                              <td className="py-1.5 pr-4 uppercase text-text-primary">{m}</td>
                              <td className="py-1.5 pr-4 text-text-secondary font-mono">{path}</td>
                            </tr>
                          )),
                        )}
                      </tbody>
                    </table>
                  </div>
                )}
                {showJson && (
                  <div className="rounded-lg border border-border bg-app p-3 max-h-[520px] overflow-auto">
                    <pre className="text-xs text-text-secondary whitespace-pre-wrap font-mono">
                      {JSON.stringify(schema, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
