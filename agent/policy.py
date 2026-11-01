"""Retrieval over the program's policy manual (the "R" in RAG).

Demo-sized on purpose: 12 short sections, so simple keyword scoring (BM25)
works well and needs no extra services. The interface (query + date in,
ranked sections out) stays the same if you swap in embeddings or hybrid search.

The one production idea built in from day one: policies change, so search
only returns the version in effect on the date the event happened.
"""
import json
import math
import os
import re
from collections import Counter

MANUAL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "policy_manual.json")
STOP = set("a an and are as at be by for from has have in is it its may must not of on or the to was were when with".split())


def _tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]


def load_manual(path=MANUAL_PATH):
    return json.load(open(path, encoding="utf-8"))


def in_effect(section, as_of):
    return section["effective_from"] <= as_of and (section["effective_to"] is None or as_of <= section["effective_to"])


class PolicyIndex:
    def __init__(self, sections=None, k1=1.5, b=0.75):
        self.sections = sections or load_manual()
        self.docs = [_tokens(s["title"] + " " + s["title"] + " " + s["text"]) for s in self.sections]
        self.avg_len = sum(len(d) for d in self.docs) / len(self.docs)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.k1, self.b = k1, b

    def search(self, query, as_of, k=3):
        """Top k sections in effect on as_of (YYYY-MM-DD), best first."""
        q = _tokens(query)
        scored = []
        for sec, doc in zip(self.sections, self.docs):
            if not in_effect(sec, as_of):
                continue
            tf = Counter(doc)
            score = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    score += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(doc) / self.avg_len))
            if score > 0:
                scored.append((score, sec))
        scored.sort(key=lambda x: -x[0])
        return [{"policy_id": f"policy:{s['id']}", "version": s["version"], "title": s["title"],
                 "effective_from": s["effective_from"], "effective_to": s["effective_to"], "text": s["text"]}
                for _, s in scored[:k]]
