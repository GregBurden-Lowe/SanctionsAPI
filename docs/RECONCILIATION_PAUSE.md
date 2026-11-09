# Pausing automatic re-screening

Admins can pause and resume automatic re-screening from Admin tools. The pause holds queued `all_sources_reconciliation` and `uk_delta_rescreen` jobs, prevents monitoring from scheduling more, and leaves ordinary screening jobs available. A job already running when the pause is set may finish. Pending jobs and the active cycle remain intact. Resume continues that cycle and normal monitoring; it does not discard or restart it. Source refresh remains available while paused.

The state is a `.reconciliation-paused` file in the shared `data` directory, visible to the API and workers. It survives service restarts and deployments as long as those processes use the same persistent volume. Both pause and resume are admin-only and audit logged. In a multi-host installation the file must live on a genuinely shared volume; a host-local file cannot coordinate multiple hosts. Do not start an older worker that does not honour this flag.
