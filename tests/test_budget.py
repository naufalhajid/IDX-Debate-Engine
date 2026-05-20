import asyncio

import core.budget as budget


def test_reset_budget_clears_counters_and_lock():
    asyncio.run(budget.check_and_increment_pro_budget())
    asyncio.run(budget.check_and_increment_flash_budget())

    assert budget.get_usage()["pro_calls"] == 1
    assert budget.get_usage()["flash_calls"] == 1
    assert budget._counter_lock is not None

    budget.reset_budget()

    assert budget.get_usage()["pro_calls"] == 0
    assert budget.get_usage()["flash_calls"] == 0
    assert budget._counter_lock is None
