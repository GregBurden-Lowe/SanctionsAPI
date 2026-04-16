import asyncio
import logging
import os
from datetime import datetime

import screening_db
from ai_triage import run_ai_triage_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ai_triage_worker")


async def main() -> None:
    enabled = os.environ.get("AI_TRIAGE_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
    run_hour = max(0, min(23, int(os.environ.get("AI_TRIAGE_RUN_HOUR", "22"))))
    poll_seconds = max(60, int(os.environ.get("AI_TRIAGE_POLL_SECONDS", "300")))
    batch_limit = max(1, int(os.environ.get("AI_TRIAGE_BATCH_LIMIT", "25")))
    last_run_key = None

    pool = await screening_db.get_pool()
    if pool is None:
        raise RuntimeError("DATABASE_URL is required for ai_triage_worker")

    logger.info("Starting AI triage worker enabled=%s run_hour=%s batch_limit=%s", enabled, run_hour, batch_limit)
    while True:
        now = datetime.now()
        run_key = now.strftime("%Y-%m-%d")
        should_run = enabled and now.hour == run_hour and run_key != last_run_key
        if should_run:
            try:
                async with pool.acquire() as conn:
                    result = await run_ai_triage_batch(
                        conn,
                        screening_db_module=screening_db,
                        trigger_type="scheduled",
                        triggered_by="ai_triage_worker",
                        limit=batch_limit,
                    )
                last_run_key = run_key
                logger.info("Scheduled AI triage completed: %s", result)
            except Exception as e:
                logger.exception("Scheduled AI triage failed: %s", e)
                last_run_key = run_key
        await asyncio.sleep(poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())
