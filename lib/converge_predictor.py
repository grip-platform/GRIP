"""Re-export shim -- converge_predictor merged into converge_forecast.

All symbols are now in lib.converge_forecast. This shim maintains
backward compatibility for existing import sites.
"""
from converge_forecast import (
    PredictionResult,
    RunRecord,
    WEIGHTS,
    DEFAULT_BASE_RATES,
    DEFAULT_STRATEGY,
    METRICS_PATH,
    STRATEGY_STORE,
    load_history,
    _parse_runs,
    _compute_base_rates,
    _compute_avg_depths,
    _compute_avg_velocity,
    _compute_hour_success,
    _task_similarity_score,
    _get_strategy_recommendation,
    extract_features,
    predict_success,
    get_model_stats,
    format_prediction,
    format_stats,
    build_cli,
)

__all__ = [
    "PredictionResult",
    "RunRecord",
    "WEIGHTS",
    "DEFAULT_BASE_RATES",
    "DEFAULT_STRATEGY",
    "METRICS_PATH",
    "STRATEGY_STORE",
    "load_history",
    "_parse_runs",
    "_compute_base_rates",
    "_compute_avg_depths",
    "_compute_avg_velocity",
    "_compute_hour_success",
    "_task_similarity_score",
    "_get_strategy_recommendation",
    "extract_features",
    "predict_success",
    "get_model_stats",
    "format_prediction",
    "format_stats",
    "build_cli",
]
