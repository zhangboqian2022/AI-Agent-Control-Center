from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aacc.kimi_quota import QuotaStatus
from aacc.qwen_web_quota import parse_qwen_quota

PERSONAL_FIVE_HOUR_GROUND = (
    "5小时限额\n将于 2026-08-05 11:13:00 重置刷新\n3.02%已用\n0%\n50%\n90%\n100%"
)
PERSONAL_WEEKLY_GROUND = (
    "7天限额\n将于 2026-08-11 17:07:00 重置刷新\n1.38%已用\n0%\n50%\n90%\n100%\n额度补充"
)
TEAM_TOTAL_GROUND = "总额度\n重置时间 2026-08-07 10:00:00\n92.82%\n0%\n50%\n90%\n100%"


def _now() -> datetime:
    return datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


def _local_reset(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    # The console renders reset times in the local timezone; the parser must
    # keep them local instead of rebasing them on the fetch instant.
    return datetime(year, month, day, hour, minute).astimezone()


def test_parse_full_text_payload_ok() -> None:
    now = _now()
    quota = parse_qwen_quota(
        {
            "raw": {
                "fiveHourText": "5 小时\n已用 30%\n1 小时 23 分钟后重置",
                "weeklyText": "7 天\n已用 65%\n3 天 4 小时后重置",
            }
        },
        now=now,
    )
    assert quota.status is QuotaStatus.OK
    assert quota.five_hour is not None
    assert quota.five_hour.percentage == 30.0
    assert quota.five_hour.reset_at == now + timedelta(hours=1, minutes=23)
    assert quota.weekly is not None
    assert quota.weekly.percentage == 65.0
    assert quota.weekly.reset_at == now + timedelta(days=3, hours=4)


def test_parse_decimal_percentage_kept() -> None:
    quota = parse_qwen_quota(
        {"raw": {"fiveHourText": "5 小时\n0.04%\n2 小时后重置", "weeklyText": ""}},
        now=_now(),
    )
    assert quota.status is QuotaStatus.PARTIAL
    assert quota.five_hour is not None
    assert quota.five_hour.percentage == 0.04
    assert quota.five_hour.reset_at == _now() + timedelta(hours=2)
    assert quota.weekly is None


def test_parse_english_labels_and_mixed_decimals() -> None:
    quota = parse_qwen_quota(
        {"raw": {"fiveHourText": "5h\n12.5% used", "weeklyText": "7d\n99%"}},
        now=_now(),
    )
    assert quota.status is QuotaStatus.OK
    assert quota.five_hour is not None and quota.five_hour.percentage == 12.5
    assert quota.weekly is not None and quota.weekly.percentage == 99.0


def test_reset_not_polluted_by_window_label_line() -> None:
    quota = parse_qwen_quota(
        {"raw": {"fiveHourText": "5 小时\n已用 1%\n59 分钟后重置", "weeklyText": None}},
        now=_now(),
    )
    assert quota.five_hour is not None
    assert quota.five_hour.reset_at == _now() + timedelta(minutes=59)


def test_reset_prefers_lines_with_reset_marker() -> None:
    quota = parse_qwen_quota(
        {
            "raw": {
                "fiveHourText": "5 小时\n额度说明 每 5 小时一份\n已用 2%\n30 分钟后重置",
                "weeklyText": "7 天\n已用 10%\n6 天后重置",
            }
        },
        now=_now(),
    )
    assert quota.five_hour is not None
    assert quota.five_hour.reset_at == _now() + timedelta(minutes=30)
    assert quota.weekly is not None
    assert quota.weekly.reset_at == _now() + timedelta(days=6)


def test_window_without_percentage_is_absent() -> None:
    quota = parse_qwen_quota(
        {"raw": {"fiveHourText": "5 小时\n重置 1 小时", "weeklyText": "7 天\n80%"}},
        now=_now(),
    )
    assert quota.status is QuotaStatus.PARTIAL
    assert quota.five_hour is None
    assert quota.weekly is not None and quota.weekly.percentage == 80.0


def test_marketing_text_without_any_percentage_is_unknown() -> None:
    quota = parse_qwen_quota(
        {
            "raw": {
                "fiveHourText": "5 小时\n每 5 小时自动刷新一次",
                "weeklyText": "7 天\n每 7 天重置",
            }
        },
        now=_now(),
    )
    assert quota.status is QuotaStatus.UNKNOWN
    assert quota.five_hour is None and quota.weekly is None


def test_parse_unknown_when_no_window() -> None:
    quota = parse_qwen_quota({"unrelated": {}}, now=_now())
    assert quota.status is QuotaStatus.UNKNOWN
    assert quota.five_hour is None and quota.weekly is None


def test_parse_non_dict_payload_unknown() -> None:
    quota = parse_qwen_quota("not-a-dict", now=_now())
    assert quota.status is QuotaStatus.UNKNOWN


def test_parse_invalid_percentage_ignored() -> None:
    quota = parse_qwen_quota(
        {"raw": {"fiveHourText": "5 小时\n101%", "weeklyText": "7 天\nabc"}},
        now=_now(),
    )
    assert quota.status is QuotaStatus.UNKNOWN
    assert quota.five_hour is None and quota.weekly is None


def test_parse_accepts_seven_day_text_alias() -> None:
    quota = parse_qwen_quota(
        {"fiveHourText": "5h\n0%", "sevenDayText": "7d\n100%"},
        now=_now(),
    )
    assert quota.status is QuotaStatus.OK
    assert quota.five_hour is not None and quota.five_hour.percentage == 0.0
    assert quota.weekly is not None and quota.weekly.percentage == 100.0


def test_detail_used_remaining_derived_from_percentage() -> None:
    quota = parse_qwen_quota(
        {"raw": {"fiveHourText": "5 小时\n42.4%", "weeklyText": None}},
        now=_now(),
    )
    assert quota.five_hour is not None
    assert quota.five_hour.used == 42
    assert quota.five_hour.limit == 100
    assert quota.five_hour.remaining == 58
    assert quota.five_hour.reset_at is None


def test_parse_real_console_payload_with_team_plan() -> None:
    quota = parse_qwen_quota(
        {
            "raw": {
                "personalFiveHourText": PERSONAL_FIVE_HOUR_GROUND,
                "personalWeeklyText": PERSONAL_WEEKLY_GROUND,
                "teamTotalText": TEAM_TOTAL_GROUND,
            }
        },
        now=_now(),
    )
    assert quota.status is QuotaStatus.OK
    assert quota.five_hour is not None
    assert quota.five_hour.percentage == 3.02
    assert quota.five_hour.reset_at == _local_reset(2026, 8, 5, 11, 13)
    assert quota.weekly is not None
    assert quota.weekly.percentage == 1.38
    assert quota.weekly.reset_at == _local_reset(2026, 8, 11, 17, 7)
    assert quota.team_total is not None
    assert quota.team_total.percentage == 92.82
    assert quota.team_total.reset_at == _local_reset(2026, 8, 7, 10, 0)


def test_parse_team_only_payload_is_partial() -> None:
    quota = parse_qwen_quota(
        {
            "raw": {
                "personalFiveHourText": None,
                "personalWeeklyText": None,
                "teamTotalText": TEAM_TOTAL_GROUND,
            }
        },
        now=_now(),
    )
    assert quota.status is QuotaStatus.PARTIAL
    assert quota.five_hour is None
    assert quota.weekly is None
    assert quota.team_total is not None
    assert quota.team_total.percentage == 92.82


def test_parse_personal_only_payload_has_no_team_detail() -> None:
    quota = parse_qwen_quota(
        {
            "raw": {
                "personalFiveHourText": PERSONAL_FIVE_HOUR_GROUND,
                "personalWeeklyText": PERSONAL_WEEKLY_GROUND,
                "teamTotalText": None,
            }
        },
        now=_now(),
    )
    assert quota.status is QuotaStatus.OK
    assert quota.team_total is None


def test_percentage_ignores_noise_after_gauge_ticks() -> None:
    quota = parse_qwen_quota(
        {
            "raw": {
                "personalFiveHourText": (
                    "5小时限额\n将于 2026-08-05 11:13:00 重置刷新\n3.02%已用\n"
                    "0%\n50%\n90%\n100%\n额外用量包说明 20%"
                ),
                "personalWeeklyText": None,
                "teamTotalText": None,
            }
        },
        now=_now(),
    )
    assert quota.five_hour is not None
    assert quota.five_hour.percentage == 3.02


def test_loading_skeleton_without_values_is_unknown() -> None:
    quota = parse_qwen_quota(
        {
            "raw": {
                "personalFiveHourText": "5小时限额\n-\n0%\n50%\n90%\n100%",
                "personalWeeklyText": "7天限额\n-\n0%\n50%\n90%\n100%",
                "teamTotalText": None,
            }
        },
        now=_now(),
    )
    assert quota.status is QuotaStatus.UNKNOWN
    assert quota.five_hour is None
    assert quota.weekly is None


def test_team_total_defaults_to_none_for_legacy_payload() -> None:
    quota = parse_qwen_quota(
        {"raw": {"fiveHourText": "5 小时\n30%", "weeklyText": "7 天\n65%"}},
        now=_now(),
    )
    assert quota.status is QuotaStatus.OK
    assert quota.team_total is None


def test_absolute_reset_without_marker_line_is_still_parsed() -> None:
    quota = parse_qwen_quota(
        {
            "raw": {
                "personalFiveHourText": "5小时限额\n2026-08-05 11:13:00\n3.02%已用",
                "personalWeeklyText": None,
                "teamTotalText": None,
            }
        },
        now=_now(),
    )
    assert quota.five_hour is not None
    assert quota.five_hour.reset_at == _local_reset(2026, 8, 5, 11, 13)
