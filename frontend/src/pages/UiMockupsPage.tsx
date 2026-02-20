const shell = 'rounded-3xl border border-border/70 shadow-[0_20px_70px_rgba(0,0,0,0.08)] overflow-hidden'
const rail = 'w-44 bg-[#0f1f2b] text-white/80 p-4 flex flex-col gap-3'
const frame = 'min-h-[420px] bg-gradient-to-br from-[#f4f7fa] via-[#eef3f7] to-[#e6edf3] p-5'
const top = 'h-10 rounded-xl bg-white/90 border border-slate-200 mb-4 flex items-center justify-between px-3'
const block = 'rounded-xl border border-slate-200/80 bg-white/90'

function MockupFrame({
  title,
  subtitle,
  accent,
  children,
}: {
  title: string
  subtitle: string
  accent: string
  children: React.ReactNode
}) {
  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-lg font-semibold text-text-primary">{title}</h3>
        <p className="text-sm text-text-secondary">{subtitle}</p>
      </div>
      <div className={shell}>
        <div className="flex">
          <aside className={rail}>
            <div className="text-sm font-semibold text-white">Sanctions Intelligence</div>
            <div className={`h-8 rounded-lg ${accent}`} />
            <div className="h-7 rounded-md bg-white/10" />
            <div className="h-7 rounded-md bg-white/10" />
            <div className="h-7 rounded-md bg-white/10" />
          </aside>
          <div className={`flex-1 ${frame}`}>
            <div className={top}>
              <div className="h-4 w-48 rounded bg-slate-200" />
              <div className="h-6 w-24 rounded-md bg-slate-200" />
            </div>
            {children}
          </div>
        </div>
      </div>
    </section>
  )
}

export function UiMockupsPage() {
  return (
    <div className="px-10 pb-10 space-y-8">
      <div className="max-w-4xl">
        <h2 className="text-2xl font-semibold tracking-tight text-text-primary">UI Mockups</h2>
        <p className="text-sm text-text-secondary mt-2">
          Three hybrid directions combining analyst-console density with client-portal polish. These are preview
          concepts only and do not change existing workflows.
        </p>
      </div>

      <MockupFrame
        title="1) Balanced Hybrid"
        subtitle="Clear executive layout with operational detail cards and restrained accent color."
        accent="bg-gradient-to-r from-[#0ea5e9] to-[#0284c7]"
      >
        <div className="grid grid-cols-3 gap-4">
          <div className={`${block} p-4 col-span-2`}>
            <div className="flex items-center justify-between mb-3">
              <div className="h-4 w-36 rounded bg-slate-200" />
              <div className="h-6 w-20 rounded-md bg-[#0ea5e9]/20" />
            </div>
            <div className="space-y-2">
              <div className="h-10 rounded-lg bg-slate-100" />
              <div className="h-10 rounded-lg bg-slate-100" />
              <div className="h-10 rounded-lg bg-slate-100" />
            </div>
          </div>
          <div className={`${block} p-4`}>
            <div className="h-4 w-24 rounded bg-slate-200 mb-3" />
            <div className="h-20 rounded-lg bg-slate-100 mb-2" />
            <div className="h-20 rounded-lg bg-slate-100" />
          </div>
          <div className={`${block} p-4 col-span-3`}>
            <div className="h-4 w-40 rounded bg-slate-200 mb-3" />
            <div className="grid grid-cols-4 gap-2">
              <div className="h-12 rounded-md bg-slate-100" />
              <div className="h-12 rounded-md bg-slate-100" />
              <div className="h-12 rounded-md bg-slate-100" />
              <div className="h-12 rounded-md bg-slate-100" />
            </div>
          </div>
        </div>
      </MockupFrame>

      <MockupFrame
        title="2) Data-First Hybrid"
        subtitle="Higher information density for analysts with compact hierarchy and stronger risk signal."
        accent="bg-gradient-to-r from-[#f97316] to-[#ea580c]"
      >
        <div className="grid grid-cols-4 gap-3">
          <div className={`${block} p-3 col-span-4`}>
            <div className="grid grid-cols-6 gap-2">
              <div className="h-7 rounded bg-slate-100 col-span-2" />
              <div className="h-7 rounded bg-slate-100" />
              <div className="h-7 rounded bg-slate-100" />
              <div className="h-7 rounded bg-[#f97316]/20" />
              <div className="h-7 rounded bg-[#ef4444]/20" />
            </div>
          </div>
          <div className={`${block} p-3 col-span-3`}>
            <div className="h-4 w-44 rounded bg-slate-200 mb-2" />
            <div className="space-y-1.5">
              <div className="h-8 rounded bg-slate-100" />
              <div className="h-8 rounded bg-slate-100" />
              <div className="h-8 rounded bg-slate-100" />
              <div className="h-8 rounded bg-slate-100" />
              <div className="h-8 rounded bg-slate-100" />
            </div>
          </div>
          <div className={`${block} p-3`}>
            <div className="h-4 w-16 rounded bg-slate-200 mb-2" />
            <div className="h-24 rounded bg-[#111827]" />
            <div className="h-7 rounded bg-slate-100 mt-2" />
            <div className="h-7 rounded bg-slate-100 mt-1.5" />
          </div>
        </div>
      </MockupFrame>

      <MockupFrame
        title="3) Premium Hybrid"
        subtitle="More whitespace and elevated visual finish while keeping compliance controls front-and-center."
        accent="bg-gradient-to-r from-[#14b8a6] to-[#0f766e]"
      >
        <div className="grid grid-cols-2 gap-4">
          <div className={`${block} p-5 bg-gradient-to-br from-white to-[#f2f8f7]`}>
            <div className="h-4 w-28 rounded bg-slate-200 mb-3" />
            <div className="h-28 rounded-2xl bg-[#0f172a]" />
            <div className="h-10 rounded-lg bg-slate-100 mt-3" />
          </div>
          <div className={`${block} p-5`}>
            <div className="h-4 w-36 rounded bg-slate-200 mb-3" />
            <div className="space-y-2">
              <div className="h-12 rounded-xl bg-slate-100" />
              <div className="h-12 rounded-xl bg-slate-100" />
              <div className="h-12 rounded-xl bg-slate-100" />
            </div>
          </div>
          <div className={`${block} p-5 col-span-2`}>
            <div className="flex items-center justify-between mb-3">
              <div className="h-4 w-40 rounded bg-slate-200" />
              <div className="h-7 w-24 rounded-lg bg-[#14b8a6]/20" />
            </div>
            <div className="grid grid-cols-5 gap-2">
              <div className="h-14 rounded-xl bg-slate-100" />
              <div className="h-14 rounded-xl bg-slate-100" />
              <div className="h-14 rounded-xl bg-slate-100" />
              <div className="h-14 rounded-xl bg-slate-100" />
              <div className="h-14 rounded-xl bg-slate-100" />
            </div>
          </div>
        </div>
      </MockupFrame>
    </div>
  )
}
