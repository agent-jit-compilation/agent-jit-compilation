import warnings

import matplotlib
import numpy as np
from scipy import stats as st

matplotlib.use("Agg")


CANDIDATES = {
    "lognormal": st.lognorm,
    "weibull": st.weibull_min,
    "gamma": st.gamma,
    "loglogistic": st.fisk,  # fisk == log-logistic
}

# Force non-negative support
FIT_KW = {
    "lognormal": {"floc": 0.0},
    "weibull": {"floc": 0.0},
    "gamma": {"floc": 0.0},
    "loglogistic": {"floc": 0.0},
}


def _fit_one(dist_name, x):
    dist = CANDIDATES[dist_name]
    kw = FIT_KW.get(dist_name, {})
    # MLE fit; returns (shape(s), loc, scale)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        params = dist.fit(x, **kw)
    return params


def _ll(dist_name, params, x):
    dist = CANDIDATES[dist_name]
    return np.sum(dist.logpdf(x, *params))


def _bic(dist_name, params, x):
    # k = number of free params (shapes + loc + scale); but we set loc=0 via FIT_KW
    k = len(params)
    n = len(x)
    ll = _ll(dist_name, params, x)
    return k * np.log(n) - 2.0 * ll


def _ks(dist_name, params, x):
    dist = CANDIDATES[dist_name]
    # KS against fully-specified CDF; when params are estimated, p-value is only a heuristic
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat, pval = st.kstest(x, dist.cdf, args=params)
    return stat, pval


def _tail_quantile_error(dist_name, params, x, qs=(0.90, 0.95, 0.99)):
    dist = CANDIDATES[dist_name]
    emp = np.quantile(x, qs, method="linear")
    mod = dist.ppf(qs, *params)
    # avoid division by ~0 if empirical q is tiny
    ape = np.abs((mod - emp) / np.maximum(emp, 1e-12))
    # weight p95/p99 more heavily
    weights = np.array([1.0, 2.0, 3.0])[: len(qs)]
    wape = np.average(ape, weights=weights)
    return {
        "emp": emp,
        "mod": mod,
        "APE": ape,
        "WAPE": wape,
    }


def fit_and_score(x):
    """Return a list of dicts: {name, params, bic, ks_stat, ks_p, tail} sorted by (WAPE, BIC, KS)."""
    results = []
    # Drop non-positive values (latencies should be > 0). If zeros encode "skipped", handle separately.
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    x = x[x > 0]
    if len(x) < 3:
        return results

    for name in CANDIDATES:
        try:
            params = _fit_one(name, x)
            bic = _bic(name, params, x)
            ks_stat, ks_p = _ks(name, params, x)
            tail = _tail_quantile_error(name, params, x)
            results.append(
                {
                    "name": name,
                    "params": params,
                    "bic": bic,
                    "ks_stat": ks_stat,
                    "ks_p": ks_p,
                    "tail": tail,
                }
            )
        except Exception as e:
            results.append(
                {
                    "name": name,
                    "error": repr(e),
                }
            )
    # Sort: smaller tail error is better, then lower BIC, then smaller KS
    results = [r for r in results if "error" not in r]
    results.sort(key=lambda r: (r["tail"]["WAPE"], r["bic"], r["ks_stat"]))
    return results


def find_best_distribution(x: np.ndarray) -> dict:
    # Handle fewer than 3 values case
    if len(x) <= 3:
        return {
            "name": "constant",
            "params": [x[0]],
            "bic": 0,
            "ks_stat": 0,
            "ks_p": 1,
            "tail": {"emp": [x[0]], "mod": [x[0]], "APE": [0], "WAPE": 0},
        }

    # Try to fit distributions
    results = fit_and_score(x)

    # If no valid fits, fall back to empirical mean
    if not results:
        print("No valid fits, falling back to empirical mean")
        valid_x = np.asarray(x, float)
        valid_x = valid_x[np.isfinite(valid_x)]
        valid_x = valid_x[valid_x > 0]

        if len(valid_x) > 0:
            mean_val = float(np.mean(valid_x))
            return {
                "name": "constant",
                "params": [mean_val],
                "bic": 0,
                "ks_stat": 0,
                "ks_p": 1,
                "tail": {"emp": [mean_val], "mod": [mean_val], "APE": [0], "WAPE": 0},
            }
        else:
            return {
                "name": "constant",
                "params": [float(x[0])],
                "bic": 0,
                "ks_stat": 0,
                "ks_p": 1,
                "tail": {
                    "emp": [float(x[0])],
                    "mod": [float(x[0])],
                    "APE": [0],
                    "WAPE": 0,
                },
            }

    return results[0]
