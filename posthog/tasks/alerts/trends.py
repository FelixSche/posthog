from typing import Optional, cast

from posthog.api.services.query import ExecutionMode
from posthog.caching.calculate_results import calculate_for_query_based_insight

from posthog.models import AlertConfiguration, Insight
from posthog.schema import (
    TrendsQuery,
    IntervalType,
    TrendsAlertConfig,
    InsightThreshold,
    AlertCondition,
    AlertConditionType,
    InsightsThresholdBounds,
    InsightThresholdType,
)
from posthog.caching.fetch_from_cache import InsightResult
from typing import TypedDict, NotRequired
from posthog.tasks.alerts.utils import (
    AlertEvaluationResult,
    NON_TIME_SERIES_DISPLAY_TYPES,
)


# TODO: move the TrendResult UI type to schema.ts and use that instead
class TrendResult(TypedDict):
    action: dict
    actions: list[dict]
    count: int
    data: list[float]
    days: list[str]
    dates: list[str]
    label: str
    labels: list[str]
    breakdown_value: str | int | list[str]
    aggregated_value: NotRequired[float]
    status: str | None
    compare_label: str | None
    compare: bool
    persons_urls: list[dict]
    persons: dict
    filter: dict


def check_trends_alert(alert: AlertConfiguration, insight: Insight, query: TrendsQuery) -> AlertEvaluationResult:
    if "type" in alert.config and alert.config["type"] == "TrendsAlertConfig":
        config = TrendsAlertConfig.model_validate(alert.config)
    else:
        ValueError(f"Unsupported alert config type: {alert.config}")

    condition = AlertCondition.model_validate(alert.condition)
    threshold = InsightThreshold.model_validate(alert.threshold.configuration) if alert.threshold else None

    if not threshold:
        return AlertEvaluationResult(value=0, breaches=[])

    has_breakdown = query.breakdownFilter and (
        (query.breakdownFilter.breakdown and query.breakdownFilter.breakdown_type) or query.breakdownFilter.breakdowns
    )

    is_non_time_series = _is_non_time_series_trend(query)

    match condition.type:
        case AlertConditionType.ABSOLUTE_VALUE:
            if threshold.type != InsightThresholdType.ABSOLUTE:
                raise ValueError(f"Absolute threshold not configured for alert condition ABSOLUTE_VALUE")

            if is_non_time_series:
                # for non time series, it's an aggregated value for full interval
                # so we need to compute full insight
                filters_override = None
            else:
                # want values back till previous interval (last hour, last day, last week, last month)
                # depending on the alert calculation interval
                filters_override = _date_range_override_for_intervals(query, last_x_intervals=2)

            calculation_result = calculate_for_query_based_insight(
                insight,
                team=alert.team,
                execution_mode=ExecutionMode.RECENT_CACHE_CALCULATE_BLOCKING_IF_STALE,
                user=None,
                filters_override=filters_override,
            )

            if not calculation_result.result:
                raise RuntimeError(f"No results found for insight with alert id = {alert.id}")

            interval = query.interval if not is_non_time_series else None

            if has_breakdown:
                # for breakdowns, we need to check all values in calculation_result.result
                breakdown_results = calculation_result.result

                for breakdown_result in breakdown_results:
                    # pick previous interval value
                    prev_interval_value = _pick_interval_value_from_trend_result(query, breakdown_result, -1)
                    breaches = _validate_bounds(
                        threshold.bounds,
                        prev_interval_value,
                        threshold.type,
                        condition.type,
                        interval,
                        breakdown_result["label"],
                    )

                    if breaches:
                        # found one breakdown value that breached the threshold
                        return AlertEvaluationResult(value=prev_interval_value, breaches=breaches)

                # None of the breakdown values breached the threshold
                return AlertEvaluationResult(value=None, breaches=[])
            else:
                # for non breakdowns, we pick the series (config.series_index) from calculation_result.result
                selected_series_result = _pick_series_result(config, calculation_result)

                prev_interval_value = _pick_interval_value_from_trend_result(query, selected_series_result, -1)
                breaches = _validate_bounds(
                    threshold.bounds,
                    prev_interval_value,
                    threshold.type,
                    condition.type,
                    interval,
                    selected_series_result["label"],
                )

                return AlertEvaluationResult(value=prev_interval_value, breaches=breaches)
        case AlertConditionType.RELATIVE_INCREASE:
            if is_non_time_series:
                raise ValueError(f"Relative alerts not supported for non time series trends")

            # to measure relative increase, we can't alert until current interval has completed
            # as to check increase less than X, we need interval to complete
            # so we need to compute the trend values for last 3 intervals
            # and then compare the previous interval with value for the interval before previous
            filters_overrides = _date_range_override_for_intervals(query, last_x_intervals=3)

            calculation_result = calculate_for_query_based_insight(
                insight,
                team=alert.team,
                execution_mode=ExecutionMode.RECENT_CACHE_CALCULATE_BLOCKING_IF_STALE,
                user=None,
                filters_override=filters_overrides,
            )

            results_to_evaluate = []

            if has_breakdown:
                # for breakdowns, we need to check all values in calculation_result.result
                breakdown_results = calculation_result.result
                results_to_evaluate.extend(breakdown_results)
            else:
                # for non breakdowns, we pick the series (config.series_index) from calculation_result.result
                selected_series_result = _pick_series_result(config, calculation_result)
                results_to_evaluate.append(selected_series_result)

            # if we don't have breakdown, we'll have to evaluate just one result
            # and increase will be the evaluated value of that result
            increase = None

            for result in results_to_evaluate:
                prev_interval_value = _pick_interval_value_from_trend_result(query, result, -1)
                prev_prev_interval_value = _pick_interval_value_from_trend_result(query, result, -2)

                if threshold.type == InsightThresholdType.ABSOLUTE:
                    increase = prev_interval_value - prev_prev_interval_value
                    breaches = _validate_bounds(
                        threshold.bounds,
                        increase,
                        threshold.type,
                        condition.type,
                        query.interval,
                        result["label"],
                    )
                elif threshold.type == InsightThresholdType.PERCENTAGE:
                    increase = (prev_interval_value - prev_prev_interval_value) / prev_prev_interval_value
                    breaches = _validate_bounds(
                        threshold.bounds,
                        increase,
                        threshold.type,
                        condition.type,
                        query.interval,
                        result["label"],
                    )
                else:
                    raise ValueError(
                        f"Neither relative nor absolute threshold configured for alert condition RELATIVE_INCREASE"
                    )

                if breaches:
                    # found a breach for one of the results so alert
                    return AlertEvaluationResult(value=increase, breaches=breaches)

            return AlertEvaluationResult(value=(increase if not has_breakdown else None), breaches=breaches)

        case AlertConditionType.RELATIVE_DECREASE:
            if is_non_time_series:
                raise ValueError(f"Relative alerts not supported for non time series trends")

            # to measure relative decrease, we can't alert until current interval has completed
            # as to check decrease more than X, we need interval to complete
            # so we need to compute the trend values for last 3 intervals
            # and then compare the previous interval with value for the interval before previous
            filters_overrides = _date_range_override_for_intervals(query, last_x_intervals=3)

            calculation_result = calculate_for_query_based_insight(
                insight,
                team=alert.team,
                execution_mode=ExecutionMode.RECENT_CACHE_CALCULATE_BLOCKING_IF_STALE,
                user=None,
                filters_override=filters_overrides,
            )

            results_to_evaluate = []

            if has_breakdown:
                # for breakdowns, we need to check all values in calculation_result.result
                breakdown_results = calculation_result.result
                results_to_evaluate.extend(breakdown_results)
            else:
                # for non breakdowns, we pick the series (config.series_index) from calculation_result.result
                selected_series_result = _pick_series_result(config, calculation_result)
                results_to_evaluate.append(selected_series_result)

                # for non breakdowns, we pick the series (config.series_index) from calculation_result.result
                selected_series_result = _pick_series_result(config, calculation_result)

            # if we don't have breakdown, we'll have to evaluate just one result
            # and increase will be the evaluated value of that result
            decrease = None

            for result in results_to_evaluate:
                prev_interval_value = _pick_interval_value_from_trend_result(query, result, -1)
                prev_prev_interval_value = _pick_interval_value_from_trend_result(query, result, -2)

                if threshold.type == InsightThresholdType.ABSOLUTE:
                    decrease = prev_prev_interval_value - prev_interval_value
                    breaches = _validate_bounds(
                        threshold.bounds,
                        decrease,
                        threshold.type,
                        condition.type,
                        query.interval,
                        result["label"],
                    )
                elif threshold.type == InsightThresholdType.PERCENTAGE:
                    decrease = (prev_prev_interval_value - prev_interval_value) / prev_prev_interval_value
                    breaches = _validate_bounds(
                        threshold.bounds,
                        decrease,
                        threshold.type,
                        condition.type,
                        query.interval,
                        result["label"],
                    )
                else:
                    raise ValueError(
                        f"Neither relative nor absolute threshold configured for alert condition RELATIVE_INCREASE"
                    )

                if breaches:
                    # found a breach for one of the results so alert
                    return AlertEvaluationResult(value=decrease, breaches=breaches)

            return AlertEvaluationResult(value=(decrease if not has_breakdown else None), breaches=breaches)

        case _:
            raise NotImplementedError(f"Unsupported alert condition type: {condition.type}")


def _is_non_time_series_trend(query: TrendsQuery) -> bool:
    return bool(query.trendsFilter and query.trendsFilter.display in NON_TIME_SERIES_DISPLAY_TYPES)


def _date_range_override_for_intervals(query: TrendsQuery, last_x_intervals: int = 1) -> Optional[dict]:
    """
    Resulting filter overrides don't set 'date_to' so we always get value for current interval.
    last_x_intervals controls how many intervals to look back to
    """
    assert last_x_intervals > 0

    match query.interval:
        case IntervalType.DAY:
            date_from = f"-{last_x_intervals}d"
        case IntervalType.WEEK:
            date_from = f"-{last_x_intervals}w"
        case IntervalType.MONTH:
            date_from = f"-{last_x_intervals}m"
        case _:
            date_from = f"-{last_x_intervals}h"

    return {"date_from": date_from}


def _pick_series_result(config: TrendsAlertConfig, results: InsightResult) -> TrendResult:
    series_index = config.series_index
    result = cast(list[TrendResult], results.result)[series_index]

    return result


def _pick_interval_value_from_trend_result(query: TrendsQuery, result: TrendResult, interval_to_pick: int = 0) -> float:
    """
    interval_to_pick to controls whether to pick value for current (0), last (-1), one before last (-2)...
    """
    assert interval_to_pick <= 0

    if _is_non_time_series_trend(query):
        # only one value in result
        return result["aggregated_value"]

    data = result["data"]
    # data is pre sorted in ascending order of timestamps
    index_from_back = len(data) - 1 + interval_to_pick
    return data[index_from_back]


def _validate_bounds(
    bounds: InsightsThresholdBounds | None,
    calculated_value: float,
    threshold_type: InsightThresholdType,
    condition_type: AlertConditionType,
    interval_type: IntervalType | None,
    series: str,
) -> list[str]:
    if not bounds:
        return []

    is_percentage = threshold_type == InsightThresholdType.PERCENTAGE

    formatted_value = f"{calculated_value:.2%}" if is_percentage else calculated_value

    match condition_type:
        case AlertConditionType.ABSOLUTE_VALUE:
            condition_text = "is"
        case AlertConditionType.RELATIVE_INCREASE:
            condition_text = "increased"
        case AlertConditionType.RELATIVE_DECREASE:
            condition_text = "decreased"

    if bounds.lower is not None and calculated_value < bounds.lower:
        lower_value = f"{bounds.lower:.2%}" if is_percentage else bounds.lower
        return [
            f"The insight value ({series}) for previous {interval_type or 'interval'} ({formatted_value}) {condition_text} less than lower threshold ({lower_value})"
        ]
    if bounds.upper is not None and calculated_value > bounds.upper:
        upper_value = f"{bounds.upper:.2%}" if is_percentage else bounds.upper
        return [
            f"The insight value ({series}) for previous {interval_type or 'interval'} ({formatted_value}) {condition_text} more than upper threshold ({upper_value})"
        ]

    return []
