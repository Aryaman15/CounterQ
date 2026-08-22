from app.worker.main import run


async def test_worker_startup_check_can_run() -> None:
    assert await run(check_once=True) == 0
