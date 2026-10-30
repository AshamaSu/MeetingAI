"""后台任务循环：认领到期 job → 处理 → 成功/重试/死亡。
重试策略：检测未完成等时序类错误用固定 30s；其他错误指数退避 30*2^n 封顶 600s。
"""
import asyncio
import traceback

from . import db, detector, writer


class RetryLater(Exception):
    """时序未就绪（如 summary 先于检测完成），固定间隔重试。"""


async def process_job(settings, speakr, lexicon, job) -> None:
    rid, event = job["recording_id"], job["event"]

    if event == "recording.transcription.completed":
        hits = await detector.detect(settings, speakr, lexicon, rid)
        for h in hits:
            h["recording_id"] = rid
        with db.connect(settings.db_path) as con:
            db.clear_alerts(con, rid)  # 重扫替换旧命中（词表可能已变）
            db.save_alerts(con, hits)
            db.save_scan(con, rid, len(hits),
                         min((h["level"] for h in hits), default=None))
        if hits:
            await writer.write_tags_and_notes(speakr, rid, hits)
        # B8 兜底：summary 已存在则同步追加清单；尚未生成 → 交给 summary.completed
        try:
            summary = await speakr.summary(rid)
        except Exception:
            return
        with db.connect(settings.db_path) as con:
            alerts = [dict(r) for r in db.alerts_for(con, rid)]
        block = writer.build_summary_block(alerts)
        await speakr.put_summary(rid, writer.upsert_summary(summary, block))

    elif event == "recording.summary.completed":
        with db.connect(settings.db_path) as con:
            scan = db.last_scan(con, rid)
            alerts = [dict(r) for r in db.alerts_for(con, rid)]
        if scan is None:
            # 转写完成与总结生成几乎同时触发，检测可能还没跑完 → 延迟重试
            raise RetryLater("detection not finished yet, will retry")
        summary = await speakr.summary(rid)
        block = writer.build_summary_block(alerts)
        await speakr.put_summary(rid, writer.upsert_summary(summary, block))

    else:
        raise ValueError(f"unknown event: {event}")


async def worker_loop(settings, speakr, lexicon):
    while True:
        try:
            with db.connect(settings.db_path) as con:
                jobs = db.due_jobs(con, settings.max_attempts)
            for job in jobs:
                try:
                    await process_job(settings, speakr, lexicon, job)
                    with db.connect(settings.db_path) as con:
                        db.job_done(con, job["id"])
                except RetryLater as e:
                    with db.connect(settings.db_path) as con:
                        db.job_failed(con, job["id"], job["attempts"] + 1, str(e),
                                      settings.summary_retry_seconds)
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    attempts = job["attempts"] + 1
                    with db.connect(settings.db_path) as con:
                        if attempts >= settings.max_attempts:
                            db.job_dead(con, job["id"], repr(e))
                        else:
                            db.job_failed(con, job["id"], attempts, repr(e),
                                          min(30 * (2 ** attempts), 600))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        await asyncio.sleep(settings.worker_interval)
