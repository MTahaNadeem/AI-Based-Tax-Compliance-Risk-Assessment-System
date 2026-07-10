"""
Entity Resolution evaluation: pairwise precision / recall / F1 against the
synthetic ground truth. (With the official CSVs, the same harness runs against
a hand-labelled sample.)
"""
import itertools
from collections import defaultdict

def pairwise_metrics(entities, truth_df):
    # restrict the metric to sources actually present in this run (plug-ins
    # absent from a core-mode run must not inflate the recall denominator)
    loaded = {r["source"] for ent in entities for r in ent["records"]}
    truth = {(r["source"], str(r["record_id"])): r["person_id"]
             for _, r in truth_df.iterrows() if r["source"] in loaded}

    # predicted pairs — only over records present in ground truth (seller-role
    # records have no truth mapping and are excluded from the metric)
    pred_pairs = set()
    for ent in entities:
        keys = [(r["source"], r["record_id"]) for r in ent["records"]
                if (r["source"], r["record_id"]) in truth]
        for a, b in itertools.combinations(sorted(keys), 2):
            pred_pairs.add((a, b))

    # true pairs
    by_person = defaultdict(list)
    for k, pid in truth.items():
        by_person[pid].append(k)
    true_pairs = set()
    for pid, keys in by_person.items():
        for a, b in itertools.combinations(sorted(keys), 2):
            true_pairs.add((a, b))

    tp = len(pred_pairs & true_pairs)
    precision = tp / len(pred_pairs) if pred_pairs else 0.0
    recall = tp / len(true_pairs) if true_pairs else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return dict(precision=round(precision, 4), recall=round(recall, 4),
                f1=round(f1, 4), predicted_pairs=len(pred_pairs),
                true_pairs=len(true_pairs), true_positives=tp)
