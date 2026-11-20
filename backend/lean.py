"""Absolute lean check and the versioned Night Watch receipt.

A lean compares one revision's measured first-player and Cowboy shares with an
operator-configured target band. It is separate from the shift check in
balance.judge, which compares the candidate with the baseline.
"""

import hashlib
import math
import re

SCHEMA = "night-watch-receipt/1"
Z95 = 1.959964
METHOD = {
    "interval": "Wilson score 95%",
    "note": "Shares are over decisive games. Games are treated as independent, an approximation:"
            " in Showdown mode each deal is played twice, once with each faction moving first.",
}
NO_CARD_DATA = {"measured": False, "note": "The runner records faction and seat outcomes only. No per-card data is measured."}


def validate_band(value):
    """Return None or {"low", "high"} in percent. Raise ValueError with a readable message."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"low", "high"}:
        raise ValueError("The target band must be empty or have exactly a low and a high percentage.")
    low, high = value["low"], value["high"]
    for number in (low, high):
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError("The target band edges must be numbers of percent.")
    if not 0 <= low < high <= 100:
        raise ValueError("The target band needs 0 <= low < high <= 100.")
    return {"low": low, "high": high}


def wilson(wins, total):
    """Wilson 95% interval in percent, rounded to 2 places."""
    if total == 0:
        return None
    p = wins / total
    denom = 1 + Z95 ** 2 / total
    centre = (p + Z95 ** 2 / (2 * total)) / denom
    half = Z95 * math.sqrt(p * (1 - p) / total + Z95 ** 2 / (4 * total ** 2)) / denom
    return [round(100 * max(0.0, centre - half), 2), round(100 * min(1.0, centre + half), 2)]


def share(wins, decisive):
    return {
        "wins": wins,
        "decisive": decisive,
        "share": round(100 * wins / decisive, 2) if decisive else None,
        "ci95": wilson(wins, decisive),
    }


def measure(counts):
    decisive = counts["cowboy"] + counts["wizard"]
    games = counts.get("games", decisive + counts["draw"])
    return {
        "firstPlayer": share(counts["first"], decisive),
        "cowboy": share(counts["cowboy"], decisive),
        "drawShare": round(100 * counts["draw"] / games, 2) if games else None,
    }


def classify(metric, band, high_side, low_side):
    """within / lean / inconclusive / no-data for one measured share."""
    if metric["ci95"] is None:
        return {"verdict": "no-data", "direction": None}
    lo, hi = metric["ci95"]
    if lo > band["high"]:
        return {"verdict": "lean", "direction": high_side}
    if hi < band["low"]:
        return {"verdict": "lean", "direction": low_side}
    if lo >= band["low"] and hi <= band["high"]:
        return {"verdict": "within", "direction": None}
    return {"verdict": "inconclusive", "direction": None}


def lean_verdict(measured, band):
    if measured is None:
        return {"verdict": "not-measured", "firstPlayer": None, "faction": None}
    if band is None:
        return {"verdict": "unconfigured", "firstPlayer": None, "faction": None}
    first = classify(measured["firstPlayer"], band, "first", "second")
    faction = classify(measured["cowboy"], band, "cowboy", "wizard")
    verdicts = {first["verdict"], faction["verdict"]}
    overall = "lean" if "lean" in verdicts else "within" if verdicts == {"within"} else "inconclusive"
    return {"verdict": overall, "firstPlayer": first, "faction": faction}


def finding_key(*parts):
    return hashlib.sha256("|".join(str(p) for p in (SCHEMA,) + parts).encode("utf-8")).hexdigest()[:16]


def findings(receipt):
    """Real findings in a receipt: each lean metric, plus a shift alert. Keys are stable across retries."""
    out = []
    seeds, band, lean = receipt["seeds"], receipt["band"], receipt["lean"]
    seed_text = "%d-%d" % (seeds["from"], seeds["to"])
    for metric, label in (("firstPlayer", "first-player"), ("faction", "faction")):
        entry = lean.get(metric)
        if entry and entry["verdict"] == "lean":
            # The key leaves out the commit: one open issue tracks a lean that persists across commits.
            key = finding_key("lean", receipt["mode"], metric, entry["direction"], band["low"], band["high"], seed_text)
            out.append({
                "key": key, "kind": "lean", "metric": metric, "direction": entry["direction"],
                "title": "Night Watch: %s lean toward %s outside %g-%g%% band (%s seeds %s)" % (
                    label, entry["direction"], band["low"], band["high"], receipt["mode"], seed_text),
            })
    shift = receipt["shift"]
    if shift["state"] == "alert":
        key = finding_key("shift", receipt["mode"], receipt["baseline"]["commit"], receipt["candidate"]["commit"],
                          shift["threshold"], seed_text)
        out.append({
            "key": key, "kind": "shift", "metric": "shift", "direction": None,
            "title": "Night Watch: balance shift %s -> %s (%s seeds %s)" % (
                receipt["baseline"]["commit"][:10], receipt["candidate"]["commit"][:10], receipt["mode"], seed_text),
        })
    return out


def build_receipt(record, provenance):
    """The compact versioned receipt for one Night Watch record. provenance holds trusted local values."""
    base, cand = record["baseline"], record["candidate"]
    mode = record.get("mode", "lanes")
    per_deal = 2 if mode == "showdown" else 1
    band = record.get("band")
    measured = {side: (measure(entry["counts"]) if entry["counts"] else None)
                for side, entry in (("candidate", cand), ("baseline", base))}
    receipt = {
        "schema": SCHEMA,
        "generatedAt": record["at"],
        "trigger": record["source"],
        "mode": mode,
        "bot": provenance.get("bot"),
        "repository": provenance.get("repository"),
        "candidate": {"ref": cand["ref"], "commit": cand["commit"], "rulesBlob": provenance.get("candidateRules")},
        "baseline": {"ref": base["ref"], "commit": base["commit"], "rulesBlob": provenance.get("baselineRules")},
        "sameCommit": base["commit"] == cand["commit"],
        "seeds": {
            "from": record["firstSeed"], "to": record["firstSeed"] + record["count"] - 1,
            "deals": record["count"], "gamesPerDeal": per_deal, "games": record["count"] * per_deal,
        },
        "rulesTests": dict(record["rules"]),
        "samples": {"candidate": cand["counts"], "baseline": base["counts"]},
        "measured": measured,
        "band": dict(band, unit="percent of decisive games", source="operator-configured") if band else None,
        "lean": lean_verdict(measured["candidate"], band),
        "shift": {"state": record["state"], "threshold": record["threshold"], "message": record["message"]},
        "cards": dict(NO_CARD_DATA),
        "specChanges": list(record.get("changes", [])),
        "method": dict(METHOD),
        "runner": provenance.get("runner"),
        "commands": provenance.get("commands", []),
    }
    receipt["findings"] = findings(receipt) if record["state"] != "failed" else []
    return receipt


def recheck(receipt):
    """Recompute measurements, verdicts and findings from the raw counts. Raise ValueError when they disagree.

    Publication uses this so an edited or truncated receipt cannot produce an issue.
    """
    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA:
        raise ValueError("This is not a %s receipt." % SCHEMA)
    if receipt["mode"] not in ("showdown", "lanes"):
        raise ValueError("The receipt names an unknown mode.")
    for side in ("candidate", "baseline"):
        if not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", str(receipt[side]["commit"])):
            raise ValueError("The receipt's %s commit is not a full SHA." % side)
    seeds = receipt["seeds"]
    if any(isinstance(seeds[k], bool) or not isinstance(seeds[k], int) for k in ("from", "to", "deals", "gamesPerDeal", "games")) \
            or seeds["to"] - seeds["from"] + 1 != seeds["deals"] or seeds["games"] != seeds["deals"] * seeds["gamesPerDeal"]:
        raise ValueError("The receipt's seed range is inconsistent.")
    samples = receipt["samples"]["candidate"]
    if not samples:
        raise ValueError("The receipt has no candidate counts, so it has no findings.")
    games = receipt["seeds"]["games"]
    if samples.get("games", games) != games or samples["cowboy"] + samples["wizard"] + samples["draw"] != games:
        raise ValueError("The receipt's candidate counts do not add up to its %d games." % games)
    if samples["first"] + samples["second"] != samples["cowboy"] + samples["wizard"]:
        raise ValueError("The receipt's seat and faction wins disagree.")
    band = validate_band({k: receipt["band"][k] for k in ("low", "high")}) if receipt["band"] else None
    measured = measure(samples)
    if measured != receipt["measured"]["candidate"] or lean_verdict(measured, band) != receipt["lean"]:
        raise ValueError("The receipt's measured shares or verdicts do not match its counts.")
    expected = findings(receipt) if receipt["shift"]["state"] != "failed" else []
    if expected != receipt["findings"]:
        raise ValueError("The receipt's findings do not match its verdicts.")
    return expected
