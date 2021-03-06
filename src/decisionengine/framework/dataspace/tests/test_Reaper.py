import gc
import logging
import time

import mock
import pytest

import decisionengine.framework.dataspace.dataspace as dataspace
from decisionengine.framework.dataspace.maintain import Reaper
from decisionengine.framework.taskmanager.ProcessingState import State
from decisionengine.framework.dataspace.datasources.null import NullDataSource

logger = logging.getLogger()


@pytest.fixture
def config(request):

    return {
        "dataspace": {
            "retention_interval_in_days": 365,
            "datasource": {
                "module": "decisionengine.framework.dataspace.datasources.null",
                "name": "NullDataSource",
                "config": {
                    "key": "value",
                },
            },
        },
    }


@pytest.fixture
def reaper(request):
    config_fixture = request.getfixturevalue("config")
    reaper = Reaper(config_fixture)

    yield reaper

    try:
        if reaper.thread.is_alive() or not reaper.state.should_stop():
            reaper.state.set(State.OFFLINE)
            reaper.join(timeout=1)
    except Exception:
        pass

    del reaper
    gc.collect()


@pytest.mark.usefixtures("reaper")
def test_reap_default_state(reaper):
    assert reaper.state.get() == State.BOOT


@pytest.mark.usefixtures("reaper")
def test_reaper_can_reap(reaper):
    reaper.reap()


@pytest.mark.usefixtures("reaper")
def test_just_stop_no_error(reaper):
    reaper.stop()


@pytest.mark.usefixtures("reaper")
def test_start_stop(reaper):
    reaper.start()
    assert reaper.state.get() in (State.IDLE, State.ACTIVE, State.STEADY)

    reaper.stop()
    assert reaper.state.get() in (State.SHUTTINGDOWN, State.SHUTDOWN)


@pytest.mark.usefixtures("reaper")
def test_start_stop_stop(reaper):
    reaper.start()
    assert reaper.state.get() in (State.IDLE, State.ACTIVE, State.STEADY)

    reaper.stop()
    assert reaper.state.get() in (State.SHUTTINGDOWN, State.SHUTDOWN)

    logger.debug("running second stop")
    reaper.stop()
    assert reaper.state.get() in (State.SHUTTINGDOWN, State.SHUTDOWN)


@pytest.mark.usefixtures("reaper")
def test_state_can_be_active(reaper):
    def sleepnow(arg1=None, arg2=None):
        time.sleep(3)

    with mock.patch.object(NullDataSource, "delete_data_older_than", new=sleepnow):
        reaper.start()
        time.sleep(0.5)  # make sure reaper has a chance to get the lock
        assert reaper.state.get() == State.ACTIVE


@pytest.mark.timeout(20)
@pytest.mark.usefixtures("reaper")
def test_state_sets_timer_and_uses_it(reaper):
    def sleepnow(arg1=None, arg2=None):
        time.sleep(3)

    with mock.patch.object(NullDataSource, "delete_data_older_than", new=sleepnow):
        reaper.MIN_SECONDS_BETWEEN_RUNS = 1
        reaper.seconds_between_runs = 1
        reaper.start(delay=2)
        assert reaper.seconds_between_runs == 1
        reaper.state.wait_while(State.IDLE)  # Make sure the reaper started
        assert reaper.state.get() == State.ACTIVE
        reaper.state.wait_while(State.ACTIVE)  # let the reaper finish its scan
        reaper.state.wait_while(State.IDLE)  # Make sure the reaper started a second time
        reaper.state.wait_while(State.ACTIVE)  # let the reaper finish its scan


@pytest.mark.usefixtures("reaper")
def test_start_delay(reaper):
    reaper.start(delay=90)
    assert reaper.state.get() == State.IDLE


@pytest.mark.timeout(20)
@pytest.mark.usefixtures("reaper")
def test_loop_of_start_stop_in_clumps(reaper):
    for _ in range(3):
        logger.debug(f"run {_} of rapid start/stop")
        reaper.start()
        assert reaper.state.get() in (State.IDLE, State.ACTIVE, State.STEADY)
        reaper.stop()
        assert reaper.state.get() in (State.SHUTTINGDOWN, State.SHUTDOWN)


@pytest.mark.usefixtures("reaper")
def test_fail_small_retain(reaper):
    with pytest.raises(ValueError):
        reaper.retention_interval = 1


@pytest.mark.usefixtures("reaper")
def test_fail_small_run_interval(reaper):
    with pytest.raises(ValueError):
        reaper.seconds_between_runs = 1


@pytest.mark.usefixtures("reaper")
def test_fail_start_two_reapers(reaper):
    reaper.start()
    assert reaper.state.get() in (State.IDLE, State.ACTIVE, State.STEADY)
    with pytest.raises(RuntimeError):
        logger.debug("running second start")
        reaper.start()


@pytest.mark.usefixtures("reaper", "config")
def test_fail_missing_config(reaper, config):
    with pytest.raises(dataspace.DataSpaceConfigurationError):
        del config["dataspace"]
        Reaper(config)


@pytest.mark.usefixtures("reaper", "config")
def test_fail_bad_config(reaper, config):
    with pytest.raises(dataspace.DataSpaceConfigurationError):
        config["dataspace"] = "somestring"
        Reaper(config)


@pytest.mark.usefixtures("reaper", "config")
def test_fail_missing_config_key(reaper, config):
    with pytest.raises(dataspace.DataSpaceConfigurationError):
        del config["dataspace"]["retention_interval_in_days"]
        Reaper(config)


@pytest.mark.usefixtures("reaper", "config")
def test_fail_wrong_config_key(reaper, config):
    with pytest.raises(ValueError):
        config["dataspace"]["retention_interval_in_days"] = "abc"
        Reaper(config)


@pytest.mark.timeout(20)
@pytest.mark.usefixtures("reaper")
def test_source_fail_can_be_fixed(reaper):
    with mock.patch.object(NullDataSource, "delete_data_older_than") as function:
        function.side_effect = KeyError
        reaper.start()
        time.sleep(1)  # make sure stack trace bubbles up before checking state
        assert reaper.state.get() == State.ERROR

        reaper.stop()
        assert reaper.state.get() == State.ERROR

        function.side_effect = None
        reaper.start(delay=30)
        assert reaper.state.get() == State.IDLE

        reaper.stop()
        assert reaper.state.get() == State.SHUTDOWN
