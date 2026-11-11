"""MoP fork (061 UV40): telemetry and the version check are removed at source.

`cad_server/analytics.py` `_environment` returns False and `_post` returns "refused"; `cad_server/updates.py`
`checking` returns False and `_get` returns None. The upstream tests below assert the old sending, so they are
deselected here instead of being edited (one MoP-owned side file, nothing to merge on an upstream rebase).
`test_mop_telemetry_off.py` holds what the fork asserts instead. An id that a release renames or drops is
simply not matched; a renamed sending test then fails again and has to be listed here.
"""

from __future__ import annotations

REASON = "MoP fork: telemetry removed at source, UV40"

# "<file>::<class>::<test>" of the upstream tests that expect a request to leave the process.
SENDING = frozenset({
    "test_engine_telemetry.py::EngineTelemetryTest::test_a_build_counts_as_the_daemon_counted_its_builds",
    "test_engine_telemetry.py::EngineTelemetryTest::test_a_command_starts_the_sender_once_the_spool_has_waited_a_window",
    "test_engine_telemetry.py::EngineTelemetryTest::test_a_refused_snapshot_is_counted_by_the_part_of_its_request_it_refused_never_the_value",
    "test_engine_telemetry.py::EngineTelemetryTest::test_nothing_is_counted_where_the_environment_or_the_asker_says_so",
    "test_engine_telemetry.py::EngineTelemetryTest::test_snapshot_sources_and_failures_keep_model_and_file_kinds_through_the_spool",
    "test_engine_telemetry.py::ThroughTheDaemonTest::test_a_command_is_counted_and_a_model_s_own_process_counts_nothing",
    "test_engine_telemetry.py::ThroughTheDaemonTest::test_a_telemetry_batch_holds_no_id_and_no_path",
    "test_telemetry_crashes.py::RecorderCrashTest::test_the_same_crash_is_sent_once_with_how_many_times_it_happened",
    "test_telemetry_crashes.py::ServerCrashTest::test_a_tool_that_breaks_is_answered_as_ever_and_counted_as_a_crash",
    "test_updates.py::UpdatesTest::test_a_store_s_copy_is_offered_the_release_its_plugin_moved_to_and_reads_the_feed_when_asked",
    "test_updates.py::UpdatesTest::test_an_update_reads_the_feed_now_and_moves_to_the_newest_it_knows",
    "test_updates.py::UpdatesTest::test_an_update_s_dry_run_reads_the_feed_now_and_keeps_nothing",
    "test_updates.py::UpdatesTest::test_nothing_waits_on_the_feed",
    "test_updates.py::UpdatesTest::test_the_feed_is_read_at_most_once_an_hour_and_kept_when_unreachable",
    "test_updates.py::UpdatesTest::test_the_newest_release_is_offered_until_the_install_has_it",
    "test_updates.py::UpdatesTest::test_the_notice_is_the_release_and_a_text_app_s_line_names_the_install_command",
})


def pytest_collection_modifyitems(config, items):
    kept, dropped = [], []
    for item in items:
        (dropped if item.nodeid.rsplit("/", 1)[-1] in SENDING else kept).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


def pytest_report_header(config):
    return f"deselected upstream sending tests: {REASON}"
